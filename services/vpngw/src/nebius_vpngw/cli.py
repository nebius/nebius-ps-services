import os
import re
import sys
import time
import typing as t
from pathlib import Path

import typer
from rich import print

from . import __version__
from .config_loader import (
    ResolvedDeploymentPlan,
    load_local_config,
    merge_peer_configs_into_local_config,
    merge_with_peer_configs,
)
from .config_template import DEFAULT_CONFIG_TEMPLATE
from .deploy.route_manager import RouteManager
from .deploy.ssh_push import SSHPush
from .deploy.vm_manager import VMManager

DEFAULT_CONFIG_FILENAME = "nebius-vpngw.config.yaml"

app = typer.Typer(
    add_completion=False,
    help="""
Nebius VM-based VPN Gateway orchestrator

By default, commands look for 'nebius-vpngw.config.yaml' in your current directory.
Use --local-config-file with any command to specify a different config file.
""",
)


def _version_callback(value: bool) -> bool:
    if value:
        print(f"nebius-vpngw {__version__}")
        raise typer.Exit()
    return value


def _create_config_from_template(output_path: Path) -> None:
    """Write the embedded config template to user's directory."""
    try:
        output_path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    except Exception as e:
        print(f"[red]Failed to write config template:[/red] {e}")
        raise typer.Exit(code=1) from e


def _ensure_authentication(
    *,
    required: bool = True,
    timeout_seconds: int = 60,
    show_progress: bool = True,
) -> str | None:
    """Centralized authentication helper for all commands.

    Args:
        required: If True, exit with error if authentication fails. If False, continue with warning.
        timeout_seconds: Maximum time to wait for authentication (default: 60s)
        show_progress: If True, show authentication status messages

    Returns:
        Token string if successful, None if failed (only when required=False)

    Raises:
        typer.Exit: If authentication fails and required=True
    """
    # Skip if token already exists in environment
    if os.environ.get("NEBIUS_IAM_TOKEN"):
        return os.environ["NEBIUS_IAM_TOKEN"]

    try:
        from .vpngw_sa import ensure_cli_access_token

        if show_progress:
            print("[bold]Authenticating with Nebius...[/bold]")

        tok = ensure_cli_access_token(timeout_seconds=timeout_seconds)

        if tok:
            os.environ["NEBIUS_IAM_TOKEN"] = tok
            if show_progress:
                print("[green]✓ Authentication successful[/green]")
            return tok
        else:
            # Authentication failed or timed out
            if required:
                print("[red]✗ Authentication failed or timed out[/red]")
                print("[yellow]Please ensure you're logged in: nebius auth login[/yellow]")
                raise typer.Exit(code=1)
            else:
                if show_progress:
                    print("[yellow]⚠️  Authentication failed - continuing without token[/yellow]")
                return None

    except typer.Exit:
        raise
    except Exception as e:
        # Unexpected error during authentication
        if required:
            print(f"[red]✗ Authentication error: {e}[/red]")
            print("[yellow]Please ensure you're logged in: nebius auth login[/yellow]")
            raise typer.Exit(code=1) from e
        else:
            if show_progress:
                print(f"[yellow]⚠️  Authentication error: {e}[/yellow]")
            return None


def _resolve_local_config(
    local_config_file: Path | None,
    *,
    create_if_missing: bool,
    exit_after_create: bool,
) -> Path:
    """Resolve config path, optionally creating from embedded template and exiting."""
    if local_config_file is not None:
        return local_config_file

    default_path = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if default_path.exists():
        return default_path

    if not create_if_missing:
        print(f"[red]Error: Config file not found at {default_path}[/red]")
        print("[yellow]Run 'nebius-vpngw' first to create a template config.[/yellow]")
        raise typer.Exit(code=1)

    _create_config_from_template(default_path)
    print(f"[green]✓ Created config template at[/green] {default_path}")
    print()
    print("[bold]Next steps:[/bold]")
    print("  1. Edit the file to set your project context (tenant_id, project_id, region_id)")
    print("  2. Configure gateway VMs (instance_count, vm_spec, external_ips)")
    print("  3. Define connections and tunnels with peer details")
    print("  4. Set secrets via environment variables (e.g., export GCP_TUNNEL_1_PSK=...)")
    print("  5. Validate: [cyan]nebius-vpngw validate-config nebius-vpngw.config.yaml[/cyan]")
    print("  6. Deploy: [cyan]nebius-vpngw apply[/cyan]")
    print()

    if exit_after_create:
        raise typer.Exit(code=0)

    return default_path


@app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
):
    """Default action: creates config template if it doesn't exist."""
    if ctx.invoked_subcommand is None:
        default_path = Path.cwd() / DEFAULT_CONFIG_FILENAME
        if default_path.exists():
            # If default config already exists, show help for convenience.
            typer.echo(ctx.get_help())
            raise typer.Exit()
        # No command given - create config template if missing
        _resolve_local_config(
            None,
            create_if_missing=True,
            exit_after_create=True,
        )


@app.command()
def apply(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    recreate_gw: bool = typer.Option(False, help="Delete and recreate gateway VMs before applying"),
    sa: str | None = typer.Option(
        None,
        hidden=True,
        help="If provided, ensure a Service Account with this name and use it for auth",
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    zone: str | None = typer.Option(None, help="Nebius zone for gateway VMs"),
    dry_run: bool = typer.Option(False, hidden=True, help="Render actions without applying"),
):
    """Apply desired state to Nebius: create/update gateway VMs and push config."""
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=True,
        exit_after_create=True,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)

    print("[bold]Building deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

    print("[bold]Validating quotas and constraints...[/bold]")
    plan.validate()

    if dry_run:
        print("[yellow]Dry-run: showing summary of actions[/yellow]")
        print(plan.summary())
        # Skip VM ensure and SSH push in dry-run; just show summary.
        raise typer.Exit(code=0)

    # Resolve context from CLI args or config
    tenant_id = (local_cfg.get("tenant_id") or "").strip() or None
    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None
    region_id = (local_cfg.get("region_id") or "").strip() or None

    # Optional Service Account provisioning/auth
    auth_token = None
    if sa:
        print(f"[bold]Ensuring Service Account '{sa}' and obtaining token...[/bold]")
        try:
            # Defer import to keep optional dependency surface small
            from .vpngw_sa import ensure_service_account_and_token

            auth_token = ensure_service_account_and_token(
                sa_name=sa, tenant_id=tenant_id, project_id=proj_id, region_id=region_id
            )
            if auth_token:
                print("[green]Service Account token acquired.[/green]")
                # Make token available to SDKs expecting env var (PyPI SDK)
                os.environ["NEBIUS_IAM_TOKEN"] = auth_token
            else:
                print(
                    "[yellow]Service Account flow returned no token; falling back to CLI config.[/yellow]"
                )
        except Exception as e:
            print(f"[yellow]Service Account setup skipped due to error:[/yellow] {e}")
    else:
        # No SA requested; if NEBIUS_IAM_TOKEN is missing, try to read it from CLI config
        _ensure_authentication(required=False, show_progress=False)
        if os.environ.get("NEBIUS_IAM_TOKEN"):
            print("[green]Using IAM token from Nebius CLI (auto-fetched).[/green]")
        else:
            print(
                "[yellow]No IAM token found; SDK will use Nebius CLI profile if configured.[/yellow]"
            )

    vm_mgr = VMManager(
        project_id=proj_id,
        zone=zone or plan.gateway_group.region,
        auth_token=auth_token,
        tenant_id=tenant_id,
        region_id=region_id,
    )
    ssh = SSHPush()

    # Check for destructive changes BEFORE making any changes
    print("[bold]Analyzing configuration changes...[/bold]")
    changes = vm_mgr.check_changes(plan.gateway_group)

    has_destructive = False
    has_no_change = True

    for inst_name, diff in changes:
        if diff.requires_recreation():
            has_destructive = True
            has_no_change = False
            print(f"[red]{inst_name}:[/red]")
            print(diff.format_warning())
        elif diff.has_changes():
            has_no_change = False
            print(f"[yellow]{inst_name}:[/yellow]")
            print(diff.format_warning())
        else:
            print(f"[green]{inst_name}: No infrastructure changes[/green]")

    # If destructive changes detected and --recreate-gw not provided, abort
    if has_destructive and not recreate_gw:
        print("\n[red]⚠️  ERROR: Destructive changes require VM recreation[/red]")
        print("[yellow]To proceed with VM recreation, run:[/yellow]")
        print("  nebius-vpngw apply --recreate-gw")
        raise typer.Exit(code=1)

    # Warn if --recreate-gw provided but no changes detected (unnecessary recreation)
    if has_no_change and recreate_gw:
        print("\n[yellow]⚠️  WARNING: No configuration changes detected[/yellow]")
        print(
            "[yellow]VM recreation will use identical specifications (unnecessary downtime).[/yellow]"
        )
        print("\nDo you want to proceed? [y/N]: ", end="")
        import sys

        response = input().strip().lower()
        if response not in ("y", "yes"):
            print("[green]Aborted. No changes made.[/green]")
            raise typer.Exit(code=0)
        print("[yellow]Proceeding with VM recreation (user confirmed)...[/yellow]")
    elif has_destructive and recreate_gw:
        print("\n[yellow]⚠️  This will:[/yellow]")
        print("[yellow]  • Delete existing VM(s) and boot disk(s)[/yellow]")
        print("[yellow]  • Recreate VM(s) with new specifications[/yellow]")
        print("[yellow]  • Cause downtime for all VPN tunnels[/yellow]")
        print("[yellow]  • Preserve and reassign public IP allocations[/yellow]")
        print("")
        import sys

        sys.stdout.write("\033[1mProceed with VM recreation? [y/N]:\033[0m ")
        sys.stdout.flush()
        response = input().strip().lower()
        if response not in ("y", "yes"):
            print("[green]Aborted. No changes made.[/green]")
            raise typer.Exit(code=0)
        print("[yellow]Proceeding with destructive changes...[/yellow]")
    elif recreate_gw:
        print(
            "\n[yellow]Proceeding with VM recreation for safe changes (--recreate-gw flag provided)...[/yellow]"
        )

    # Determine appropriate action message based on whether VMs exist
    has_existing_vms = any(
        diff.change_type.value != "safe" or "does not exist" not in " ".join(diff.differences)
        for _, diff in changes
    )
    if has_existing_vms or recreate_gw:
        print("[bold]Updating gateway VMs...[/bold]")
    else:
        print("[bold]Creating gateway VMs...[/bold]")

    vm_ips = vm_mgr.ensure_group(
        plan.gateway_group,
        recreate=recreate_gw,
        local_prefixes=plan.gateway.get("local_prefixes"),
    )

    # Wait for VMs to be network-reachable and verify bootstrap
    if vm_ips:
        print("[bold]Waiting for VMs to become reachable...[/bold]")
        all_reachable = True
        for vm_name, vm_ip in vm_ips.items():
            if not vm_mgr.wait_for_vm_network(vm_name, vm_ip, timeout=180):
                all_reachable = False

        if all_reachable:
            print("[bold]Verifying VM bootstrap and package installation...[/bold]")
            all_healthy = True
            for vm_name, vm_ip in vm_ips.items():
                health = vm_mgr.check_vm_health(vm_name, vm_ip)
                if (
                    health["cloud_init_complete"]
                    and health["strongswan_installed"]
                    and health["frr_installed"]
                ):
                    print(f"[green]{vm_name} ({vm_ip}): {health['message']}[/green]")
                elif health["reachable"]:
                    print(f"[yellow]{vm_name} ({vm_ip}): {health['message']}[/yellow]")
                    all_healthy = False
                else:
                    print(f"[red]{vm_name} ({vm_ip}): {health['message']}[/red]")
                    all_healthy = False

            # If VMs are not fully healthy (cloud-init not complete or packages not installed), wait additional time
            if not all_healthy:
                import time

                print(
                    "[yellow]Waiting for cloud-init to complete and packages to be installed...[/yellow]"
                )
                max_wait = 300  # Wait up to 5 minutes for cloud-init
                wait_interval = 10
                for attempt in range(max_wait // wait_interval):
                    time.sleep(wait_interval)
                    all_ready = True
                    for vm_name, vm_ip in vm_ips.items():
                        health = vm_mgr.check_vm_health(vm_name, vm_ip)
                        # Check if VM is reachable AND cloud-init is complete
                        if not health["reachable"] or not health["cloud_init_complete"]:
                            all_ready = False
                            if not health["reachable"]:
                                print(f"[dim]{vm_name}: SSH not ready yet[/dim]")
                            elif not health["cloud_init_complete"]:
                                print(
                                    f"[dim]{vm_name}: Cloud-init still running (packages being installed)[/dim]"
                                )
                            break
                    if all_ready:
                        print(
                            f"[green]✓ All VMs ready: SSH accessible and cloud-init complete (waited {(attempt + 1) * wait_interval}s)[/green]"
                        )
                        break
                    print(
                        f"[dim]Waiting for bootstrap to complete... ({(attempt + 1) * wait_interval}s elapsed)[/dim]"
                    )
                else:
                    print(
                        "[yellow]Warning: Cloud-init did not complete within timeout, attempting config push anyway...[/yellow]"
                    )
        else:
            print("[yellow]Some VMs did not become reachable within timeout[/yellow]")

    print("[bold]Pushing per-VM resolved configs and reloading agent...[/bold]")
    for inst_cfg in plan.iter_instance_configs():
        # Use discovered IP from vm_ips first, then fall back to config
        target = vm_ips.get(inst_cfg.hostname) or (inst_cfg.external_ip or "").strip()
        if not target:
            # Last resort: try to query the VM
            discovered_ip = vm_mgr.get_vm_public_ip(inst_cfg.hostname)
            if discovered_ip:
                target = discovered_ip
            else:
                print(
                    f"[dim]Skipping config push for {inst_cfg.hostname}: No IP address available[/dim]"
                )
                continue
        ssh.push_config_and_reload(target, inst_cfg, local_cfg)

    print("[green]Apply completed successfully.[/green]")


@app.command(options_metavar="")
def validate_config(
    config_file: Path = typer.Argument(
        ..., exists=True, readable=True, help="Path to configuration file to validate"
    ),
):
    """Validate configuration file against schema without deploying.

    This command performs comprehensive validation including:
    - Schema compliance (correct structure, no unknown fields)
    - Type checking (strings, numbers, booleans, lists)
    - Field constraints (IP addresses, CIDRs, ASN ranges)
    - Logical consistency (BGP mode requires remote_asn, etc.)
    - Resource quotas (connections, tunnels within limits)

    Examples:
        nebius-vpngw validate-config my-config.yaml
        nebius-vpngw validate-config nebius-gcp-ha-vpngw.config.yaml
    """
    from rich.console import Console
    from rich.panel import Panel

    from .config_loader import load_local_config

    console = Console()

    try:
        console.print(f"[bold]Validating configuration: {config_file}[/bold]")

        # Load and validate (this will trigger schema validation)
        local_cfg = load_local_config(config_file)

        # Extract key metrics for summary
        connections_count = len(local_cfg.get("connections", []))
        tunnels_count = sum(len(c.get("tunnels", [])) for c in local_cfg.get("connections", []))
        instance_count = local_cfg.get("gateway_group", {}).get("instance_count", 1)

        # Success message with summary
        console.print()
        console.print(
            Panel.fit(
                f"[bold green]✓ Configuration is valid![/bold green]\n\n"
                f"[dim]Summary:[/dim]\n"
                f"  • Gateway instances: {instance_count}\n"
                f"  • Connections: {connections_count}\n"
                f"  • Tunnels: {tunnels_count}\n"
                f"  • Schema version: v{local_cfg.get('version', 1)}",
                title="[green]Validation Passed[/green]",
                border_style="green",
            )
        )
        console.print()
        console.print(
            "[dim]You can now run 'nebius-vpngw apply' to deploy this configuration.[/dim]"
        )

    except ValueError as e:
        # Schema validation errors or missing env vars
        console.print()
        console.print(
            Panel.fit(
                f"[bold red]✗ Configuration validation failed[/bold red]\n\n{str(e)}",
                title="[red]Validation Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e

    except Exception as e:
        # Unexpected errors
        console.print()
        console.print(
            Panel.fit(
                f"[bold red]✗ Unexpected error during validation[/bold red]\n\n{str(e)}",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e


@app.command(options_metavar="")
def create_config(
    config_file: Path = typer.Argument(
        ..., help="Path for new configuration file (recommended: *.config.yaml)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing file if it exists"),
):
    """Create a new configuration file from template.

    Generates a new YAML configuration file with comprehensive comments and examples.
    The template includes all available options for gateway setup, crypto settings,
    BGP/static routing, and tunnel configuration.

    Security best practice: Use *.config.yaml extension - these files are git-ignored
    automatically to prevent committing sensitive information (IPs, ASNs, secrets).

    Examples:
        nebius-vpngw create-config gcp-ha-vpn.config.yaml
        nebius-vpngw create-config aws-vpn.config.yaml
        nebius-vpngw create-config test.yaml  # Warning: not git-ignored
    """
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    # Check if file exists
    if config_file.exists() and not force:
        console.print()
        console.print(
            Panel.fit(
                f"[bold red]✗ File already exists[/bold red]\n\n"
                f"Path: {config_file}\n\n"
                f"Use --force to overwrite, or choose a different filename.",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    # Warn if not using .config.yaml extension
    if not str(config_file).endswith(".config.yaml"):
        console.print()
        console.print(
            Panel.fit(
                f"[bold yellow]⚠️  Security Warning[/bold yellow]\n\n"
                f"File: [cyan]{config_file}[/cyan]\n\n"
                f"The filename does not end with [bold].config.yaml[/bold]\n\n"
                f"Files matching [bold]*.config.yaml[/bold] are automatically git-ignored to prevent\n"
                f"committing sensitive information (public IPs, ASNs, PSKs).\n\n"
                f"[bold red]This file may be tracked by git and could expose secrets![/bold red]\n\n"
                f"[dim]Recommended: Use a .config.yaml extension (e.g., {config_file.stem}.config.yaml)[/dim]",
                title="[yellow]⚠️  Not Git-Ignored[/yellow]",
                border_style="yellow",
            )
        )

        # Ask for confirmation
        console.print()
        proceed = typer.confirm("Do you want to proceed anyway?", default=False)
        if not proceed:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)
        console.print()

    # Create the config file
    try:
        _create_config_from_template(config_file)

        console.print()
        console.print(
            Panel.fit(
                f"[bold green]✓ Configuration template created[/bold green]\n\n"
                f"File: [cyan]{config_file}[/cyan]\n\n"
                f"[dim]Next steps:[/dim]\n"
                f"  1. Edit file to set project context (tenant_id, project_id, region_id)\n"
                f"  2. Configure gateway VMs and networking\n"
                f"  3. Define connections and tunnels with peer details\n"
                f"  4. Set secrets via environment variables\n"
                f"  5. Validate: [cyan]nebius-vpngw validate-config {config_file}[/cyan]\n"
                f"  6. Deploy: [cyan]nebius-vpngw apply --local-config-file {config_file}[/cyan]",
                title="[green]Success[/green]",
                border_style="green",
            )
        )

        # Additional warning for non-.config.yaml files
        if not str(config_file).endswith(".config.yaml"):
            console.print()
            console.print(
                "[bold red]Remember: This file is NOT git-ignored. Do not commit secrets![/bold red]"
            )

    except Exception as e:
        console.print()
        console.print(
            Panel.fit(
                f"[bold red]✗ Failed to create configuration file[/bold red]\n\n{str(e)}",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e


@app.command(options_metavar="")
def create_from_peer_config(
    config_file: Path = typer.Argument(
        ..., help="Path for new configuration file (recommended: *.config.yaml)"
    ),
    peer_config_file: list[Path] = typer.Option(
        ..., exists=True, readable=True, help="Vendor peer config file(s)"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing file if it exists"),
):
    """Create a new configuration file by merging peer configs into the template.

    This generates a standalone YAML config file aligned with the schema for
    review and validation before deployment.
    """
    import yaml
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    if not peer_config_file:
        console.print(
            Panel.fit(
                "[bold red]✗ No peer config file provided[/bold red]\n\n"
                "Use --peer-config-file to specify at least one vendor config.",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    if config_file.exists() and not force:
        console.print()
        console.print(
            Panel.fit(
                f"[bold red]✗ File already exists[/bold red]\n\n"
                f"Path: {config_file}\n\n"
                f"Use --force to overwrite, or choose a different filename.",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    if not str(config_file).endswith(".config.yaml"):
        console.print()
        console.print(
            Panel.fit(
                f"[bold yellow]⚠️  Security Warning[/bold yellow]\n\n"
                f"File: [cyan]{config_file}[/cyan]\n\n"
                f"The filename does not end with [bold].config.yaml[/bold]\n\n"
                f"Files matching [bold]*.config.yaml[/bold] are automatically git-ignored to prevent\n"
                f"committing sensitive information (public IPs, ASNs, PSKs).\n\n"
                f"[bold red]This file may be tracked by git and could expose secrets![/bold red]\n\n"
                f"[dim]Recommended: Use a .config.yaml extension (e.g., {config_file.stem}.config.yaml)[/dim]",
                title="[yellow]⚠️  Not Git-Ignored[/yellow]",
                border_style="yellow",
            )
        )
        console.print()
        proceed = typer.confirm("Do you want to proceed anyway?", default=False)
        if not proceed:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)
        console.print()

    try:
        base_cfg = yaml.safe_load(DEFAULT_CONFIG_TEMPLATE) or {}
        merged_cfg = merge_peer_configs_into_local_config(
            base_cfg, peer_config_file, prefer_peer=True
        )

        if merged_cfg == base_cfg:
            console.print(
                "[yellow]⚠️  Peer config did not change the template. "
                "Review the file and fill in any missing fields manually.[/yellow]"
            )

        config_file.write_text(yaml.safe_dump(merged_cfg, sort_keys=False), encoding="utf-8")

        console.print()
        console.print(
            Panel.fit(
                f"[bold green]✓ Configuration created from peer config[/bold green]\n\n"
                f"File: [cyan]{config_file}[/cyan]\n\n"
                f"[dim]Next steps:[/dim]\n"
                f"  1. Review and replace any placeholders (tenant/project/region/PSKs)\n"
                f"  2. Validate: [cyan]nebius-vpngw validate-config {config_file}[/cyan]\n"
                f"  3. Deploy: [cyan]nebius-vpngw apply --local-config-file {config_file}[/cyan]",
                title="[green]Success[/green]",
                border_style="green",
            )
        )

        if not str(config_file).endswith(".config.yaml"):
            console.print()
            console.print(
                "[bold red]Remember: This file is NOT git-ignored. Do not commit secrets![/bold red]"
            )

    except Exception as e:
        console.print()
        console.print(
            Panel.fit(
                f"[bold red]✗ Failed to create configuration file[/bold red]\n\n{str(e)}",
                title="[red]Error[/red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e


@app.command()
def status(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    zone: str | None = typer.Option(None, help="Nebius zone for gateway VMs"),
):
    """Show status of VPN tunnels and gateway health."""
    import json
    import subprocess

    from rich.console import Console
    from rich.table import Table

    console = Console()

    # Use default config if not provided (do not auto-create for status)
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

    # Resolve context from CLI args or config
    tenant_id = (local_cfg.get("tenant_id") or "").strip() or None
    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None
    region_id = (local_cfg.get("region_id") or "").strip() or None

    # Get token for API access
    auth_token = _ensure_authentication(required=False, show_progress=True)

    vm_mgr = VMManager(
        project_id=proj_id,
        zone=zone or plan.gateway_group.region,
        auth_token=auth_token,
        tenant_id=tenant_id,
        region_id=region_id,
    )

    # Quick check: verify at least one gateway VM exists before attempting SSH
    print("[bold]Checking for gateway VMs...[/bold]")
    from nebius.api.nebius.compute.v1 import (  # type: ignore
        InstanceServiceClient,
        ListInstancesRequest,
    )

    client = vm_mgr._get_client()
    if client and proj_id:
        isc = InstanceServiceClient(client)
        ilist_op = isc.list(ListInstancesRequest(parent_id=proj_id))
        ilist = ilist_op.wait() if hasattr(ilist_op, "wait") else ilist_op

        items = []
        if hasattr(ilist, "items"):
            items = ilist.items
        elif hasattr(ilist, "__iter__"):
            items = list(ilist)

        existing_vms = [
            inst
            for inst in items
            if getattr(getattr(inst, "metadata", None), "name", "").startswith(
                f"{plan.gateway_group.name}-"
            )
        ]

        if not existing_vms:
            console.print(
                f"[yellow]No gateway VMs found matching pattern '{plan.gateway_group.name}-*'[/yellow]"
            )
            console.print("[yellow]Run 'nebius-vpngw apply' to create gateway VMs first.[/yellow]")
            raise typer.Exit(0)

    print("[bold]Collecting gateway VM status...[/bold]")
    vm_ips = {}
    for inst_cfg in plan.iter_instance_configs():
        ip = vm_mgr.get_vm_public_ip(inst_cfg.hostname) or (inst_cfg.external_ip or "").strip()
        if ip:
            vm_ips[inst_cfg.hostname] = ip
        else:
            print(
                f"[yellow]Warning: Could not find IP for {inst_cfg.hostname}. "
                "Ensure project_id is correct and/or set gateway_group.external_ips if discovery is blocked.[/yellow]"
            )

    # Create status table
    table = Table(title="VPN Gateway Status", show_header=True, header_style="bold cyan")
    table.add_column("Tunnel", style="white")
    table.add_column("Role", style="white")
    table.add_column("Carrying Traffic", style="white")
    table.add_column("Gateway VM", style="white")
    table.add_column("Status", style="white")
    table.add_column("BGP", style="white")
    table.add_column("Peer IP", style="white")
    table.add_column("Encryption", style="white")
    table.add_column("Uptime", style="white")

    # Build mapping of tunnel -> BGP peer IP, remote public IP, and ha_role per instance
    tunnel_bgp_map: dict[str, dict[str, str]] = {}
    tunnel_peer_map: dict[str, dict[str, str]] = {}
    tunnel_role_map: dict[str, dict[str, str]] = {}

    def _normalize_mode(value: t.Any) -> str:
        if hasattr(value, "value"):
            value = value.value
        value = str(value or "").strip().lower()
        return value or "bgp"

    def _normalize_role(value: t.Any) -> str:
        if hasattr(value, "value"):
            value = value.value
        value = str(value or "").strip().lower()
        return value or "unknown"

    defaults_mode = _normalize_mode(
        (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode")
    )
    for conn in local_cfg.get("connections") or []:
        conn_mode = _normalize_mode(conn.get("routing_mode") or defaults_mode)
        for tun in conn.get("tunnels") or []:
            try:
                inst_idx = int(tun.get("gateway_instance_index", 0))
            except Exception:
                inst_idx = 0
            hostname = f"{plan.gateway_group.name}-{inst_idx}"
            tunnel_bgp_map.setdefault(hostname, {})
            tunnel_peer_map.setdefault(hostname, {})
            tunnel_role_map.setdefault(hostname, {})
            if conn_mode == "bgp":
                peer_ip = tun.get("inner_remote_ip")
                if peer_ip:
                    tunnel_bgp_map[hostname][tun.get("name") or f"tunnel{inst_idx}"] = str(
                        peer_ip
                    )
            remote_public_ip = tun.get("remote_public_ip")
            if remote_public_ip:
                tunnel_peer_map[hostname][tun.get("name") or f"tunnel{inst_idx}"] = str(
                    remote_public_ip
                )
            ha_role = _normalize_role(tun.get("ha_role") or "active")
            tunnel_role_map[hostname][tun.get("name") or f"tunnel{inst_idx}"] = ha_role

    def format_role(role: str | None) -> str:
        role_value = role or "-"
        if hasattr(role_value, "value"):
            role_value = role_value.value  # type: ignore[assignment]
        role_value = str(role_value).lower()
        if role_value == "active":
            return "[green]active[/green]"
        if role_value == "passive":
            return "[yellow]passive[/yellow]"
        if role_value == "disable":
            return "[red]disabled[/red]"
        return role_value

    def format_bgp_status(bgp_status: str | None) -> str:
        if not bgp_status or bgp_status == "-":
            return "-"
        state = str(bgp_status).strip()
        state_lower = state.lower()
        if state_lower == "established":
            return "[green]Established[/green]"
        if "admin" in state_lower:
            return "[red]Down (Admin)[/red]"
        if state_lower.startswith("idle") or state_lower in ("connect", "active"):
            label = state.split()[0].capitalize()
            return f"[red]Down ({label})[/red]"
        return f"[red]{state}[/red]"

    def select_carrying_tunnel(
        hostname: str,
        tunnel_names: list[str],
        tunnel_statuses: dict[str, str],
        bgp_states: dict[str, str],
    ) -> str | None:
        established: list[str] = []
        if bgp_states:
            for name in tunnel_names:
                peer_ip = tunnel_bgp_map.get(hostname, {}).get(name)
                if not peer_ip:
                    continue
                state = str(bgp_states.get(peer_ip, "")).strip().lower()
                if state == "established":
                    established.append(name)
        if not established:
            for name in tunnel_names:
                if str(tunnel_statuses.get(name, "")).upper() == "ESTABLISHED":
                    established.append(name)
        if len(established) == 1:
            return established[0]
        if len(established) > 1:
            for name in established:
                role_value = _normalize_role(tunnel_role_map.get(hostname, {}).get(name))
                if role_value == "active":
                    return name
            return established[0]
        return None

    def format_carrying(tunnel_name: str, carrying_tunnel: str | None) -> str:
        if not carrying_tunnel:
            return "-"
        if tunnel_name == carrying_tunnel:
            return "[green]yes[/green]"
        return "[dim]no[/dim]"

    def _uptime_seconds(text: str) -> int | None:
        value_text = text.strip().lower()
        if value_text.endswith("ago"):
            value_text = value_text[:-3].strip()

        short_match = re.match(r"(\d+)\s*([smhd])$", value_text)
        if short_match:
            value = int(short_match.group(1))
            unit = short_match.group(2)
            multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
            return value * multiplier

        word_match = re.match(r"(\d+)\s+(second|minute|hour|day)s?$", value_text)
        if word_match:
            value = int(word_match.group(1))
            unit = word_match.group(2)
            multiplier = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}[unit]
            return value * multiplier

        return None

    def _format_uptime(seconds: int) -> str:
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{days}:{hours:02d}:{minutes:02d}:{secs:02d}"

    def parse_strongswan_uptime(uptime_str: str) -> str:
        """Parse strongSwan uptime and return d:h:m:s."""
        seconds = _uptime_seconds(uptime_str)
        if seconds is None:
            return uptime_str.strip()
        return _format_uptime(seconds)

    # Check each gateway VM's tunnels
    for inst_cfg in plan.iter_instance_configs():
        target = vm_ips.get(inst_cfg.hostname)
        if not target:
            continue

        # Pull BGP neighbor states (if any BGP tunnels on this instance)
        bgp_states: dict[str, str] = {}
        if tunnel_bgp_map.get(inst_cfg.hostname):
            try:
                # Try JSON output first
                bgp_out = subprocess.run(
                    [
                        "ssh",
                        "-o",
                        "StrictHostKeyChecking=no",
                        "-o",
                        "ConnectTimeout=10",
                        f"ubuntu@{target}",
                        "sudo vtysh -c 'show bgp ipv4 unicast summary json'",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if bgp_out.returncode == 0 and bgp_out.stdout:
                    try:
                        data = json.loads(bgp_out.stdout)
                        # Try multiple possible JSON structures from different FRR versions
                        peers = (data.get("ipv4Unicast") or {}).get("peers") or {}
                        if not peers:
                            # Try alternative structure
                            peers = data.get("peers") or {}
                        for ip, info in peers.items():
                            # Try different possible field names for state
                            state = (
                                info.get("state")
                                or info.get("state_name")
                                or info.get("stateName")
                                or info.get("peerState")
                                or info.get("bgpState")
                            )
                            if state:
                                bgp_states[ip] = state
                    except json.JSONDecodeError:
                        pass

                # If JSON parsing didn't work, fall back to text parsing
                if not bgp_states:
                    bgp_out = subprocess.run(
                        [
                            "ssh",
                            "-o",
                            "StrictHostKeyChecking=no",
                            "-o",
                            "ConnectTimeout=10",
                            f"ubuntu@{target}",
                            "sudo vtysh -c 'show bgp summary'",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if bgp_out.returncode == 0 and bgp_out.stdout:
                        # Parse text output: look for neighbor lines
                        # Example: "169.254.5.153    4 65014      123      456       0    0 01:23:45 Established"
                        for line in bgp_out.stdout.splitlines():
                            parts = line.split()
                            # Look for lines starting with an IP address
                            if len(parts) >= 2 and parts[0] and "." in parts[0]:
                                try:
                                    # Validate it's an IP
                                    octets = parts[0].split(".")
                                    if len(octets) == 4 and all(
                                        o.isdigit() and 0 <= int(o) <= 255 for o in octets
                                    ):
                                        # Last column is typically the state or prefix count
                                        state = parts[-1]
                                        if state.isdigit():
                                            state = "Established"
                                        bgp_states[parts[0]] = state
                                except (ValueError, IndexError):
                                    continue
            except Exception:
                pass

        # Run swanctl status command (preferred for VICI-based configs)
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=10",
                    f"ubuntu@{target}",
                    "sudo swanctl --list-sas",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )

            output = result.stdout if result.returncode == 0 else ""
            if output:
                import re

                tunnel_statuses: dict[str, str] = {}
                tunnel_uptime: dict[str, str] = {}
                tunnel_uptime_seconds: dict[str, int] = {}
                tunnel_encryption: dict[str, list[str]] = {}
                tunnel_ike_encryption: dict[str, list[str]] = {}
                tunnel_order: list[str] = []
                current_tunnel: str | None = None
                header_pattern = re.compile(r"^(\S+?):\s+#\d+,", re.IGNORECASE)
                status_pattern = re.compile(r"\b(ESTABLISHED|CONNECTING)\b", re.IGNORECASE)
                uptime_pattern = re.compile(r"established\s+(\S+)\s+ago", re.IGNORECASE)
                esp_pattern = re.compile(r"\bESP:([^,]+)", re.IGNORECASE)
                for line in output.splitlines():
                    header_match = header_pattern.match(line)
                    if header_match:
                        name = header_match.group(1)
                        status_match = status_pattern.search(line)
                        status = status_match.group(1).upper() if status_match else "CONNECTING"
                        if name not in tunnel_statuses:
                            tunnel_statuses[name] = status
                            tunnel_order.append(name)
                        elif tunnel_statuses[name] != "ESTABLISHED" and status == "ESTABLISHED":
                            tunnel_statuses[name] = status
                        current_tunnel = name
                        continue

                    if not current_tunnel:
                        continue

                    uptime_match = uptime_pattern.search(line)
                    if uptime_match:
                        uptime_token = uptime_match.group(1)
                        uptime_display = parse_strongswan_uptime(uptime_token)
                        uptime_seconds = _uptime_seconds(uptime_token)
                        if uptime_seconds is None:
                            if current_tunnel not in tunnel_uptime:
                                tunnel_uptime[current_tunnel] = uptime_display
                        else:
                            prev = tunnel_uptime_seconds.get(current_tunnel)
                            if prev is None or uptime_seconds < prev:
                                tunnel_uptime_seconds[current_tunnel] = uptime_seconds
                                tunnel_uptime[current_tunnel] = uptime_display

                    esp_match = esp_pattern.search(line)
                    if esp_match:
                        algo = esp_match.group(1).strip()
                        if algo:
                            algos = tunnel_encryption.setdefault(current_tunnel, [])
                            if algo not in algos:
                                algos.append(algo)

                    if line.startswith("  "):
                        algo_line = line.strip()
                        if "PRF_" in algo_line and "MODP_" in algo_line and "/" in algo_line:
                            ike_algos = tunnel_ike_encryption.setdefault(current_tunnel, [])
                            if algo_line not in ike_algos:
                                ike_algos.append(algo_line)

                if tunnel_statuses:
                    carrying_tunnel = select_carrying_tunnel(
                        inst_cfg.hostname,
                        tunnel_order,
                        tunnel_statuses,
                        bgp_states,
                    )
                    for tunnel_name in tunnel_order:
                        status_text = tunnel_statuses[tunnel_name]
                        if status_text == "ESTABLISHED":
                            status_display = "[green]Established[/green]"
                        elif status_text == "CONNECTING":
                            status_display = "[yellow]Connecting[/yellow]"
                        else:
                            status_display = f"[red]{status_text.capitalize()}[/red]"

                        peer_cfg_ip = tunnel_bgp_map.get(inst_cfg.hostname, {}).get(tunnel_name)
                        if peer_cfg_ip and peer_cfg_ip in bgp_states:
                            bgp_status = bgp_states[peer_cfg_ip]
                        else:
                            bgp_status = "-"

                        bgp_display = format_bgp_status(bgp_status)

                        peer_display = (
                            tunnel_peer_map.get(inst_cfg.hostname, {}).get(tunnel_name) or "-"
                        )
                        role = format_role(
                            tunnel_role_map.get(inst_cfg.hostname, {}).get(tunnel_name)
                        )
                        carrying_display = format_carrying(tunnel_name, carrying_tunnel)
                        enc_algos = tunnel_encryption.get(tunnel_name) or []
                        if not enc_algos:
                            enc_algos = tunnel_ike_encryption.get(tunnel_name) or []
                        encryption_display = ", ".join(enc_algos) if enc_algos else "n/a"
                        uptime_display = tunnel_uptime.get(tunnel_name, "n/a")

                        table.add_row(
                            tunnel_name,
                            role,
                            carrying_display,
                            inst_cfg.hostname,
                            status_display,
                            bgp_display,
                            peer_display,
                            encryption_display,
                            uptime_display,
                        )

                    continue

            # Fall back to ipsec statusall if swanctl is unavailable
            result = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=10",
                    f"ubuntu@{target}",
                    "sudo ipsec statusall",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )

            if result.returncode != 0:
                table.add_row(
                    "All tunnels",
                    "-",
                    "-",
                    inst_cfg.hostname,
                    "[red]ERROR[/red]",
                    "-",
                    "-",
                    "-",
                    f"Failed to get status: {result.stderr.strip()}",
                )
                continue

            output = result.stdout

            # Parse IPsec status output
            # Look for patterns like: "gcp-classic-tunnel-0[202]: ESTABLISHED 8 minutes ago, 10.48.0.13[10.48.0.13]...34.155.169.244[34.155.169.244]"
            tunnel_pattern = re.compile(
                r"(\S+)\[\d+\]:\s+(\w+)\s+(.+?),\s+[\d.]+\[[\d.]+\]\.\.\.(\d+\.\d+\.\d+\.\d+)\["
            )

            tunnels = {}
            for match in tunnel_pattern.finditer(output):
                tunnel_name = match.group(1)
                status = match.group(2)
                raw_uptime = match.group(3)
                formatted_uptime = parse_strongswan_uptime(raw_uptime)
                peer_ip = match.group(4)
                tunnels[tunnel_name] = {
                    "status": status,
                    "uptime": formatted_uptime,
                    "peer_ip": peer_ip,
                    "encryption": "Unknown",
                    "bgp": "-",
                    "role": format_role(
                        tunnel_role_map.get(inst_cfg.hostname, {}).get(tunnel_name)
                    ),
                }

            # Parse encryption from IKE proposal lines
            # Pattern: "IKE proposal: AES_GCM_16_128/PRF_AES128_XCBC/MODP_2048"
            ike_pattern = re.compile(r"(\S+)\[\d+\]:.*?IKE proposal:\s+(\S+)")
            for match in ike_pattern.finditer(output):
                tunnel_name = match.group(1)
                encryption = match.group(2)
                if tunnel_name in tunnels:
                    tunnels[tunnel_name]["encryption"] = encryption

            # Fallback: parse simplified connection lines if no SAs matched yet
            # Example: "gcp-ha-tunnel-1:  %any...34.157.15.187  IKEv2, dpddelay=30s"
            if not tunnels:
                conn_line_pattern = re.compile(
                    r"^(\S+):\s+%any\.\.\.(\d+\.\d+\.\d+\.\d+)", re.MULTILINE
                )
                for match in conn_line_pattern.finditer(output):
                    tunnels[match.group(1)] = {
                        "status": "CONNECTING",
                        "uptime": "-",
                        "peer_ip": match.group(2),
                        "encryption": "Unknown",
                        "bgp": "-",
                        "role": format_role(
                            tunnel_role_map.get(inst_cfg.hostname, {}).get(match.group(1))
                        ),
                    }

            # Attach BGP states where we know the peer IP from config
            for tname, tinfo in tunnels.items():
                peer_cfg_ip = tunnel_bgp_map.get(inst_cfg.hostname, {}).get(tname)
                if peer_cfg_ip and peer_cfg_ip in bgp_states:
                    tinfo["bgp"] = bgp_states[peer_cfg_ip]
                elif bgp_states:
                    # Fallback: if we have BGP states but no exact match, try to match any peer
                    # This handles cases where tunnel name mapping might be off
                    for _bgp_ip, bgp_state in bgp_states.items():
                        # Simple heuristic: assign if we don't have a BGP status yet
                        if tinfo.get("bgp") == "-":
                            tinfo["bgp"] = bgp_state
                            break

            # Add rows to table
            if tunnels:
                tunnel_statuses = {
                    name: str(info.get("status", "")).upper() for name, info in tunnels.items()
                }
                carrying_tunnel = select_carrying_tunnel(
                    inst_cfg.hostname,
                    list(tunnels.keys()),
                    tunnel_statuses,
                    bgp_states,
                )
                for tunnel_name, info in tunnels.items():
                    status_text = info["status"]
                    if status_text == "ESTABLISHED":
                        status_display = "[green]Established[/green]"
                    elif status_text == "CONNECTING":
                        status_display = "[yellow]Connecting[/yellow]"
                    else:
                        status_display = f"[red]{status_text.capitalize()}[/red]"

                    # Format BGP status with colors
                    bgp_status = info.get("bgp", "-")
                    bgp_display = format_bgp_status(bgp_status)
                    carrying_display = format_carrying(tunnel_name, carrying_tunnel)

                    table.add_row(
                        tunnel_name,
                        info.get("role", "-"),
                        carrying_display,
                        inst_cfg.hostname,
                        status_display,
                        bgp_display,
                        info["peer_ip"],
                        info["encryption"],
                        info["uptime"],
                    )
            else:
                # No tunnels found in output
                if "no matching" in output.lower() or "no active" in output.lower():
                    table.add_row(
                        "No tunnels",
                        "-",
                        "-",
                        inst_cfg.hostname,
                        "[yellow]NONE[/yellow]",
                        "-",
                        "-",
                        "-",
                        "-",
                    )
                else:
                    table.add_row(
                        "Unknown",
                        "-",
                        "-",
                        inst_cfg.hostname,
                        "[red]PARSE ERROR[/red]",
                        "-",
                        "-",
                        "-",
                        "Could not parse ipsec output",
                    )
                    # Show a trimmed snippet to aid debugging
                    snippet = "\n".join(output.splitlines()[:20])
                    print(
                        f"[yellow]{inst_cfg.hostname} ipsec status output (first lines):[/yellow]\n{snippet}\n"
                    )

        except subprocess.TimeoutExpired:
            table.add_row(
                "All tunnels",
                "-",
                "-",
                inst_cfg.hostname,
                "[red]TIMEOUT[/red]",
                "-",
                "-",
                "-",
                "SSH command timed out",
            )
        except Exception as e:
            table.add_row(
                "All tunnels",
                "-",
                "-",
                inst_cfg.hostname,
                "[red]ERROR[/red]",
                "-",
                "-",
                "-",
                str(e),
            )

    console.print(table)

    # Show service health
    console.print("\n[bold]Checking system services...[/bold]")
    service_table = Table(show_header=True, header_style="bold cyan")
    service_table.add_column("Gateway VM", style="white")
    service_table.add_column("Agent", style="white")
    service_table.add_column("StrongSwan", style="white")
    service_table.add_column("FRR", style="white")

    for inst_cfg in plan.iter_instance_configs():
        target = vm_ips.get(inst_cfg.hostname)
        if not target:
            continue

        services = {
            "nebius-vpngw-agent": "Unknown",
            "strongswan": "Unknown",  # Check process, not systemd service
            "frr": "Unknown",
        }

        for service_name in services:
            try:
                # Special handling for strongSwan - check if charon daemon is running
                if service_name == "strongswan":
                    result = subprocess.run(
                        [
                            "ssh",
                            "-o",
                            "StrictHostKeyChecking=no",
                            "-o",
                            "ConnectTimeout=10",
                            f"ubuntu@{target}",
                            "pgrep -x charon >/dev/null && echo active || echo inactive",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        shell=False,
                    )
                else:
                    result = subprocess.run(
                        [
                            "ssh",
                            "-o",
                            "StrictHostKeyChecking=no",
                            "-o",
                            "ConnectTimeout=10",
                            f"ubuntu@{target}",
                            f"systemctl is-active {service_name}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )

                status_raw = result.stdout.strip()
                if status_raw == "active":
                    services[service_name] = "[green]active[/green]"
                elif status_raw == "inactive":
                    services[service_name] = "[yellow]inactive[/yellow]"
                else:
                    services[service_name] = f"[red]{status_raw}[/red]"
                    # Fetch last few lines of systemctl status for context
                    try:
                        detail_cmd = f"systemctl status {service_name} --no-pager -n 20"
                        if service_name == "strongswan":
                            detail_cmd = "systemctl status strongswan-starter --no-pager -n 20 || systemctl status strongswan --no-pager -n 20"
                        detail = subprocess.run(
                            [
                                "ssh",
                                "-o",
                                "StrictHostKeyChecking=no",
                                "-o",
                                "ConnectTimeout=10",
                                f"ubuntu@{target}",
                                detail_cmd,
                            ],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            shell=False,
                        )
                        snippet = (detail.stdout or detail.stderr or "").strip()
                        if snippet:
                            print(
                                f"[yellow]{inst_cfg.hostname} {service_name} status:[/yellow]\n{snippet}\n"
                            )
                    except Exception:
                        pass

            except Exception:
                services[service_name] = "[red]error[/red]"

        service_table.add_row(
            inst_cfg.hostname,
            services["nebius-vpngw-agent"],
            services["strongswan"],
            services["frr"],
        )

    console.print(service_table)

    # Show routing health (checks for routing table invariants)
    console.print("\n[bold]Routing Table Health:[/bold]")
    routing_table = Table(show_header=True, header_style="bold cyan")
    routing_table.add_column("Gateway VM", style="white")
    routing_table.add_column("Table 220", style="white")
    routing_table.add_column("Broad APIPA", style="white")
    routing_table.add_column("Tunnel Routes", style="white")
    routing_table.add_column("Overall", style="white")

    for inst_cfg in plan.iter_instance_configs():
        target = vm_ips.get(inst_cfg.hostname)
        if not target:
            continue

        try:
            # Check routing health by running Python status check remotely
            check_cmd = """python3 -c "
import subprocess
import json

health = {
    'table_220': False,
    'broad_apipa': False,
    'orphaned_count': 0,
    'status': 'healthy'
}

# Check table 220
r = subprocess.run(['ip', 'rule', 'show'], capture_output=True, text=True)
if '220' in r.stdout:
    health['table_220'] = True
    health['status'] = 'error'

# Check broad APIPA
r = subprocess.run(['ip', 'route', 'show', '169.254.0.0/16'], capture_output=True, text=True)
if r.stdout.strip():
    health['broad_apipa'] = True
    health['status'] = 'error'

# Count APIPA tunnel routes (VTI subnets + BGP peer /32s)
# This is for informational purposes - these are expected/legitimate routes
r = subprocess.run(['ip', 'route', 'show'], capture_output=True, text=True)
apipa_count = 0
for line in r.stdout.split('\\n'):
    if '169.254.' in line and not line.startswith('169.254.169.'):
        apipa_count += 1

health['orphaned_count'] = apipa_count  # Note: 'orphaned' name kept for compatibility

print(json.dumps(health))
" """

            result = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=10",
                    f"ubuntu@{target}",
                    check_cmd,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
            )

            if result.returncode == 0 and result.stdout.strip():
                try:
                    health = json.loads(result.stdout.strip())

                    # Format table 220 status
                    if health.get("table_220"):
                        table_220_display = "[red]EXISTS[/red]"
                    else:
                        table_220_display = "[green]OK[/green]"

                    # Format broad APIPA status
                    if health.get("broad_apipa"):
                        broad_apipa_display = "[red]EXISTS[/red]"
                    else:
                        broad_apipa_display = "[green]OK[/green]"

                    # Format tunnel routes count (APIPA routes for VTI interfaces)
                    tunnel_routes_count = health.get("orphaned_count", 0)
                    tunnel_routes_display = f"{tunnel_routes_count} routes"

                    # Overall status
                    status = health.get("status", "unknown")
                    if status == "healthy":
                        overall_display = "[green]Healthy[/green]"
                    elif status == "warning":
                        overall_display = "[yellow]Warning[/yellow]"
                    else:
                        overall_display = "[red]Issues Found[/red]"

                    routing_table.add_row(
                        inst_cfg.hostname,
                        table_220_display,
                        broad_apipa_display,
                        tunnel_routes_display,
                        overall_display,
                    )
                except json.JSONDecodeError:
                    routing_table.add_row(
                        inst_cfg.hostname,
                        "[red]ERROR[/red]",
                        "[red]ERROR[/red]",
                        "-",
                        "[red]Parse Error[/red]",
                    )
            else:
                routing_table.add_row(
                    inst_cfg.hostname,
                    "[red]ERROR[/red]",
                    "[red]ERROR[/red]",
                    "-",
                    "[red]Check Failed[/red]",
                )

        except subprocess.TimeoutExpired:
            routing_table.add_row(
                inst_cfg.hostname,
                "[red]TIMEOUT[/red]",
                "[red]TIMEOUT[/red]",
                "-",
                "[red]Timeout[/red]",
            )
        except Exception as e:
            routing_table.add_row(
                inst_cfg.hostname,
                "[red]ERROR[/red]",
                "[red]ERROR[/red]",
                "-",
                f"[red]{str(e)[:20]}[/red]",
            )

    console.print(routing_table)

    # Show vpngw-subnet route table
    console.print("\n[bold]VPN Gateway Subnet Route Table:[/bold]")
    try:
        from nebius.api.nebius.vpc.v1 import (
            GetRouteTableRequest,
            GetSubnetByNameRequest,
            ListRoutesRequest,
            RouteServiceClient,
            RouteTableServiceClient,
            SubnetServiceClient,
        )
        from rich.table import Table

        client = vm_mgr._get_client()
        if client and proj_id:
            subnet_client = SubnetServiceClient(client)

            try:
                # Get vpngw-subnet
                subnet_obj = subnet_client.get_by_name(
                    GetSubnetByNameRequest(parent_id=proj_id, name="vpngw-subnet")
                ).wait()

                # Get subnet CIDR
                subnet_spec = getattr(subnet_obj, "spec", None)
                subnet_cidrs = []
                if subnet_spec:
                    ipv4_pools = getattr(subnet_spec, "ipv4_private_pools", None)
                    if ipv4_pools:
                        pools = getattr(ipv4_pools, "pools", []) or []
                        for pool in pools:
                            cidrs = getattr(pool, "cidrs", []) or []
                            for cidr_obj in cidrs:
                                cidr_str = getattr(cidr_obj, "cidr", None)
                                if cidr_str:
                                    subnet_cidrs.append(cidr_str)

                subnet_cidr = subnet_cidrs[0] if subnet_cidrs else "unknown"

                # Get route table ID
                rt_id = getattr(subnet_spec, "route_table_id", None) if subnet_spec else None

                if not rt_id:
                    console.print(f"[yellow]Subnet: vpngw-subnet ({subnet_cidr})[/yellow]")
                    console.print("[yellow]  No route table attached[/yellow]")
                else:
                    # Get route table details
                    rt_client = RouteTableServiceClient(client)
                    route_client = RouteServiceClient(client)

                    rt_obj = rt_client.get(GetRouteTableRequest(id=rt_id)).wait()
                    rt_meta = getattr(rt_obj, "metadata", None)
                    rt_name = getattr(rt_meta, "name", None) or "unknown"

                    # Check if it's default route table
                    is_default = False
                    try:
                        subnet_status = getattr(subnet_obj, "status", None)
                        if subnet_status:
                            rt_info = getattr(subnet_status, "route_table", None)
                            if rt_info:
                                is_default = getattr(rt_info, "default", False)
                    except Exception:
                        pass

                    console.print(f"Subnet: vpngw-subnet ({subnet_cidr})")
                    console.print(f"  Route Table: {rt_name} (ID: {rt_id}, default={is_default})")

                    # Get routes in the table
                    routes_list_op = route_client.list(ListRoutesRequest(parent_id=rt_id))
                    routes_list = (
                        routes_list_op.wait() if hasattr(routes_list_op, "wait") else routes_list_op
                    )

                    route_items = []
                    if hasattr(routes_list, "items"):
                        route_items = routes_list.items
                    elif hasattr(routes_list, "__iter__"):
                        route_items = list(routes_list)

                    if route_items:
                        # Create routes table
                        routes_table = Table(show_header=True, header_style="bold cyan", box=None)
                        routes_table.add_column("Destination", style="white")
                        routes_table.add_column("Next Hop", style="white")

                        for route in route_items:
                            route_spec = getattr(route, "spec", None)
                            if not route_spec:
                                continue

                            # Get destination
                            dest = getattr(route_spec, "destination", None)
                            dest_cidr = getattr(dest, "cidr", None) if dest else "unknown"

                            # Get next hop
                            next_hop_text = "unknown"
                            next_hop = getattr(route_spec, "next_hop", None)
                            if next_hop:
                                # Check for default_egress_gateway field
                                if hasattr(next_hop, "default_egress_gateway") and getattr(
                                    next_hop, "default_egress_gateway", False
                                ):
                                    next_hop_text = "default-egress"
                                elif hasattr(next_hop, "default_internet_gateway") and getattr(
                                    next_hop, "default_internet_gateway", False
                                ):
                                    next_hop_text = "default-gateway"
                                elif hasattr(next_hop, "allocation"):
                                    alloc = next_hop.allocation
                                    alloc_id = getattr(alloc, "id", None)
                                    if alloc_id:
                                        next_hop_text = f"allocation:{alloc_id[:16]}..."

                            routes_table.add_row(dest_cidr, next_hop_text)

                        console.print(routes_table)
                    else:
                        console.print("  [dim]No routes in table[/dim]")

            except Exception as e:
                console.print(f"[yellow]Could not fetch vpngw-subnet route table: {e}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Error displaying route table: {e}[/yellow]")


@app.command(name="add-routes-local")
def add_routes_local(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
):
    """Add VPC routes for gateway.local_prefixes pointing to VPN gateway (Nebius → Remote).

    These routes direct traffic from Nebius VPC subnets to remote sites via the VPN gateway.
    Next-hop is the VPN gateway's private IP.
    """
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)

    print("[bold]Parsing deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

    # Resolve project_id
    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None

    # Get token for API access (required for route management)
    auth_token = _ensure_authentication(required=True, show_progress=True)

    routes = RouteManager(project_id=proj_id, auth_token=auth_token)

    print("[bold]Ensuring VPC routes for local prefixes (Nebius → Remote)...[/bold]")
    routes.add_routes(plan, local_cfg)

    print("[green]Local route management completed.[/green]")


@app.command(name="list-routes-local")
def list_routes_local(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
):
    """List VPC routes for gateway.local_prefixes (Nebius → Remote) and BGP advertised routes.

    Shows:
    1. Route table entries in Nebius VPC subnets that match local_prefixes
    2. BGP routes being advertised to peer routers (organized by connection/tunnel)
    """
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)

    print("[bold]Parsing deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None

    # Get token for API access (required for route management)
    auth_token = _ensure_authentication(required=True, show_progress=True)

    routes = RouteManager(project_id=proj_id, auth_token=auth_token)

    print("[bold]Listing VPC routes for local prefixes...[/bold]")
    routes.list_routes(plan, local_cfg)


@app.command(name="list-routes-remote")
def list_routes_remote(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    connection: str | None = typer.Option(
        None, help="Connection name to show routes for (default: all)"
    ),
):
    """List remote routes learned/configured via VPN (Remote → Nebius).

    - BGP mode: Shows BGP-learned routes from peers with whitelist status
    - Static mode: Shows static routes configured on gateway VMs
    """
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)

    print("[bold]Parsing deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

    # Get project_id for RouteManager (not really needed for this command but kept for consistency)
    proj_id = local_cfg.get("project_id") or ""

    routes = RouteManager(project_id=proj_id, auth_token=None)

    print("[bold]Querying remote routes from gateway VMs...[/bold]")
    routes.list_remote_routes(plan, local_cfg, connection_filter=connection)


@app.command()
def destroy(
    local_config_file: Path | None = typer.Option(
        None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"
    ),
    project_id: str | None = typer.Option(None, help="Nebius project/folder identifier"),
    zone: str | None = typer.Option(None, help="Nebius zone for gateway VMs"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Delete gateway VMs, boot disks, private IP allocations, and routes (preserves public IPs)."""
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=False,
        exit_after_create=False,
    )

    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)

    print("[bold]Parsing deployment plan...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

    # Resolve context from CLI args or config
    tenant_id = (local_cfg.get("tenant_id") or "").strip() or None
    proj_id = project_id or (local_cfg.get("project_id") or "").strip() or None
    region_id = (local_cfg.get("region_id") or "").strip() or None

    # Get token for API access (required for VM management)
    auth_token = _ensure_authentication(required=True, show_progress=True)

    vm_mgr = VMManager(
        project_id=proj_id,
        zone=zone or plan.gateway_group.region,
        auth_token=auth_token,
        tenant_id=tenant_id,
        region_id=region_id,
    )

    # Confirmation prompt
    if not yes:
        print("\n[yellow]⚠️  WARNING: This will:[/yellow]")
        print(f"[yellow]  • Delete all gateway VMs ({plan.gateway_group.name}-*)[/yellow]")
        print("[yellow]  • Delete all boot disks[/yellow]")
        print("[yellow]  • Delete static private IP allocations[/yellow]")
        print("[yellow]  • Delete VPC routes pointing to gateway[/yellow]")
        print("[yellow]  • Terminate all VPN tunnels[/yellow]")
        print("")
        print("[green]  ✓ Preserve network resources (VPC, subnets)[/green]")
        print("[green]  ✓ Preserve public IP allocations (reusable)[/green]")
        print("")
        import sys

        sys.stdout.write("\033[1mProceed with destruction? [y/N]:\033[0m ")
        sys.stdout.flush()
        response = input().strip().lower()
        if response not in ("y", "yes"):
            print("[green]Aborted. No changes made.[/green]")
            raise typer.Exit(code=0)

    print("[bold]Destroying gateway infrastructure...[/bold]")

    try:
        # Import the client
        Client = None
        try:
            from nebius.sdk import SDK as _C

            Client = _C
        except Exception:
            try:
                from nebius.sdk import Client as _C

                Client = _C
            except Exception:
                try:
                    from nebius.client import Client as _C

                    Client = _C
                except Exception as e:
                    print(
                        "[red]Error: Nebius SDK not available. Install with 'pip install nebius'.[/red]"
                    )
                    raise typer.Exit(code=1) from e

        if (
            vm_mgr.tenant_id
            and vm_mgr.project_id
            and (vm_mgr.region_id or plan.gateway_group.region)
        ):
            try:
                client = Client(
                    tenant_id=vm_mgr.tenant_id,
                    project_id=vm_mgr.project_id,
                    region_id=vm_mgr.region_id or plan.gateway_group.region,
                )
            except TypeError:
                client = Client()
        else:
            client = Client()

        # Get service clients
        from nebius.api.nebius.compute.v1 import (
            DiskServiceClient,
            InstanceServiceClient,
            ListInstancesRequest,
        )
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient

        isc = InstanceServiceClient(client)
        dsc = DiskServiceClient(client)
        asc = AllocationServiceClient(client)

        # List existing VMs matching the gateway group name
        print(
            f"[bold]Step 1/5: Listing VMs matching pattern '{plan.gateway_group.name}-*'...[/bold]"
        )
        ilist_op = isc.list(ListInstancesRequest(parent_id=proj_id or ""))
        ilist = ilist_op.wait() if hasattr(ilist_op, "wait") else ilist_op

        # Extract items from the response
        items = []
        if hasattr(ilist, "items"):
            items = ilist.items
        elif hasattr(ilist, "__iter__"):
            items = list(ilist)

        existing = [
            inst
            for inst in items
            if getattr(getattr(inst, "metadata", None), "name", "").startswith(
                f"{plan.gateway_group.name}-"
            )
        ]

        if not existing:
            print(f"[yellow]No VMs found matching '{plan.gateway_group.name}-*'.[/yellow]")
        else:
            print(f"[yellow]Found {len(existing)} VM(s) to delete[/yellow]")

        # Collect private IP allocations to delete
        # Method 1: From existing VMs (if any)
        private_alloc_ids = []
        for inst in existing:
            inst_name = getattr(getattr(inst, "metadata", None), "name", None) or "unknown"
            # Get network interfaces from VM status
            if hasattr(inst, "status") and hasattr(inst.status, "network_interfaces"):
                for ni in inst.status.network_interfaces:
                    # Private IP allocation (we want to delete these)
                    if hasattr(ni, "ip_address") and hasattr(ni.ip_address, "allocation_id"):
                        if ni.ip_address.allocation_id:
                            private_alloc_ids.append((inst_name, ni.ip_address.allocation_id))
                            print(
                                f"[dim]Found private allocation from VM {inst_name}: {ni.ip_address.allocation_id}[/dim]"
                            )

        # Method 2: Search by name pattern (catches allocations from already-deleted VMs)
        try:
            from nebius.api.nebius.vpc.v1 import ListAllocationsRequest

            alloc_list_op = asc.list(ListAllocationsRequest(parent_id=proj_id or ""))
            alloc_list = alloc_list_op.wait() if hasattr(alloc_list_op, "wait") else alloc_list_op

            alloc_items = []
            if hasattr(alloc_list, "items"):
                alloc_items = alloc_list.items
            elif hasattr(alloc_list, "__iter__"):
                alloc_items = list(alloc_list)

            # Look for private IP allocations matching our naming pattern
            for alloc in alloc_items:
                alloc_name = getattr(getattr(alloc, "metadata", None), "name", None)
                alloc_id = getattr(alloc, "id", None) or getattr(
                    getattr(alloc, "metadata", None), "id", None
                )

                # Check if this is a private allocation for our gateway
                # Pattern: {gateway-name}-{index}-eth{nic}-private-ip
                if alloc_name and alloc_id:
                    for i in range(plan.gateway_group.instance_count):
                        expected_name = f"{plan.gateway_group.name}-{i}-eth0-private-ip"
                        if alloc_name == expected_name:
                            # Check if we already have this from VM inspection
                            if not any(aid == alloc_id for _, aid in private_alloc_ids):
                                inst_name = f"{plan.gateway_group.name}-{i}"
                                private_alloc_ids.append((inst_name, alloc_id))
                                print(
                                    f"[dim]Found private allocation by name pattern {alloc_name}: {alloc_id}[/dim]"
                                )
                            break
        except Exception as e:
            print(f"[dim]Could not search for allocations by name: {e}[/dim]")

        # Step 2: Delete VMs
        print("[bold]Step 2/5: Deleting VMs...[/bold]")
        for inst in existing:
            inst_id = getattr(inst, "id", None) or getattr(
                getattr(inst, "metadata", None), "id", None
            )
            inst_name = getattr(getattr(inst, "metadata", None), "name", None) or "unknown"

            if inst_id:
                try:
                    print(f"[VMManager] Deleting VM {inst_name} (id={inst_id})...")
                    from nebius.api.nebius.compute.v1 import DeleteInstanceRequest

                    delete_req = DeleteInstanceRequest(id=inst_id)
                    op = isc.delete(delete_req)
                    if hasattr(op, "wait"):
                        op.wait()
                        print(f"[green]✓ VM {inst_name} deleted[/green]")
                except Exception as e:
                    print(f"[red]Failed to delete VM {inst_name}: {e}[/red]")

        # Wait for VM deletions to complete
        if existing:
            import time

            print("[VMManager] Waiting for VM deletions to complete...")
            time.sleep(15)

        # Step 3: Delete boot disks
        print("[bold]Step 3/5: Deleting boot disks...[/bold]")
        import time

        from nebius.api.nebius.common.v1 import GetByNameRequest

        for i in range(plan.gateway_group.instance_count):
            inst_name = f"{plan.gateway_group.name}-{i}"
            boot_disk_name = f"{inst_name}-boot"

            try:
                disk_obj = dsc.get_by_name(
                    GetByNameRequest(parent_id=proj_id, name=boot_disk_name)
                ).wait()
                disk_id = getattr(disk_obj, "id", None) or getattr(
                    getattr(disk_obj, "metadata", None), "id", None
                )

                if disk_id:
                    # Retry disk deletion up to 3 times
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            print(
                                f"[VMManager] Deleting boot disk {boot_disk_name} (id={disk_id})..."
                            )
                            from nebius.api.nebius.compute.v1 import DeleteDiskRequest

                            delete_disk_req = DeleteDiskRequest(id=disk_id)
                            disk_op = dsc.delete(delete_disk_req)
                            if hasattr(disk_op, "wait"):
                                disk_op.wait()
                                print(f"[green]✓ Boot disk {boot_disk_name} deleted[/green]")
                            break
                        except Exception as disk_err:
                            if "FAILED_PRECONDITION" in str(
                                disk_err
                            ) and "read-write attachments" in str(disk_err):
                                if attempt < max_retries - 1:
                                    wait_time = 10 * (attempt + 1)
                                    print(
                                        f"[yellow]Disk still attached, waiting {wait_time}s before retry {attempt + 2}/{max_retries}...[/yellow]"
                                    )
                                    time.sleep(wait_time)
                                else:
                                    print(
                                        f"[red]Could not delete boot disk {boot_disk_name} after {max_retries} attempts: {disk_err}[/red]"
                                    )
                            else:
                                print(
                                    f"[red]Could not delete boot disk {boot_disk_name}: {disk_err}[/red]"
                                )
                                break
            except Exception:
                # Non-fatal: disk might not exist
                print(
                    f"[dim]Boot disk {boot_disk_name} not found (may have been already deleted)[/dim]"
                )

        # Step 4: Delete VPC routes (MUST happen before deleting private IP allocations)
        print("[bold]Step 4/5: Deleting VPC routes pointing to gateway allocations...[/bold]")
        deleted_routes = []
        try:
            from nebius.api.nebius.vpc.v1 import (
                ListRoutesRequest,
                ListRouteTablesRequest,
                RouteServiceClient,
                RouteTableServiceClient,
            )

            rtc = RouteTableServiceClient(client)
            rsc = RouteServiceClient(client)

            # List all route tables in the project
            rt_list_op = rtc.list(ListRouteTablesRequest(parent_id=proj_id or ""))
            rt_list = rt_list_op.wait() if hasattr(rt_list_op, "wait") else rt_list_op

            rt_items = []
            if hasattr(rt_list, "items"):
                rt_items = rt_list.items
            elif hasattr(rt_list, "__iter__"):
                rt_items = list(rt_list)

            # For each route table, list its routes
            for rt in rt_items:
                rt_id = getattr(rt, "id", None) or getattr(
                    getattr(rt, "metadata", None), "id", None
                )
                rt_name = getattr(getattr(rt, "metadata", None), "name", None) or "unknown"

                if not rt_id:
                    continue

                # List routes in this table using ListRoutesRequest
                try:
                    routes_list_op = rsc.list(ListRoutesRequest(parent_id=rt_id))
                    routes_list = (
                        routes_list_op.wait() if hasattr(routes_list_op, "wait") else routes_list_op
                    )

                    route_items = []
                    if hasattr(routes_list, "items"):
                        route_items = routes_list.items
                    elif hasattr(routes_list, "__iter__"):
                        route_items = list(routes_list)

                    for route in route_items:
                        route_id = getattr(route, "id", None) or getattr(
                            getattr(route, "metadata", None), "id", None
                        )
                        route_name = (
                            getattr(getattr(route, "metadata", None), "name", None) or "unknown"
                        )
                        spec = getattr(route, "spec", None)
                        next_hop = getattr(spec, "next_hop", None) if spec else None

                        # Check if this route uses one of our private allocations
                        # NextHop has an 'allocation' field with an 'id' sub-field
                        if next_hop and hasattr(next_hop, "allocation"):
                            allocation = next_hop.allocation
                            if hasattr(allocation, "id") and allocation.id:
                                nh_alloc_id = allocation.id
                                for _inst_name, alloc_id in private_alloc_ids:
                                    if nh_alloc_id == alloc_id:
                                        # Delete this route
                                        try:
                                            print(f"Deleting route {route_name} → {alloc_id}")
                                            from nebius.api.nebius.vpc.v1 import (
                                                DeleteRouteRequest,
                                            )

                                            delete_route_req = DeleteRouteRequest(id=route_id)
                                            route_op = rsc.delete(delete_route_req)
                                            if hasattr(route_op, "wait"):
                                                route_op.wait()
                                                deleted_routes.append(route_id)
                                        except Exception as e:
                                            print(f"[yellow]Could not delete route: {e}[/yellow]")
                                        break
                except Exception as e:
                    print(f"[yellow]Could not list routes for table {rt_name}: {e}[/yellow]")

            if deleted_routes:
                print(f"[green]Deleted {len(deleted_routes)} route(s)[/green]")
            else:
                print("[dim]No routes found using gateway allocations[/dim]")
        except Exception as e:
            print(f"[yellow]Could not clean up routes: {e}[/yellow]")
            print(
                "[yellow]You may need to manually delete routes before private IP allocations can be removed[/yellow]"
            )

        # Step 5: Delete static private IP allocations (after routes are deleted)
        print("[bold]Step 5/5: Deleting static private IP allocations...[/bold]")
        if private_alloc_ids:
            from nebius.api.nebius.vpc.v1 import DeleteAllocationRequest

            for inst_name, alloc_id in private_alloc_ids:
                try:
                    print(
                        f"[VMManager] Deleting private IP allocation for {inst_name} (id={alloc_id})..."
                    )
                    delete_alloc_req = DeleteAllocationRequest(id=alloc_id)
                    alloc_op = asc.delete(delete_alloc_req)
                    if hasattr(alloc_op, "wait"):
                        alloc_op.wait()
                        print("[green]✓ Private IP allocation deleted[/green]")
                except Exception as e:
                    # Check if it's already deleted (lifecycle managed by network interface)
                    if "NOT_FOUND" in str(e):
                        print(
                            "[dim]Private IP allocation already deleted (auto-managed by network interface)[/dim]"
                        )
                    elif "FAILED_PRECONDITION" in str(e) and "used as next hop for routes" in str(
                        e
                    ):
                        print(
                            f"[yellow]Could not delete private IP allocation (still used by routes): {e}[/yellow]"
                        )
                        print("[yellow]This may require manual cleanup via console or CLI[/yellow]")
                    else:
                        print(f"[yellow]Could not delete private IP allocation: {e}[/yellow]")
        else:
            print("[dim]No private IP allocations found to delete[/dim]")

        print()
        print("[green]✓ Destroy completed successfully.[/green]")
        print("[dim]Preserved resources:[/dim]")
        print("[dim]  • Network resources (VPC, subnets)[/dim]")
        print("[dim]  • Public IP allocations (reusable via external_ips in config)[/dim]")
        print("")
        print("[yellow]⚠️  IMPORTANT: After recreating VMs, you must run:[/yellow]")
        print("[bold]  nebius-vpngw add-routes-local --local-config-file <your-config.yaml>[/bold]")
        print("[dim]This will create new routes with the new static private IP allocations.[/dim]")

    except Exception as e:
        print(f"[red]Error during destroy: {e}[/red]")
        raise typer.Exit(code=1) from e


@app.command(name="restart-tunnel")
def restart_tunnel(
    tunnel_name: str = typer.Argument(
        ...,
        help="Name of the tunnel to restart (use 'all' to restart all tunnels). Use 'nebius-vpngw status' to find tunnel names.",
    ),
    local_config_file: Path = typer.Option(
        None,
        "--local-config-file",
        "-c",
        help="Path to local config file",
        show_default="nebius-vpngw.config.yaml in current directory",
    ),
) -> None:
    """
    Manually restart IPsec tunnel(s) to recover from stale state.

    This command connects to the gateway VMs via SSH and executes the tunnel
    restart procedure. Useful for immediate recovery from tunnel state desync
    or after network maintenance.

    Examples:

      # Restart specific tunnel
      nebius-vpngw restart-tunnel gcp-ha-tunnel-1

      # Restart all tunnels
      nebius-vpngw restart-tunnel all

      # Use custom config file
      nebius-vpngw restart-tunnel gcp-ha-tunnel-1 -c my-config.yaml
    """
    try:
        # Resolve config path
        config_path = _resolve_local_config(
            local_config_file, create_if_missing=False, exit_after_create=False
        )
        if not config_path:
            raise typer.Exit(code=1)

        print(f"[bold]Loading config from:[/bold] {config_path}")
        local_cfg = load_local_config(config_path)

        # Get gateway instances
        gateway_group = local_cfg.get("gateway_group", {})
        instance_count = gateway_group.get("instance_count", 1)

        print(f"[bold]Found {instance_count} gateway instance(s)[/bold]")

        # Construct restart command
        if tunnel_name.lower() == "all":
            cmd = "python3 -m nebius_vpngw.agent.tunnel_health_monitor --restart-tunnel all"
            action_desc = "all tunnels"
        else:
            cmd = f"python3 -m nebius_vpngw.agent.tunnel_health_monitor --restart-tunnel {tunnel_name}"
            action_desc = f"tunnel '{tunnel_name}'"

        print(f"[bold]Restarting {action_desc}...[/bold]")

        # Get SSH credentials and resolved deployment plan
        vm_spec = gateway_group.get("vm_spec", {})
        username = vm_spec.get("ssh_username", os.environ.get("VPNGW_SSH_USER", "ubuntu"))
        key_path_str = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
        key_path = Path(key_path_str).expanduser() if key_path_str else None

        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])

        success_count = 0

        for inst in plan.per_instance:
            hostname = inst.hostname
            external_ip = inst.external_ip

            if not external_ip:
                print(f"[yellow]⚠️  No external IP for {hostname}, skipping[/yellow]")
                continue

            print(f"\\n[dim]Connecting to {hostname} ({external_ip})...[/dim]")

            # Build SSH command
            ssh_cmd = ["ssh"]
            if key_path:
                ssh_cmd.extend(["-i", str(key_path)])
            ssh_cmd.extend(
                [
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-o",
                    "ConnectTimeout=10",
                    f"{username}@{external_ip}",
                    cmd,
                ]
            )

            try:
                import subprocess

                result = subprocess.run(
                    ssh_cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                if result.returncode == 0:
                    print(f"[green]✓ Successfully restarted on {hostname}[/green]")
                    if result.stdout.strip():
                        print(f"[dim]{result.stdout.strip()}[/dim]")
                    success_count += 1
                else:
                    print(f"[red]✗ Failed on {hostname}[/red]")
                    if result.stderr:
                        print(f"[dim]{result.stderr.strip()}[/dim]")
            except subprocess.TimeoutExpired:
                print(f"[red]✗ Timeout connecting to {hostname}[/red]")
            except Exception as e:
                print(f"[red]✗ Error connecting to {hostname}: {e}[/red]")

        print()
        if success_count == instance_count:
            print(
                f"[green]✓ Successfully restarted {action_desc} on all {instance_count} gateway(s)[/green]"
            )
            print(
                "[dim]Tunnels should re-establish within 10-15 seconds. Run 'nebius-vpngw status' to verify.[/dim]"
            )
        elif success_count > 0:
            print(
                f"[yellow]⚠️  Partial success: restarted on {success_count}/{instance_count} gateway(s)[/yellow]"
            )
            raise typer.Exit(code=1)
        else:
            print("[red]✗ Failed to restart on any gateway[/red]")
            raise typer.Exit(code=1)

    except typer.Exit:
        raise
    except Exception as e:
        print(f"[red]Error during tunnel restart: {e}[/red]")
        import traceback

        print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(code=1) from e


@app.command(name="failover")
def tunnel_failover(
    tunnel_name: str | None = typer.Option(
        None,
        "--tunnel-failover",
        help=(
            "Passive tunnel name to fail over to. Required when more than two enabled tunnels "
            "exist in the config."
        ),
    ),
    local_config_file: Path = typer.Option(
        None,
        "--local-config-file",
        "-c",
        help="Path to local config file",
        show_default="nebius-vpngw.config.yaml in current directory",
    ),
) -> None:
    """Manually fail over traffic to a passive tunnel by disabling the active BGP neighbor."""
    try:
        config_path = _resolve_local_config(
            local_config_file, create_if_missing=False, exit_after_create=False
        )
        if not config_path:
            raise typer.Exit(code=1)

        print(f"[bold]Loading config from:[/bold] {config_path}")
        local_cfg = load_local_config(config_path)

        gateway = local_cfg.get("gateway") or {}
        local_asn = gateway.get("local_asn")
        if not local_asn:
            print("[red]gateway.local_asn is required for BGP failover.[/red]")
            raise typer.Exit(code=1)

        defaults_mode = (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode") or "bgp"

        enabled_tunnels: list[dict[str, object]] = []
        for conn in local_cfg.get("connections") or []:
            conn_mode = (conn.get("routing_mode") or defaults_mode) or "bgp"
            for tun in conn.get("tunnels") or []:
                ha_role = (tun.get("ha_role") or "active").lower()
                if ha_role == "disable":
                    continue
                enabled_tunnels.append(
                    {
                        "name": tun.get("name"),
                        "ha_role": ha_role,
                        "conn_name": conn.get("name"),
                        "conn_mode": conn_mode,
                        "instance_index": int(tun.get("gateway_instance_index", 0) or 0),
                        "inner_remote_ip": tun.get("inner_remote_ip")
                        or (tun.get("bgp", {}) or {}).get("remote_ip"),
                        "inner_local_ip": tun.get("inner_local_ip")
                        or (tun.get("bgp", {}) or {}).get("local_ip"),
                    }
                )

        if not enabled_tunnels:
            print("[red]No enabled tunnels found in config.[/red]")
            raise typer.Exit(code=1)

        target = None
        if tunnel_name:
            for tun in enabled_tunnels:
                if tun.get("name") == tunnel_name:
                    target = tun
                    break
            if not target:
                names = sorted(t.get("name") or "" for t in enabled_tunnels)
                print(
                    f"[red]Tunnel '{tunnel_name}' not found. Available: {', '.join(n for n in names if n)}[/red]"
                )
                raise typer.Exit(code=1)
        else:
            if len(enabled_tunnels) != 2:
                print(
                    "[red]Multiple tunnels found. Use --tunnel-failover <tunnel-name> to select a passive tunnel.[/red]"
                )
                raise typer.Exit(code=1)
            passives = [t for t in enabled_tunnels if t.get("ha_role") == "passive"]
            if len(passives) != 1:
                print(
                    "[red]Expected exactly one passive tunnel. Check ha_role settings in your config.[/red]"
                )
                raise typer.Exit(code=1)
            target = passives[0]

        if (target.get("ha_role") or "").lower() != "passive":
            print("[red]Selected tunnel is not passive. Choose a passive tunnel for failover.[/red]")
            raise typer.Exit(code=1)

        if (target.get("conn_mode") or "").lower() != "bgp":
            print("[red]Manual failover is only supported for BGP routing mode.[/red]")
            raise typer.Exit(code=1)

        conn_name = target.get("conn_name") or "unknown"
        instance_index = int(target.get("instance_index") or 0)

        active = None
        for tun in enabled_tunnels:
            if (
                tun.get("conn_name") == conn_name
                and int(tun.get("instance_index") or 0) == instance_index
                and tun.get("ha_role") == "active"
            ):
                active = tun
                break

        if not active:
            print("[red]No active tunnel found for the selected connection/instance.[/red]")
            raise typer.Exit(code=1)

        active_peer_ip = active.get("inner_remote_ip")
        if not active_peer_ip:
            print("[red]Active tunnel missing inner_remote_ip; cannot fail over.[/red]")
            raise typer.Exit(code=1)
        passive_peer_ip = target.get("inner_remote_ip")
        if not passive_peer_ip:
            print("[red]Passive tunnel missing inner_remote_ip; cannot fail over.[/red]")
            raise typer.Exit(code=1)

        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
        target_instance = None
        for inst in plan.per_instance:
            if inst.instance_index == instance_index:
                target_instance = inst
                break

        if not target_instance or not target_instance.external_ip:
            print("[red]Could not resolve gateway VM IP for failover.[/red]")
            raise typer.Exit(code=1)

        gateway_group = local_cfg.get("gateway_group", {})
        vm_spec = gateway_group.get("vm_spec", {})
        username = vm_spec.get("ssh_username", os.environ.get("VPNGW_SSH_USER", "ubuntu"))
        key_path_str = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
        key_path = Path(key_path_str).expanduser() if key_path_str else None

        print(
            f"[bold]Failing over connection '{conn_name}' on {target_instance.hostname}:[/bold] "
            f"{active.get('name')} → {target.get('name')}"
        )

        cmd = (
            f"sudo vtysh -c 'configure terminal' -c 'router bgp {local_asn}' "
            f"-c 'neighbor {active_peer_ip} shutdown'"
        )
        ssh_cmd = ["ssh"]
        if key_path:
            ssh_cmd.extend(["-i", str(key_path)])
        ssh_cmd.extend(
            [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "ConnectTimeout=10",
                f"{username}@{target_instance.external_ip}",
                cmd,
            ]
        )

        import subprocess

        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            print(f"[red]Failover command failed: {err}[/red]")
            raise typer.Exit(code=1)

        ssh_base = ["ssh"]
        if key_path:
            ssh_base.extend(["-i", str(key_path)])
        ssh_base.extend(
            [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "ConnectTimeout=10",
            ]
        )
        ssh_target = f"{username}@{target_instance.external_ip}"

        def _fetch_bgp_states() -> dict[str, str]:
            import json

            summary_cmd = "sudo vtysh -c 'show bgp ipv4 unicast summary json'"
            result = subprocess.run(
                ssh_base + [ssh_target, summary_cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                try:
                    data = json.loads(result.stdout)
                    peers = (data.get("ipv4Unicast") or {}).get("peers") or data.get("peers") or {}
                    states: dict[str, str] = {}
                    for ip, info in peers.items():
                        state = (
                            info.get("state")
                            or info.get("state_name")
                            or info.get("stateName")
                            or info.get("peerState")
                            or info.get("bgpState")
                        )
                        if state:
                            states[ip] = str(state)
                    if states:
                        return states
                except json.JSONDecodeError:
                    pass

            text_cmd = "sudo vtysh -c 'show bgp summary'"
            result = subprocess.run(
                ssh_base + [ssh_target, text_cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            states: dict[str, str] = {}
            if result.returncode == 0 and result.stdout:
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] and "." in parts[0]:
                        octets = parts[0].split(".")
                        if len(octets) == 4 and all(
                            o.isdigit() and 0 <= int(o) <= 255 for o in octets
                        ):
                            state = parts[-1]
                            if state.isdigit():
                                state = "Established"
                            states[parts[0]] = state
            return states

        start = time.monotonic()
        timeout_seconds = 30
        active_state = "-"
        passive_state = "-"
        confirmed = False
        while time.monotonic() - start < timeout_seconds:
            states = _fetch_bgp_states()
            active_state = states.get(str(active_peer_ip), "-")
            passive_state = states.get(str(passive_peer_ip), "-")
            if (
                str(passive_state).strip().lower() == "established"
                and str(active_state).strip().lower() != "established"
            ):
                confirmed = True
                break
            time.sleep(1)

        elapsed = time.monotonic() - start
        if confirmed:
            print(
                "[green]✓ Failover confirmed.[/green] "
                f"{active.get('name')} BGP={active_state} "
                f"{target.get('name')} BGP={passive_state} "
                f"(elapsed {elapsed:.1f}s)"
            )
        else:
            print(
                "[yellow]⚠ Failover triggered but not confirmed within timeout.[/yellow] "
                f"{active.get('name')} BGP={active_state} "
                f"{target.get('name')} BGP={passive_state} "
                f"(elapsed {elapsed:.1f}s)"
            )
            print("[dim]Run 'nebius-vpngw status' to verify current states.[/dim]")

    except typer.Exit:
        raise
    except Exception as e:
        print(f"[red]Error during failover: {e}[/red]")
        import traceback

        print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(code=1) from e


@app.command(name="failback")
def tunnel_failback(
    tunnel_name: str | None = typer.Option(
        None,
        "--tunnel-failback",
        help=(
            "Active tunnel name to restore. Required when multiple active tunnels exist in the "
            "config."
        ),
    ),
    local_config_file: Path = typer.Option(
        None,
        "--local-config-file",
        "-c",
        help="Path to local config file",
        show_default="nebius-vpngw.config.yaml in current directory",
    ),
) -> None:
    """Restore traffic to the active tunnel by re-enabling its BGP neighbor."""
    try:
        config_path = _resolve_local_config(
            local_config_file, create_if_missing=False, exit_after_create=False
        )
        if not config_path:
            raise typer.Exit(code=1)

        print(f"[bold]Loading config from:[/bold] {config_path}")
        local_cfg = load_local_config(config_path)

        gateway = local_cfg.get("gateway") or {}
        local_asn = gateway.get("local_asn")
        if not local_asn:
            print("[red]gateway.local_asn is required for BGP failback.[/red]")
            raise typer.Exit(code=1)

        defaults_mode = (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode") or "bgp"

        enabled_tunnels: list[dict[str, object]] = []
        for conn in local_cfg.get("connections") or []:
            conn_mode = (conn.get("routing_mode") or defaults_mode) or "bgp"
            for tun in conn.get("tunnels") or []:
                ha_role = (tun.get("ha_role") or "active").lower()
                if ha_role == "disable":
                    continue
                enabled_tunnels.append(
                    {
                        "name": tun.get("name"),
                        "ha_role": ha_role,
                        "conn_name": conn.get("name"),
                        "conn_mode": conn_mode,
                        "instance_index": int(tun.get("gateway_instance_index", 0) or 0),
                        "inner_remote_ip": tun.get("inner_remote_ip")
                        or (tun.get("bgp", {}) or {}).get("remote_ip"),
                    }
                )

        active_tunnels = [t for t in enabled_tunnels if t.get("ha_role") == "active"]
        if not active_tunnels:
            print("[red]No active tunnels found in config.[/red]")
            raise typer.Exit(code=1)

        target = None
        if tunnel_name:
            for tun in active_tunnels:
                if tun.get("name") == tunnel_name:
                    target = tun
                    break
            if not target:
                names = sorted(t.get("name") or "" for t in active_tunnels)
                print(
                    f"[red]Active tunnel '{tunnel_name}' not found. Available: {', '.join(n for n in names if n)}[/red]"
                )
                raise typer.Exit(code=1)
        else:
            if len(active_tunnels) != 1:
                print(
                    "[red]Multiple active tunnels found. Use --tunnel-failback <tunnel-name> to select one.[/red]"
                )
                raise typer.Exit(code=1)
            target = active_tunnels[0]

        if (target.get("conn_mode") or "").lower() != "bgp":
            print("[red]Manual failback is only supported for BGP routing mode.[/red]")
            raise typer.Exit(code=1)

        active_peer_ip = target.get("inner_remote_ip")
        if not active_peer_ip:
            print("[red]Active tunnel missing inner_remote_ip; cannot fail back.[/red]")
            raise typer.Exit(code=1)

        conn_name = target.get("conn_name") or "unknown"
        instance_index = int(target.get("instance_index") or 0)

        plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, [])
        target_instance = None
        for inst in plan.per_instance:
            if inst.instance_index == instance_index:
                target_instance = inst
                break

        if not target_instance or not target_instance.external_ip:
            print("[red]Could not resolve gateway VM IP for failback.[/red]")
            raise typer.Exit(code=1)

        gateway_group = local_cfg.get("gateway_group", {})
        vm_spec = gateway_group.get("vm_spec", {})
        username = vm_spec.get("ssh_username", os.environ.get("VPNGW_SSH_USER", "ubuntu"))
        key_path_str = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")
        key_path = Path(key_path_str).expanduser() if key_path_str else None

        print(
            f"[bold]Failing back connection '{conn_name}' on {target_instance.hostname}:[/bold] "
            f"restore {target.get('name')}"
        )

        cmd = (
            f"sudo vtysh -c 'configure terminal' -c 'router bgp {local_asn}' "
            f"-c 'no neighbor {active_peer_ip} shutdown'"
        )
        ssh_cmd = ["ssh"]
        if key_path:
            ssh_cmd.extend(["-i", str(key_path)])
        ssh_cmd.extend(
            [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "ConnectTimeout=10",
                f"{username}@{target_instance.external_ip}",
                cmd,
            ]
        )

        import subprocess

        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            print(f"[red]Failback command failed: {err}[/red]")
            raise typer.Exit(code=1)

        ssh_base = ["ssh"]
        if key_path:
            ssh_base.extend(["-i", str(key_path)])
        ssh_base.extend(
            [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "ConnectTimeout=10",
            ]
        )
        ssh_target = f"{username}@{target_instance.external_ip}"

        def _fetch_bgp_states() -> dict[str, str]:
            import json

            summary_cmd = "sudo vtysh -c 'show bgp ipv4 unicast summary json'"
            result = subprocess.run(
                ssh_base + [ssh_target, summary_cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                try:
                    data = json.loads(result.stdout)
                    peers = (data.get("ipv4Unicast") or {}).get("peers") or data.get("peers") or {}
                    states: dict[str, str] = {}
                    for ip, info in peers.items():
                        state = (
                            info.get("state")
                            or info.get("state_name")
                            or info.get("stateName")
                            or info.get("peerState")
                            or info.get("bgpState")
                        )
                        if state:
                            states[ip] = str(state)
                    if states:
                        return states
                except json.JSONDecodeError:
                    pass

            text_cmd = "sudo vtysh -c 'show bgp summary'"
            result = subprocess.run(
                ssh_base + [ssh_target, text_cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            states: dict[str, str] = {}
            if result.returncode == 0 and result.stdout:
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] and "." in parts[0]:
                        octets = parts[0].split(".")
                        if len(octets) == 4 and all(
                            o.isdigit() and 0 <= int(o) <= 255 for o in octets
                        ):
                            state = parts[-1]
                            if state.isdigit():
                                state = "Established"
                            states[parts[0]] = state
            return states

        start = time.monotonic()
        timeout_seconds = 30
        active_state = "-"
        confirmed = False
        while time.monotonic() - start < timeout_seconds:
            states = _fetch_bgp_states()
            active_state = states.get(str(active_peer_ip), "-")
            if str(active_state).strip().lower() == "established":
                confirmed = True
                break
            time.sleep(1)

        elapsed = time.monotonic() - start
        if confirmed:
            print(
                "[green]✓ Failback confirmed.[/green] "
                f"{target.get('name')} BGP={active_state} (elapsed {elapsed:.1f}s)"
            )
        else:
            print(
                "[yellow]⚠ Failback triggered but not confirmed within timeout.[/yellow] "
                f"{target.get('name')} BGP={active_state} (elapsed {elapsed:.1f}s)"
            )
            print("[dim]Run 'nebius-vpngw status' to verify current states.[/dim]")

    except typer.Exit:
        raise
    except Exception as e:
        print(f"[red]Error during failback: {e}[/red]")
        import traceback

        print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(code=1) from e


# init_config command removed; auto-creation occurs on first run without --local-config-file


def main():  # console script entry point
    try:
        app()
    except Exception as e:
        print(f"[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
