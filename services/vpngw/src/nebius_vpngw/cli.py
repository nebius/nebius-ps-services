import sys
import typing as t
from pathlib import Path

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
    project_id: t.Optional[str] = typer.Option(None, help="Nebius project/folder identifier"),
    zone: t.Optional[str] = typer.Option(None, help="Nebius zone for gateway VMs"),
    dry_run: bool = typer.Option(False, help="Render actions without applying"),
):
    """Default action: behaves like `apply` if no subcommand is provided."""
    if ctx.invoked_subcommand is None:
        if local_config_file is None:
            typer.echo("Error: --local-config-file is required")
            raise typer.Exit(code=2)
        return apply(
            local_config_file=local_config_file,
            peer_config_file=peer_config_file,
            recreate_gw=recreate_gw,
            project_id=project_id,
            zone=zone,
            dry_run=dry_run,
        )


@app.command()
def apply(
    local_config_file: Path = typer.Option(..., exists=True, readable=True, help="Path to nebius-vpngw-config.yaml"),
    peer_config_file: t.List[Path] = typer.Option([], exists=True, readable=True, help="Vendor peer config file(s)"),
    recreate_gw: bool = typer.Option(False, help="Delete and recreate gateway VMs before applying"),
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
        raise typer.Exit(code=0)

    vm_mgr = VMManager(project_id=project_id, zone=zone)
    ssh = SSHPush()
    routes = RouteManager(project_id=project_id)

    print("[bold]Ensuring gateway VMs exist...[/bold]")
    vm_mgr.ensure_group(plan.gateway_group, recreate=recreate_gw)

    print("[bold]Pushing per-VM resolved configs and reloading agent...[/bold]")
    for inst_cfg in plan.iter_instance_configs():
        target = vm_mgr.get_instance_ssh_target(inst_cfg.instance_index)
        ssh.push_config_and_reload(target, inst_cfg)

    if plan.should_manage_routes:
        print("[bold]Reconciling VPC routes...[/bold]")
        routes.reconcile(plan)

    print("[green]Apply completed successfully.[/green]")


def main():  # console script entry point
    try:
        app()
    except Exception as e:
        print(f"[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
