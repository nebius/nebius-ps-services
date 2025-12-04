import sys
import os
import typing as t
from pathlib import Path
import shutil
import importlib.resources as resources

import typer
from rich import print

from .config_loader import load_local_config, merge_with_peer_configs, ResolvedDeploymentPlan
from .deploy.vm_manager import VMManager
from .deploy.ssh_push import SSHPush
from .deploy.route_manager import RouteManager

app = typer.Typer(add_completion=False, help="Nebius VM-based VPN Gateway orchestrator")


@app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    local_config_file: t.Optional[Path] = typer.Option(None, exists=True, readable=True, help="Path to nebius-vpngw-config.yaml"),
    peer_config_file: t.List[Path] = typer.Option([], exists=True, readable=True, help="Vendor peer config file(s)"),
    recreate_gw: bool = typer.Option(False, help="Delete and recreate gateway VMs before applying"),
    sa: t.Optional[str] = typer.Option(None, help="If provided, ensure a Service Account with this name and use it for auth"),
    project_id: t.Optional[str] = typer.Option(None, help="Nebius project/folder identifier"),
    zone: t.Optional[str] = typer.Option(None, help="Nebius zone for gateway VMs"),
    dry_run: bool = typer.Option(False, help="Render actions without applying"),
):
    """Default action: behaves like `apply` if no subcommand is provided."""
    if ctx.invoked_subcommand is None:
        # If no local config provided, check for default in CWD and auto-create from template if missing.
        if local_config_file is None:
            default_path = Path.cwd() / "nebius-vpngw-config.yaml"
            if not default_path.exists():
                try:
                    template_rel = "nebius-vpngw-config-template.yaml"
                    with resources.as_file(resources.files("nebius_vpngw").joinpath(template_rel)) as tpl_path:
                        shutil.copyfile(tpl_path, default_path)
                    print(f"[green]Created default config at[/green] {default_path}")
                    print("[bold]Please edit the file to fill environment-specific values and secrets, then re-run.[/bold]")
                    raise typer.Exit(code=0)
                except Exception as e:
                    print(f"[red]Failed to create default config:[/red] {e}")
                    raise typer.Exit(code=1)
            local_config_file = default_path
        return apply(
            local_config_file=local_config_file,
            peer_config_file=peer_config_file,
            recreate_gw=recreate_gw,
            sa=sa,
            project_id=project_id,
            zone=zone,
            dry_run=dry_run,
        )


@app.command()
def apply(
    local_config_file: Path = typer.Option(..., exists=True, readable=True, help="Path to nebius-vpngw-config.yaml"),
    peer_config_file: t.List[Path] = typer.Option([], exists=True, readable=True, help="Vendor peer config file(s)"),
    recreate_gw: bool = typer.Option(False, help="Delete and recreate gateway VMs before applying"),
    sa: t.Optional[str] = typer.Option(None, help="If provided, ensure a Service Account with this name and use it for auth"),
    project_id: t.Optional[str] = typer.Option(None, help="Nebius project/folder identifier"),
    zone: t.Optional[str] = typer.Option(None, help="Nebius zone for gateway VMs"),
    dry_run: bool = typer.Option(False, help="Render actions without applying"),
):
    """Apply desired state to Nebius: create/update gateway VMs and push config."""
    print("[bold]Loading local YAML config...[/bold]")
    local_cfg = load_local_config(local_config_file)

    print("[bold]Parsing peer configs...[/bold]")
    plan: ResolvedDeploymentPlan = merge_with_peer_configs(local_cfg, peer_config_file)

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
                print("[yellow]Service Account flow returned no token; falling back to CLI config.[/yellow]")
        except Exception as e:
            print(f"[yellow]Service Account setup skipped due to error:[/yellow] {e}")
    else:
        # No SA requested; if NEBIUS_IAM_TOKEN is missing, try to read it from CLI config
        if not os.environ.get("NEBIUS_IAM_TOKEN"):
            try:
                from .vpngw_sa import ensure_cli_access_token
                tok = ensure_cli_access_token()
                if tok:
                    os.environ["NEBIUS_IAM_TOKEN"] = tok
                    print("[green]Using IAM token from Nebius CLI (auto-fetched).[/green]")
                else:
                    print("[yellow]No IAM token found; SDK will use Nebius CLI profile if configured.[/yellow]")
            except Exception:
                print("[yellow]Unable to obtain IAM token from CLI; proceeding without NEBIUS_IAM_TOKEN.[/yellow]")

    vm_mgr = VMManager(project_id=proj_id, zone=zone or plan.gateway_group.region, auth_token=auth_token, tenant_id=tenant_id, region_id=region_id)
    ssh = SSHPush()
    routes = RouteManager(project_id=proj_id)

    # Check for destructive changes BEFORE making any changes
    print("[bold]Analyzing configuration changes...[/bold]")
    changes = vm_mgr.check_changes(plan.gateway_group)
    
    has_destructive = False
    has_safe = False
    has_no_change = True
    
    for inst_name, diff in changes:
        if diff.requires_recreation():
            has_destructive = True
            has_no_change = False
            print(f"[red]{inst_name}:[/red]")
            print(diff.format_warning())
        elif diff.has_changes():
            has_safe = True
            has_no_change = False
            print(f"[yellow]{inst_name}:[/yellow]")
            print(diff.format_warning())
        else:
            print(f"[green]{inst_name}: No infrastructure changes[/green]")
    
    # If destructive changes detected and --recreate-gw not provided, abort
    if has_destructive and not recreate_gw:
        print("\n[red]⚠️  ERROR: Destructive changes require VM recreation[/red]")
        print("[yellow]To proceed with VM recreation, run:[/yellow]")
        print(f"  nebius-vpngw apply --recreate-gw")
        raise typer.Exit(code=1)
    
    # Warn if --recreate-gw provided but no changes detected (unnecessary recreation)
    if has_no_change and recreate_gw:
        print("\n[yellow]⚠️  WARNING: No configuration changes detected[/yellow]")
        print("[yellow]VM recreation will use identical specifications (unnecessary downtime).[/yellow]")
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
        print("\n[yellow]Proceeding with VM recreation for safe changes (--recreate-gw flag provided)...[/yellow]")

    print("[bold]Ensuring gateway VMs exist...[/bold]")
    vm_ips = vm_mgr.ensure_group(plan.gateway_group, recreate=recreate_gw)

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
                if health['cloud_init_complete'] and health['strongswan_installed'] and health['frr_installed']:
                    print(f"[green]{vm_name} ({vm_ip}): {health['message']}[/green]")
                elif health['reachable']:
                    print(f"[yellow]{vm_name} ({vm_ip}): {health['message']}[/yellow]")
                    all_healthy = False
                else:
                    print(f"[red]{vm_name} ({vm_ip}): {health['message']}[/red]")
                    all_healthy = False
            
            # If VMs are not fully healthy (e.g., SSH not ready), wait additional time
            if not all_healthy:
                import time
                print("[yellow]Waiting for SSH to become available...[/yellow]")
                max_ssh_wait = 120  # Wait up to 2 minutes for SSH
                ssh_wait_interval = 10
                for attempt in range(max_ssh_wait // ssh_wait_interval):
                    time.sleep(ssh_wait_interval)
                    all_ssh_ready = True
                    for vm_name, vm_ip in vm_ips.items():
                        health = vm_mgr.check_vm_health(vm_name, vm_ip)
                        if not health['reachable']:
                            all_ssh_ready = False
                            break
                    if all_ssh_ready:
                        print(f"[green]✓ All VMs SSH ready (waited {(attempt + 1) * ssh_wait_interval}s)[/green]")
                        break
                    print(f"[dim]SSH not ready, waiting... ({(attempt + 1) * ssh_wait_interval}s elapsed)[/dim]")
                else:
                    print("[yellow]Warning: SSH did not become ready within timeout, attempting config push anyway...[/yellow]")
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
                print(f"[dim]Skipping config push for {inst_cfg.hostname}: No IP address available[/dim]")
                continue
        ssh.push_config_and_reload(target, inst_cfg, local_cfg)

    if plan.should_manage_routes:
        print("[bold]Reconciling VPC routes...[/bold]")
        routes.reconcile(plan)

    print("[green]Apply completed successfully.[/green]")


# init_config command removed; auto-creation occurs on first run without --local-config-file


def main():  # console script entry point
    try:
        app()
    except Exception as e:
        print(f"[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
