from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import typer

from . import __version__
from .auth import ensure_access_token
from .config_loader import (
    ConfigFile,
    derive_invite_path,
    derive_quota_path,
    load_config,
    resolve_config_path,
    write_default_config,
    write_invite_template,
    write_quota_template,
)
from .config_template import DEFAULT_CONFIG_FILENAME
from .core import (
    apply_quotas,
    ensure_access_permit,
    ensure_federation,
    ensure_federation_certificate,
    ensure_group,
    ensure_group_membership,
    ensure_invitation,
    ensure_project,
    get_project_by_name,
    list_group_memberships,
    list_invitations,
    list_tenant_users_with_attributes,
)
from .errors import ConfigError, NebiusAccError
from .invites import load_invite_file, parse_invite_file_spec
from .nebius_sdk import NebiusSdk
from .quota import (
    QuotaEntry,
    QuotaSpec,
    load_quota_file,
    parse_limit_value,
    parse_quota_file_spec,
)

Role = Literal["auditor", "viewer", "editor", "admin"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="""
[bold]Nebius Account Management CLI[/bold]

Manage tenant projects, project-level IAM groups, access permits, quotas, and invites.
Provide inputs via CLI flags or via YAML using the apply command.
""",
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@dataclass
class AppContext:
    config_file: Path | None
    nebius_profile: str | None
    nebius_config: Path | None
    logger: logging.Logger


@dataclass(frozen=True)
class ProjectSpec:
    name: str
    region_id: str


@app.callback()
def main(
    ctx: typer.Context,
    config_file: Path | None = typer.Option(
        None,
        "--config-file",
        help=f"Path to {DEFAULT_CONFIG_FILENAME} (used by apply/validate).",
    ),
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    nebius_profile: str | None = typer.Option(None, "--nebius-profile", help="Nebius SDK profile."),
    nebius_config: Path | None = typer.Option(
        None,
        "--nebius-config",
        help="Path to Nebius CLI config file (used by the SDK).",
    ),
    log_level: LogLevel = typer.Option("INFO", "--log-level", help="Logging verbosity."),
) -> None:
    logger = setup_logging(log_level)
    ctx.obj = AppContext(
        config_file=config_file,
        nebius_profile=nebius_profile,
        nebius_config=nebius_config,
        logger=logger,
    )


@app.command(
    "create-config",
    help="Create a default YAML configuration file (plus sample quota/invite files).",
)
def create_config(
    ctx: typer.Context,
    output: Path | None = typer.Argument(
        None,
        help=f"Output path (recommended: *.{DEFAULT_CONFIG_FILENAME.split('.', 1)[-1]}).",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite the config file if it exists."),
) -> None:
    """Write a template config file to disk."""
    app_ctx = ctx.obj
    path = output or resolve_config_path(app_ctx.config_file)
    try:
        write_default_config(path, force=force)
        app_ctx.logger.info("Wrote config template to %s", path)
    except ConfigError as exc:
        if force:
            raise
        app_ctx.logger.warning("%s (use --force to overwrite)", exc)

    quota_path = derive_quota_path(path)
    try:
        write_quota_template(quota_path, force=force)
        app_ctx.logger.info("Wrote sample quota file to %s", quota_path)
    except ConfigError as exc:
        if force:
            raise
        app_ctx.logger.warning("%s (use --force to overwrite)", exc)

    invite_path = derive_invite_path(path)
    try:
        write_invite_template(invite_path, force=force)
        app_ctx.logger.info("Wrote sample invite file to %s", invite_path)
    except ConfigError as exc:
        if force:
            raise
        app_ctx.logger.warning("%s (use --force to overwrite)", exc)


@app.command("validate", help="Validate config, quota, and invite files.")
def validate(
    ctx: typer.Context,
    config_file: Path | None = typer.Option(None, "--config-file", help="Config file path."),
    quota_file: Path | None = typer.Option(None, "--quota-file", help="Quota file path."),
    invite_file: Path | None = typer.Option(None, "--invite-file", help="Invite file path."),
) -> None:
    """Validate provided YAML/JSON files and exit with non-zero status on failure."""
    app_ctx = ctx.obj
    path = config_file or app_ctx.config_file
    if not path:
        raise ConfigError("config-file is required")
    load_config(path)
    app_ctx.logger.info("Config file %s is valid.", path)

    if quota_file:
        data = load_quota_file(quota_file)
        parse_quota_file_spec(data)
        app_ctx.logger.info("Quota file %s is valid.", quota_file)

    if invite_file:
        data = load_invite_file(invite_file)
        parse_invite_file_spec(data)
        app_ctx.logger.info("Invite file %s is valid.", invite_file)


@app.command(
    "apply",
    help="Apply a YAML config file and optionally quota/invite files in one run.",
)
def apply(
    ctx: typer.Context,
    config_file: Path | None = typer.Option(None, "--config-file", help="Config file path."),
    quota_file: Path | None = typer.Option(None, "--quota-file", help="Quota file path."),
    invite_file: Path | None = typer.Option(None, "--invite-file", help="Invite file path."),
) -> None:
    """Apply config-defined projects/groups, then optional quota and invite files."""
    app_ctx = ctx.obj
    sdk = build_sdk(app_ctx)

    config_path = config_file or app_ctx.config_file
    if not config_path:
        raise ConfigError("config-file is required")

    config = load_config(config_path)
    tenant_id_value = get_root_value(config, "tenant_id")
    group_name_value = get_root_value(config, "group_name")
    role_value = get_root_value(config, "role")

    if not tenant_id_value:
        raise ConfigError("tenant_id is required in config file")
    if not group_name_value:
        group_name_value = "grp-{project}"
    if "{project}" not in str(group_name_value):
        raise ConfigError("group_name must include '{project}'")
    if not role_value:
        raise ConfigError("role is required in config file")

    project_specs = load_project_specs(config)
    if not project_specs:
        raise ConfigError("projects are required in config file")

    if quota_file:
        quota_data = load_quota_file(quota_file)
        quota_spec = parse_quota_file_spec(quota_data)
        quota_tenant_id = quota_data.get("tenant_id")
        if quota_tenant_id and quota_tenant_id != str(tenant_id_value):
            raise ConfigError("quota-file tenant_id does not match config tenant_id")
    else:
        quota_spec = QuotaSpec({}, {})

    for project in project_specs:
        app_ctx.logger.info("Processing project %s", project.name)
        project_id, created, region = ensure_project(
            sdk,
            str(tenant_id_value),
            project.name,
            project.region_id,
        )
        if region and region != project.region_id:
            app_ctx.logger.warning(
                "Project %s exists in region %s (requested %s)",
                project.name,
                region,
                project.region_id,
            )
        group_label = str(group_name_value).format(project=project.name)
        group_id, _ = ensure_group(sdk, str(tenant_id_value), group_label)
        access_created = ensure_access_permit(sdk, group_id, project_id, str(role_value))

        if not quota_spec.is_empty():
            quotas = quota_spec.quotas_for_project(project.name, project.region_id)
            if quotas:
                apply_quotas(sdk, project_id, quotas, app_ctx.logger)

        app_ctx.logger.info(
            "Project %s: id=%s created=%s group=%s access-permit-created=%s",
            project.name,
            project_id,
            created,
            group_id,
            access_created,
        )

    if invite_file:
        invite_spec = parse_invite_file_spec(load_invite_file(invite_file))
        if invite_spec.tenant_id != str(tenant_id_value):
            raise ConfigError("invite-file tenant_id does not match config tenant_id")
        apply_invites(
            sdk,
            tenant_id=str(tenant_id_value),
            invite_map=invite_spec.invites,
            group_template=str(group_name_value),
            logger=app_ctx.logger,
        )

    apply_configure_sso(sdk, config, app_ctx.logger)


@app.command("create-projects", help="Create projects, groups, and access permits (CLI only).")
def create_projects(
    ctx: typer.Context,
    projects: str | None = typer.Argument(
        None,
        help="Comma-separated project names (positional).",
    ),
    projects_arg: str | None = typer.Option(None, "--projects", help="Comma-separated project names."),
    tenant_id: str | None = typer.Option(None, "--tenant-id", "--tenant_id", help="Tenant ID."),
    region_id: str | None = typer.Option(
        None,
        "--region-id",
        "--region_id",
        help="Region ID for all projects.",
    ),
    role: Role | None = typer.Option(
        None,
        "--role",
        "--perm",
        help="Role to assign to the project group.",
    ),
    group_name: str | None = typer.Option(
        None,
        "--group-name",
        help="Group naming template (must include {project}).",
    ),
) -> None:
    """Create projects, project groups, and access permits via CLI flags."""
    app_ctx = ctx.obj
    if app_ctx.config_file:
        raise ConfigError("Use apply --config-file for YAML-driven runs")
    sdk = build_sdk(app_ctx)

    projects_list = parse_projects(projects_arg, projects)

    if not tenant_id:
        raise ConfigError("--tenant-id is required")
    if not region_id:
        raise ConfigError("--region-id is required")
    if not role:
        raise ConfigError("--role is required")

    group_name_value = group_name or "grp-{project}"
    if "{project}" not in group_name_value:
        raise ConfigError("--group-name must include '{project}'")

    for project_name in projects_list:
        app_ctx.logger.info("Processing project %s", project_name)
        project_id, created, region = ensure_project(
            sdk,
            str(tenant_id),
            project_name,
            str(region_id),
        )
        if region and region != region_id:
            app_ctx.logger.warning(
                "Project %s exists in region %s (requested %s)",
                project_name,
                region,
                region_id,
            )
        group_label = group_name_value.format(project=project_name)
        group_id, _ = ensure_group(sdk, str(tenant_id), group_label)
        access_created = ensure_access_permit(sdk, group_id, project_id, str(role))

        app_ctx.logger.info(
            "Project %s: id=%s created=%s group=%s access-permit-created=%s",
            project_name,
            project_id,
            created,
            group_id,
            access_created,
        )


@app.command("set-quotas", help="Apply quotas to projects (CLI only).")
def set_quotas(
    ctx: typer.Context,
    tenant_id: str | None = typer.Option(None, "--tenant-id", "--tenant_id", help="Tenant ID."),
    projects: str | None = typer.Option(None, "--projects", help="Comma-separated project names."),
    region_id: str | None = typer.Option(None, "--region-id", "--region_id", help="Region ID."),
    quota: list[str] = typer.Option(
        None,
        "--quota",
        help="Quota entry as quota=limit (repeatable).",
    ),
) -> None:
    """Apply quotas using CLI flags only."""
    app_ctx = ctx.obj
    if app_ctx.config_file:
        raise ConfigError("Use apply --config-file for YAML-driven runs")
    sdk = build_sdk(app_ctx)

    if not tenant_id:
        raise ConfigError("--tenant-id is required")
    if not region_id:
        raise ConfigError("--region-id is required")

    projects_list = parse_projects(projects, None)
    quotas = parse_cli_quotas(quota, str(region_id))

    for project_name in projects_list:
        project = get_project_by_name(sdk, str(tenant_id), project_name)
        if not project:
            raise ConfigError(f"Project not found: {project_name}")
        project_id = project["id"]
        if not quotas:
            app_ctx.logger.info("No quotas defined for %s, skipping", project_name)
            continue
        apply_quotas(sdk, project_id, quotas, app_ctx.logger)


@app.command("configure-sso", help="Configure SSO federation (CLI only).")
def configure_sso(
    ctx: typer.Context,
    tenant_id: str | None = typer.Option(None, "--tenant-id", "--tenant_id", help="Tenant ID."),
    name: str | None = typer.Option(None, "--name", help="Federation name."),
    sso_url: str | None = typer.Option(None, "--sso-url", help="SAML SSO Login URL."),
    idp_issuer: str | None = typer.Option(None, "--idp-issuer", help="SAML IdP issuer identifier."),
    auto_create_users: bool | None = typer.Option(
        None,
        "--auto-create-users/--no-auto-create-users",
        help="Enable or disable auto-creation of user accounts.",
    ),
    active: bool | None = typer.Option(
        None,
        "--active/--inactive",
        help="Create a federation in active or inactive state.",
    ),
    force_authn: bool | None = typer.Option(
        None,
        "--force-authn/--no-force-authn",
        help="Force authentication at the IdP.",
    ),
    cert_file: Path | None = typer.Option(None, "--cert-file", help="Federation certificate file."),
    cert_description: str | None = typer.Option(None, "--cert-description", help="Certificate note."),
) -> None:
    """Configure SSO federation for a tenant via CLI flags."""
    app_ctx = ctx.obj
    if app_ctx.config_file:
        raise ConfigError("Use apply --config-file for YAML-driven runs")
    sdk = build_sdk(app_ctx)

    if not tenant_id:
        raise ConfigError("--tenant-id is required")
    if not name:
        raise ConfigError("--name is required")
    if not sso_url:
        raise ConfigError("--sso-url is required")
    if not idp_issuer:
        raise ConfigError("--idp-issuer is required")

    federation_id, created, updated = ensure_federation(
        sdk,
        tenant_id=str(tenant_id),
        name=str(name),
        sso_url=str(sso_url),
        idp_issuer=str(idp_issuer),
        auto_create=bool(auto_create_users) if auto_create_users is not None else True,
        active=bool(active) if active is not None else True,
        force_authn=bool(force_authn) if force_authn is not None else False,
    )
    if created:
        app_ctx.logger.info("Created federation %s", federation_id)
    elif updated:
        app_ctx.logger.info("Updated federation %s", federation_id)
    else:
        app_ctx.logger.info("Federation %s already up to date", federation_id)

    if cert_file:
        cert_data = read_certificate(cert_file)
        description = str(cert_description) if cert_description is not None else None
        created_cert = ensure_federation_certificate(
            sdk,
            federation_id,
            cert_data,
            description,
        )
        if created_cert:
            app_ctx.logger.info("Uploaded federation certificate for %s", federation_id)
        else:
            app_ctx.logger.info("Federation certificate already present for %s", federation_id)


def apply_configure_sso(sdk: NebiusSdk, config: ConfigFile, logger: logging.Logger) -> None:
    section = get_section(config, "configure_sso")
    enabled_value = section.get("enabled")
    if enabled_value is None:
        enabled_value = False
    if not enabled_value:
        logger.info("configure_sso is disabled (set enabled=true to run).")
        return

    tenant_id_value = section.get("tenant_id") or get_root_value(config, "tenant_id")
    name_value = section.get("name")
    sso_url_value = section.get("sso_url")
    idp_issuer_value = section.get("idp_issuer")
    auto_create_value = section.get("auto_create_users")
    active_value = section.get("active")
    force_authn_value = section.get("force_authn")
    cert_file_value = resolve_path(section.get("cert_file"), config)
    cert_description_value = section.get("cert_description")

    if not tenant_id_value:
        raise ConfigError("tenant_id is required (configure_sso)")
    if not name_value:
        raise ConfigError("configure_sso.name is required")
    if not sso_url_value:
        raise ConfigError("configure_sso.sso_url is required")
    if not idp_issuer_value:
        raise ConfigError("configure_sso.idp_issuer is required")

    federation_id, created, updated = ensure_federation(
        sdk,
        tenant_id=str(tenant_id_value),
        name=str(name_value),
        sso_url=str(sso_url_value),
        idp_issuer=str(idp_issuer_value),
        auto_create=bool(auto_create_value) if auto_create_value is not None else True,
        active=bool(active_value) if active_value is not None else True,
        force_authn=bool(force_authn_value) if force_authn_value is not None else False,
    )
    if created:
        logger.info("Created federation %s", federation_id)
    elif updated:
        logger.info("Updated federation %s", federation_id)
    else:
        logger.info("Federation %s already up to date", federation_id)

    if cert_file_value:
        cert_data = read_certificate(cert_file_value)
        description = str(cert_description_value) if cert_description_value is not None else None
        created_cert = ensure_federation_certificate(
            sdk,
            federation_id,
            cert_data,
            description,
        )
        if created_cert:
            logger.info("Uploaded federation certificate for %s", federation_id)
        else:
            logger.info("Federation certificate already present for %s", federation_id)


@app.command("invite-users", help="Invite users by email to project groups (CLI only).")
def invite_users(
    ctx: typer.Context,
    tenant_id: str | None = typer.Option(None, "--tenant-id", "--tenant_id", help="Tenant ID."),
    project: str | None = typer.Option(None, "--project", help="Project name."),
    emails: str | None = typer.Option(None, "--emails", help="Comma-separated emails."),
    group_name: str | None = typer.Option(
        None,
        "--group-name",
        help="Group naming template (must include {project}).",
    ),
) -> None:
    """Invite users to project groups via CLI only."""
    app_ctx = ctx.obj
    if app_ctx.config_file:
        raise ConfigError("Use apply --invite-file for YAML-driven runs")
    sdk = build_sdk(app_ctx)

    if not tenant_id:
        raise ConfigError("--tenant-id is required")
    if not project:
        raise ConfigError("--project is required")
    if not emails:
        raise ConfigError("--emails is required")
    tenant_id_value = tenant_id
    invite_map = {project: parse_emails(emails)}

    group_template = group_name or "grp-{project}"
    if "{project}" not in group_template:
        raise ConfigError("--group-name must include '{project}'")

    apply_invites(
        sdk,
        tenant_id=tenant_id_value,
        invite_map=invite_map,
        group_template=group_template,
        logger=app_ctx.logger,
    )


def build_sdk(app_ctx: AppContext) -> NebiusSdk:
    ensure_access_token(
        required=True,
        logger=app_ctx.logger,
        config_file=app_ctx.nebius_config,
        profile=app_ctx.nebius_profile,
    )
    return NebiusSdk.from_config(
        config_file=app_ctx.nebius_config,
        profile=app_ctx.nebius_profile,
    )


def parse_projects(
    projects_arg: str | None,
    positional_arg: str | None,
    allow_empty: bool = False,
) -> list[str]:
    if projects_arg and positional_arg:
        raise ConfigError("Provide project names either as positional or via --projects, not both")
    raw = projects_arg or positional_arg
    if not raw:
        if allow_empty:
            return []
        raise ConfigError("Project names are required")
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        if allow_empty:
            return []
        raise ConfigError("Project names are required")
    seen: set[str] = set()
    unique: list[str] = []
    for name in parts:
        if name not in seen:
            unique.append(name)
            seen.add(name)
    return unique


def parse_cli_quotas(raw: list[str], region_id: str) -> list[QuotaEntry]:
    if not raw:
        raise ConfigError("--quota is required")
    quotas: list[QuotaEntry] = []
    for item in raw:
        if not item or "=" not in item:
            raise ConfigError("Quota must be in the form quota=limit")
        name, limit_raw = item.split("=", 1)
        name = name.strip()
        limit_raw = limit_raw.strip()
        if not name:
            raise ConfigError("Quota name cannot be empty")
        if not limit_raw:
            raise ConfigError(f"Quota '{name}' requires a limit")
        limit = parse_limit_value(limit_raw)
        quotas.append(QuotaEntry(name=name, limit=limit, region=region_id))
    return quotas


def parse_emails(raw: str) -> list[str]:
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        raise ConfigError("Emails are required")
    return dedupe_emails(parts)


def dedupe_emails(emails: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for email in emails:
        normalized = email.strip().lower()
        if not normalized:
            continue
        if "@" not in normalized:
            raise ConfigError(f"Invalid email address: {email}")
        if normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)
    if not unique:
        raise ConfigError("Emails are required")
    return unique


def build_user_email_map(sdk: NebiusSdk, tenant_id: str) -> dict[str, str]:
    users = list_tenant_users_with_attributes(sdk, tenant_id)
    mapping: dict[str, str] = {}
    for item in users:
        attributes = getattr(item, "attributes", None)
        email = getattr(attributes, "email", None) if attributes else None
        if not email:
            continue
        account = getattr(item, "tenant_user_account", None)
        metadata = getattr(account, "metadata", None) if account else None
        account_id = getattr(metadata, "id", None) if metadata else None
        if not account_id:
            continue
        mapping[email.strip().lower()] = account_id
    return mapping


def apply_invites(
    sdk: NebiusSdk,
    *,
    tenant_id: str,
    invite_map: dict[str, list[str]],
    group_template: str,
    logger: logging.Logger,
) -> None:
    user_map = build_user_email_map(sdk, tenant_id)
    invitations = list_invitations(sdk, tenant_id)
    invite_by_email = {
        (inv.spec.email or "").strip().lower(): inv
        for inv in invitations
        if inv.spec and inv.spec.email
    }

    for project_name, email_list in invite_map.items():
        group_label = group_template.format(project=project_name)
        group_id, _ = ensure_group(sdk, tenant_id, group_label)
        membership_ids = {
            m.spec.member_id
            for m in list_group_memberships(sdk, group_id)
            if m.spec and m.spec.member_id
        }
        for email in dedupe_emails(email_list):
            member_id = user_map.get(email)
            invite = invite_by_email.get(email)
            if not member_id and invite and invite.status:
                member_id = invite.status.tenant_user_account_id or None

            if member_id and member_id in membership_ids:
                logger.info("%s already in %s; skipping", email, group_label)
                continue

            if not member_id:
                invite, created = ensure_invitation(sdk, tenant_id, email)
                if created:
                    logger.info("Invitation created for %s", email)
                else:
                    logger.info("Invitation already exists for %s", email)
                if invite and invite.status:
                    member_id = invite.status.tenant_user_account_id or None

            if not member_id:
                logger.info(
                    "Invitation sent to %s; will add to group after acceptance",
                    email,
                )
                continue

            created = ensure_group_membership(
                sdk,
                group_id,
                member_id,
                existing_member_ids=membership_ids,
            )
            if created:
                logger.info("Added %s to %s", email, group_label)
            else:
                logger.info("%s already in %s; skipping", email, group_label)


def load_project_specs(config: ConfigFile | None) -> list[ProjectSpec]:
    if not config:
        return []
    raw_projects = config.data.get("projects")
    if raw_projects is None:
        return []
    if not isinstance(raw_projects, dict):
        raise ConfigError("projects must be a mapping of region IDs to projects")
    specs: list[ProjectSpec] = []
    seen: set[str] = set()
    for region_id, region_projects in raw_projects.items():
        if not isinstance(region_id, str) or not region_id.strip():
            raise ConfigError("Region IDs must be non-empty strings")
        if not isinstance(region_projects, dict):
            raise ConfigError(f"Region {region_id} must map to projects")
        for project_name, project_cfg in region_projects.items():
            if not isinstance(project_name, str) or not project_name.strip():
                raise ConfigError("Project names must be non-empty strings")
            if project_name in seen:
                raise ConfigError(f"Duplicate project name in config: {project_name}")
            seen.add(project_name)
            if project_cfg not in (None, {}):
                if not isinstance(project_cfg, dict):
                    raise ConfigError(f"Project {project_name} must be a mapping or null")
                if project_cfg:
                    raise ConfigError(
                        f"Project {project_name} must not include settings in the config file"
                    )
            specs.append(ProjectSpec(name=project_name, region_id=region_id))
    return specs


def get_root_value(config: ConfigFile | None, key: str) -> object | None:
    if not config:
        return None
    return config.data.get(key)


def get_section(config: ConfigFile | None, key: str) -> dict[str, object]:
    if not config:
        return {}
    value = config.data.get(key)
    if isinstance(value, dict):
        return value
    return {}


def resolve_path(value: object | None, config: ConfigFile | None) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    if not isinstance(value, str):
        raise ConfigError("File path must be a string")
    path = Path(value)
    if path.is_absolute() or not config:
        return path
    return config.path.parent / path


def read_certificate(path: Path) -> str:
    if not path.exists():
        raise ConfigError(f"Certificate file not found: {path}")
    content = path.read_text().strip()
    if not content:
        raise ConfigError("Certificate file is empty")
    return content


def setup_logging(level: str) -> logging.Logger:
    from rich.logging import RichHandler

    handler = RichHandler(
        rich_tracebacks=False,
        markup=True,
        show_time=False,
        show_level=True,
        show_path=False,
    )
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(message)s",
        handlers=[handler],
    )
    return logging.getLogger("nebius-acc")


def main_entry() -> int:
    try:
        app()
    except NebiusAccError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        return 1
    return 0


if __name__ == "__main__":
    main_entry()
