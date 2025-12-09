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

DEFAULT_CONFIG_FILENAME = "nebius-vpngw-config.config.yaml"
DEFAULT_TEMPLATE_FILENAME = "nebius-vpngw-config-template.config.yaml"

app = typer.Typer(
    add_completion=False,
    help="""
Nebius VM-based VPN Gateway orchestrator

By default, the CLI looks for 'nebius-vpngw-config.config.yaml' in your current directory.
Use --local-config-file to specify a different config file if needed.
"""
)


def _resolve_local_config(
    local_config_file: t.Optional[Path],
    *,
    create_if_missing: bool,
    exit_after_create: bool,
) -> Path:
    """Resolve config path, optionally creating a template and exiting."""
    if local_config_file is not None:
        return local_config_file

    default_path = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if default_path.exists():
        return default_path

    if not create_if_missing:
        print(f"[red]Error: Config file not found at {default_path}[/red]")
        print("[yellow]Run 'nebius-vpngw' first to create a template config.[/yellow]")
        raise typer.Exit(code=1)

    try:
        with resources.as_file(resources.files("nebius_vpngw").joinpath(DEFAULT_TEMPLATE_FILENAME)) as tpl_path:
            shutil.copyfile(tpl_path, default_path)
        print(f"[green]Created default config at[/green] {default_path}")
        print("[bold]Please edit the file to fill environment-specific values and secrets, then re-run.[/bold]")
    except Exception as e:
        print(f"[red]Failed to create default config:[/red] {e}")
        raise typer.Exit(code=1)

    if exit_after_create:
        raise typer.Exit(code=0)

    return default_path


@app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    local_config_file: t.Optional[Path] = typer.Option(None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"),
    project_id: t.Optional[str] = typer.Option(None, help="Nebius project/folder identifier"),
    zone: t.Optional[str] = typer.Option(None, help="Nebius zone for gateway VMs"),
):
    """Default action: shows status if config exists, creates template if not."""
    if ctx.invoked_subcommand is None:
        local_config_file = _resolve_local_config(
            local_config_file,
            create_if_missing=True,
            exit_after_create=True,
        )
        # Config exists - show status by default
        return status(
            local_config_file=local_config_file,
            project_id=project_id,
            zone=zone,
        )


@app.command()
def apply(
    local_config_file: t.Optional[Path] = typer.Option(None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"),
    peer_config_file: t.List[Path] = typer.Option([], exists=True, readable=True, help="Vendor peer config file(s)"),
    recreate_gw: bool = typer.Option(False, help="Delete and recreate gateway VMs before applying"),
    sa: t.Optional[str] = typer.Option(None, help="If provided, ensure a Service Account with this name and use it for auth"),
    project_id: t.Optional[str] = typer.Option(None, help="Nebius project/folder identifier"),
    zone: t.Optional[str] = typer.Option(None, help="Nebius zone for gateway VMs"),
    dry_run: bool = typer.Option(False, help="Render actions without applying"),
    add_route: bool = typer.Option(False, help="(Experimental) Ensure VPC routes to VPN gateway for remote_prefixes"),
    list_route: bool = typer.Option(False, help="List VPC routes for subnets matching gateway.local_prefixes"),
):
    """Apply desired state to Nebius: create/update gateway VMs and push config."""
    local_config_file = _resolve_local_config(
        local_config_file,
        create_if_missing=True,
        exit_after_create=True,
    )

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
    routes = RouteManager(project_id=proj_id, auth_token=auth_token)

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

    if list_route:
        print("[bold]Listing VPC routes for local_prefixes...[/bold]")
        routes.list_routes(plan, local_cfg)
    if add_route:
        print("[bold]Ensuring VPC routes to VPN gateway for remote_prefixes...[/bold]")
        routes.add_routes(plan, local_cfg)
    elif plan.should_manage_routes:
        print("[bold]Reconciling VPC routes...[/bold]")
        routes.reconcile(plan)

    print("[green]Apply completed successfully.[/green]")


@app.command()
def status(
    local_config_file: t.Optional[Path] = typer.Option(None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"),
    project_id: t.Optional[str] = typer.Option(None, help="Nebius project/folder identifier"),
    zone: t.Optional[str] = typer.Option(None, help="Nebius zone for gateway VMs"),
):
    """Show status of VPN tunnels and gateway health."""
    from rich.console import Console
    from rich.table import Table
    import subprocess
    import re
    import json
    
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
    auth_token = None
    if not os.environ.get("NEBIUS_IAM_TOKEN"):
        try:
            from .vpngw_sa import ensure_cli_access_token
            tok = ensure_cli_access_token()
            if tok:
                os.environ["NEBIUS_IAM_TOKEN"] = tok
        except Exception:
            pass
    
    vm_mgr = VMManager(project_id=proj_id, zone=zone or plan.gateway_group.region, auth_token=auth_token, tenant_id=tenant_id, region_id=region_id)
    
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
    table.add_column("Gateway VM", style="white")
    table.add_column("Status", style="white")
    table.add_column("BGP", style="white")
    table.add_column("Peer IP", style="white")
    table.add_column("Encryption", style="white")
    table.add_column("Uptime", style="white")

    # Build mapping of tunnel -> BGP peer IP per instance (for BGP status lookup)
    tunnel_bgp_map: dict[str, dict[str, str]] = {}
    defaults_mode = (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode") or "bgp"
    for conn in (local_cfg.get("connections") or []):
        conn_mode = (conn.get("routing_mode") or defaults_mode) or "bgp"
        if conn_mode != "bgp":
            continue
        for tun in (conn.get("tunnels") or []):
            try:
                inst_idx = int(tun.get("gateway_instance_index", 0))
            except Exception:
                inst_idx = 0
            hostname = f"{plan.gateway_group.name}-{inst_idx}"
            tunnel_bgp_map.setdefault(hostname, {})
            peer_ip = tun.get("inner_remote_ip")
            if peer_ip:
                tunnel_bgp_map[hostname][tun.get("name") or f"tunnel{inst_idx}"] = str(peer_ip)
    
    # Check each gateway VM's tunnels
    for inst_cfg in plan.iter_instance_configs():
        target = vm_ips.get(inst_cfg.hostname)
        if not target:
            continue

        # Pull BGP neighbor states (if any BGP tunnels on this instance)
        bgp_states: dict[str, str] = {}
        if tunnel_bgp_map.get(inst_cfg.hostname):
            try:
                bgp_out = subprocess.run(
                    ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", f"ubuntu@{target}",
                     "sudo vtysh -c 'show bgp ipv4 unicast summary json'"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if bgp_out.returncode == 0 and bgp_out.stdout:
                    data = json.loads(bgp_out.stdout)
                    peers = (data.get("ipv4Unicast") or {}).get("peers") or {}
                    for ip, info in peers.items():
                        state = info.get("state") or info.get("state_name") or info.get("stateName")
                        if state:
                            bgp_states[ip] = state
            except Exception:
                pass
        
        # Run ipsec status command
        try:
            result = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", f"ubuntu@{target}", "sudo ipsec statusall"],
                capture_output=True,
                text=True,
                timeout=15
            )
            
            if result.returncode != 0:
                table.add_row(
                    "All tunnels",
                    inst_cfg.hostname,
                    "[red]ERROR[/red]",
                    "-",
                    "-",
                    "-",
                    f"Failed to get status: {result.stderr.strip()}"
                )
                continue
            
            output = result.stdout
            
            # Parse IPsec status output
            # Look for patterns like: "gcp-classic-tunnel-0[202]: ESTABLISHED 8 minutes ago, 10.48.0.13[10.48.0.13]...34.155.169.244[34.155.169.244]"
            tunnel_pattern = re.compile(r'(\S+)\[\d+\]:\s+(\w+)\s+(.+?),\s+[\d.]+\[[\d.]+\]\.\.\.(\d+\.\d+\.\d+\.\d+)\[')


            
            tunnels = {}
            for match in tunnel_pattern.finditer(output):
                tunnel_name = match.group(1)
                status = match.group(2)
                uptime = match.group(3)
                peer_ip = match.group(4)
                tunnels[tunnel_name] = {
                    'status': status,
                    'uptime': uptime,
                    'peer_ip': peer_ip,
                    'encryption': 'Unknown',
                    'bgp': '-',
                }
            
            # Parse encryption from IKE proposal lines
            # Pattern: "IKE proposal: AES_GCM_16_128/PRF_AES128_XCBC/MODP_2048"
            ike_pattern = re.compile(r'(\S+)\[\d+\]:.*?IKE proposal:\s+(\S+)')
            for match in ike_pattern.finditer(output):
                tunnel_name = match.group(1)
                encryption = match.group(2)
                if tunnel_name in tunnels:
                    tunnels[tunnel_name]['encryption'] = encryption

            # Fallback: parse simplified connection lines if no SAs matched yet
            # Example: "gcp-ha-tunnel-1:  %any...34.157.15.187  IKEv2, dpddelay=30s"
            if not tunnels:
                conn_line_pattern = re.compile(r'^(\S+):\s+%any\.\.\.(\d+\.\d+\.\d+\.\d+)', re.MULTILINE)
                for match in conn_line_pattern.finditer(output):
                    tunnels[match.group(1)] = {
                        'status': 'CONNECTING',
                        'uptime': '-',
                        'peer_ip': match.group(2),
                        'encryption': 'Unknown',
                        'bgp': '-',
                    }

            # Attach BGP states where we know the peer IP from config
            for tname, tinfo in tunnels.items():
                peer_cfg_ip = tunnel_bgp_map.get(inst_cfg.hostname, {}).get(tname)
                if peer_cfg_ip and peer_cfg_ip in bgp_states:
                    tinfo['bgp'] = bgp_states[peer_cfg_ip]
            
            # Add rows to table
            if tunnels:
                for tunnel_name, info in tunnels.items():
                    status_text = info['status']
                    if status_text == "ESTABLISHED":
                        status_display = "[green]ESTABLISHED[/green]"
                    elif status_text == "CONNECTING":
                        status_display = "[yellow]CONNECTING[/yellow]"
                    else:
                        status_display = f"[red]{status_text}[/red]"
                    
                    table.add_row(
                        tunnel_name,
                        inst_cfg.hostname,
                        status_display,
                        info.get('bgp', '-'),
                        info['peer_ip'],
                        info['encryption'],
                        info['uptime']
                    )
            else:
                # No tunnels found in output
                if "no matching" in output.lower() or "no active" in output.lower():
                    table.add_row(
                        "No tunnels",
                        inst_cfg.hostname,
                        "[yellow]NONE[/yellow]",
                        "-",
                        "-",
                        "-",
                        "-"
                    )
                else:
                    table.add_row(
                        "Unknown",
                        inst_cfg.hostname,
                        "[red]PARSE ERROR[/red]",
                        "-",
                        "-",
                        "-",
                        "Could not parse ipsec output"
                    )
                    # Show a trimmed snippet to aid debugging
                    snippet = "\n".join(output.splitlines()[:20])
                    print(f"[yellow]{inst_cfg.hostname} ipsec status output (first lines):[/yellow]\n{snippet}\n")
        
        except subprocess.TimeoutExpired:
            table.add_row(
                "All tunnels",
                inst_cfg.hostname,
                "[red]TIMEOUT[/red]",
                "-",
                "-",
                "-",
                "SSH command timed out"
            )
        except Exception as e:
            table.add_row(
                "All tunnels",
                inst_cfg.hostname,
                "[red]ERROR[/red]",
                "-",
                "-",
                "-",
                str(e)
            )
    
    console.print(table)
    
    # Show service health
    print("\n[bold]Checking system services...[/bold]")
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
            'nebius-vpngw-agent': 'Unknown',
            'strongswan': 'Unknown',  # Check process, not systemd service
            'frr': 'Unknown'
        }
        
        for service_name in services.keys():
            try:
                # Special handling for strongSwan - check if charon daemon is running
                if service_name == 'strongswan':
                    result = subprocess.run(
                        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", f"ubuntu@{target}", 
                         "pgrep -x charon >/dev/null && echo active || echo inactive"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        shell=False
                    )
                else:
                    result = subprocess.run(
                        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", f"ubuntu@{target}", 
                         f"systemctl is-active {service_name}"],
                        capture_output=True,
                        text=True,
                        timeout=10
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
                            ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", f"ubuntu@{target}", detail_cmd],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            shell=False,
                        )
                        snippet = (detail.stdout or detail.stderr or "").strip()
                        if snippet:
                            print(f"[yellow]{inst_cfg.hostname} {service_name} status:[/yellow]\n{snippet}\n")
                    except Exception:
                        pass
            
            except Exception:
                services[service_name] = "[red]error[/red]"
        
        service_table.add_row(
            inst_cfg.hostname,
            services['nebius-vpngw-agent'],
            services['strongswan'],
            services['frr']
        )
    
    console.print(service_table)


# init_config command removed; auto-creation occurs on first run without --local-config-file


def main():  # console script entry point
    try:
        app()
    except Exception as e:
        print(f"[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
