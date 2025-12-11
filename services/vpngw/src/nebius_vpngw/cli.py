import sys
import os
import typing as t
from pathlib import Path
import shutil
import importlib.resources as resources
import re

import typer
from rich import print

from .config_loader import load_local_config, merge_with_peer_configs, ResolvedDeploymentPlan
from .deploy.vm_manager import VMManager
from .deploy.ssh_push import SSHPush
from .deploy.route_manager import RouteManager

DEFAULT_CONFIG_FILENAME = "nebius-vpngw.config.yaml"
DEFAULT_TEMPLATE_FILENAME = "nebius-vpngw-config-template.config.yaml"

app = typer.Typer(
    add_completion=False,
    help="""
Nebius VM-based VPN Gateway orchestrator

By default, the CLI looks for 'nebius-vpngw.config.yaml' in your current directory.
Use --local-config-file to specify a different config file if needed.
"""
)


def _create_minimal_config(template_path: Path, output_path: Path) -> None:
    """Create a minimal config file by stripping comments and blank lines from template."""
    lines = template_path.read_text(encoding="utf-8").splitlines()
    minimal_lines = []
    
    for line in lines:
        # Skip blank lines
        if not line.strip():
            continue
        
        # Skip full-line comments
        if line.strip().startswith("#"):
            continue
        
        # Remove inline comments but keep the content
        # Handle cases like: key: "value"  # comment
        if "#" in line:
            # Find the # that's not inside quotes
            in_quotes = False
            quote_char = None
            for i, char in enumerate(line):
                if char in ('"', "'") and (i == 0 or line[i-1] != "\\"):
                    if not in_quotes:
                        in_quotes = True
                        quote_char = char
                    elif char == quote_char:
                        in_quotes = False
                elif char == "#" and not in_quotes:
                    line = line[:i].rstrip()
                    break
        
        # Skip if line becomes empty after removing comment
        if not line.strip():
            continue
            
        minimal_lines.append(line)
    
    output_path.write_text("\n".join(minimal_lines) + "\n", encoding="utf-8")


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
            _create_minimal_config(tpl_path, default_path)
        print(f"[green]Created minimal config at[/green] {default_path}")
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
    sa: t.Optional[str] = typer.Option(None, hidden=True, help="If provided, ensure a Service Account with this name and use it for auth"),
    project_id: t.Optional[str] = typer.Option(None, help="Nebius project/folder identifier"),
    zone: t.Optional[str] = typer.Option(None, help="Nebius zone for gateway VMs"),
    dry_run: bool = typer.Option(False, hidden=True, help="Render actions without applying"),
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
def validate_config(
    config_file: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help="Path to configuration file to validate"
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
    from pydantic import ValidationError
    from . import schema
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
        console.print(Panel.fit(
            f"[bold green]✓ Configuration is valid![/bold green]\n\n"
            f"[dim]Summary:[/dim]\n"
            f"  • Gateway instances: {instance_count}\n"
            f"  • Connections: {connections_count}\n"
            f"  • Tunnels: {tunnels_count}\n"
            f"  • Schema version: v{local_cfg.get('version', 1)}",
            title="[green]Validation Passed[/green]",
            border_style="green"
        ))
        console.print()
        console.print("[dim]You can now run 'nebius-vpngw apply' to deploy this configuration.[/dim]")
        
    except ValueError as e:
        # Schema validation errors or missing env vars
        console.print()
        console.print(Panel.fit(
            f"[bold red]✗ Configuration validation failed[/bold red]\n\n"
            f"{str(e)}",
            title="[red]Validation Error[/red]",
            border_style="red"
        ))
        raise typer.Exit(code=1)
    
    except Exception as e:
        # Unexpected errors
        console.print()
        console.print(Panel.fit(
            f"[bold red]✗ Unexpected error during validation[/bold red]\n\n"
            f"{str(e)}",
            title="[red]Error[/red]",
            border_style="red"
        ))
        raise typer.Exit(code=1)


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
    
    # Quick check: verify at least one gateway VM exists before attempting SSH
    print("[bold]Checking for gateway VMs...[/bold]")
    from nebius.api.nebius.compute.v1 import InstanceServiceClient, ListInstancesRequest  # type: ignore
    
    client = vm_mgr._get_client()
    if client and proj_id:
        isc = InstanceServiceClient(client)
        ilist_op = isc.list(ListInstancesRequest(parent_id=proj_id))
        ilist = ilist_op.wait() if hasattr(ilist_op, 'wait') else ilist_op
        
        items = []
        if hasattr(ilist, 'items'):
            items = ilist.items
        elif hasattr(ilist, '__iter__'):
            items = list(ilist)
        
        existing_vms = [
            inst for inst in items
            if getattr(getattr(inst, "metadata", None), "name", "").startswith(f"{plan.gateway_group.name}-")
        ]
        
        if not existing_vms:
            console.print(f"[yellow]No gateway VMs found matching pattern '{plan.gateway_group.name}-*'[/yellow]")
            console.print(f"[yellow]Run 'nebius-vpngw apply' to create gateway VMs first.[/yellow]")
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
                # Try JSON output first
                bgp_out = subprocess.run(
                    ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", f"ubuntu@{target}",
                     "sudo vtysh -c 'show bgp ipv4 unicast summary json'"],
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
                            state = (info.get("state") or 
                                   info.get("state_name") or 
                                   info.get("stateName") or 
                                   info.get("peerState") or
                                   info.get("bgpState"))
                            if state:
                                bgp_states[ip] = state
                    except json.JSONDecodeError:
                        pass
                
                # If JSON parsing didn't work, fall back to text parsing
                if not bgp_states:
                    bgp_out = subprocess.run(
                        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", f"ubuntu@{target}",
                         "sudo vtysh -c 'show bgp summary'"],
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
                            if len(parts) >= 2 and parts[0] and '.' in parts[0]:
                                try:
                                    # Validate it's an IP
                                    octets = parts[0].split('.')
                                    if len(octets) == 4 and all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
                                        # Last column is typically the state
                                        state = parts[-1]
                                        bgp_states[parts[0]] = state
                                except (ValueError, IndexError):
                                    continue
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
                elif bgp_states:
                    # Fallback: if we have BGP states but no exact match, try to match any peer
                    # This handles cases where tunnel name mapping might be off
                    for bgp_ip, bgp_state in bgp_states.items():
                        # Simple heuristic: assign if we don't have a BGP status yet
                        if tinfo.get('bgp') == '-':
                            tinfo['bgp'] = bgp_state
                            break
            
            # Add rows to table
            if tunnels:
                for tunnel_name, info in tunnels.items():
                    status_text = info['status']
                    if status_text == "ESTABLISHED":
                        status_display = "[green]Established[/green]"
                    elif status_text == "CONNECTING":
                        status_display = "[yellow]Connecting[/yellow]"
                    else:
                        status_display = f"[red]{status_text.capitalize()}[/red]"
                    
                    # Format BGP status with colors
                    bgp_status = info.get('bgp', '-')
                    if bgp_status and bgp_status != '-':
                        if bgp_status.lower() == 'established':
                            bgp_display = "[green]Established[/green]"
                        elif bgp_status.lower() in ('idle', 'connect', 'active'):
                            # These are failure states when persistent - show as Down in red
                            bgp_display = f"[red]Down ({bgp_status.capitalize()})[/red]"
                        else:
                            bgp_display = f"[red]{bgp_status}[/red]"
                    else:
                        bgp_display = '-'
                    
                    table.add_row(
                        tunnel_name,
                        inst_cfg.hostname,
                        status_display,
                        bgp_display,
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
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", 
                 f"ubuntu@{target}", check_cmd],
                capture_output=True,
                text=True,
                timeout=10,
                shell=False
            )
            
            if result.returncode == 0 and result.stdout.strip():
                try:
                    health = json.loads(result.stdout.strip())
                    
                    # Format table 220 status
                    if health.get('table_220'):
                        table_220_display = "[red]EXISTS[/red]"
                    else:
                        table_220_display = "[green]OK[/green]"
                    
                    # Format broad APIPA status
                    if health.get('broad_apipa'):
                        broad_apipa_display = "[red]EXISTS[/red]"
                    else:
                        broad_apipa_display = "[green]OK[/green]"
                    
                    # Format tunnel routes count (APIPA routes for VTI interfaces)
                    tunnel_routes_count = health.get('orphaned_count', 0)
                    tunnel_routes_display = f"{tunnel_routes_count} routes"
                    
                    # Overall status
                    status = health.get('status', 'unknown')
                    if status == 'healthy':
                        overall_display = "[green]Healthy[/green]"
                    elif status == 'warning':
                        overall_display = "[yellow]Warning[/yellow]"
                    else:
                        overall_display = "[red]Issues Found[/red]"
                    
                    routing_table.add_row(
                        inst_cfg.hostname,
                        table_220_display,
                        broad_apipa_display,
                        tunnel_routes_display,
                        overall_display
                    )
                except json.JSONDecodeError:
                    routing_table.add_row(
                        inst_cfg.hostname,
                        "[red]ERROR[/red]",
                        "[red]ERROR[/red]",
                        "-",
                        "[red]Parse Error[/red]"
                    )
            else:
                routing_table.add_row(
                    inst_cfg.hostname,
                    "[red]ERROR[/red]",
                    "[red]ERROR[/red]",
                    "-",
                    "[red]Check Failed[/red]"
                )
        
        except subprocess.TimeoutExpired:
            routing_table.add_row(
                inst_cfg.hostname,
                "[red]TIMEOUT[/red]",
                "[red]TIMEOUT[/red]",
                "-",
                "[red]Timeout[/red]"
            )
        except Exception as e:
            routing_table.add_row(
                inst_cfg.hostname,
                "[red]ERROR[/red]",
                "[red]ERROR[/red]",
                "-",
                f"[red]{str(e)[:20]}[/red]"
            )
    
    console.print(routing_table)


@app.command()
def list_routes(
    local_config_file: t.Optional[Path] = typer.Option(None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"),
    project_id: t.Optional[str] = typer.Option(None, help="Nebius project/folder identifier"),
):
    """List VPC routes for subnets matching gateway.local_prefixes."""
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
    
    # Get token for API access
    auth_token = None
    if not os.environ.get("NEBIUS_IAM_TOKEN"):
        try:
            from .vpngw_sa import ensure_cli_access_token
            tok = ensure_cli_access_token()
            if tok:
                os.environ["NEBIUS_IAM_TOKEN"] = tok
                auth_token = tok
        except Exception:
            pass
    
    routes = RouteManager(project_id=proj_id, auth_token=auth_token)
    
    print("[bold]Listing VPC routes for local_prefixes...[/bold]")
    routes.list_routes(plan, local_cfg)


@app.command()
def add_routes(
    local_config_file: t.Optional[Path] = typer.Option(None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"),
    project_id: t.Optional[str] = typer.Option(None, help="Nebius project/folder identifier"),
):
    """Ensure VPC routes to VPN gateway for remote_prefixes."""
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
    
    # Get token for API access
    auth_token = None
    if not os.environ.get("NEBIUS_IAM_TOKEN"):
        try:
            from .vpngw_sa import ensure_cli_access_token
            tok = ensure_cli_access_token()
            if tok:
                os.environ["NEBIUS_IAM_TOKEN"] = tok
                auth_token = tok
        except Exception:
            pass
    
    routes = RouteManager(project_id=proj_id, auth_token=auth_token)
    
    print("[bold]Ensuring VPC routes to VPN gateway for remote_prefixes...[/bold]")
    routes.add_routes(plan, local_cfg)
    
    print("[green]Route management completed.[/green]")


@app.command()
def destroy(
    local_config_file: t.Optional[Path] = typer.Option(None, exists=True, readable=True, help=f"Path to {DEFAULT_CONFIG_FILENAME}"),
    project_id: t.Optional[str] = typer.Option(None, help="Nebius project/folder identifier"),
    zone: t.Optional[str] = typer.Option(None, help="Nebius zone for gateway VMs"),
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
    
    # Get token for API access
    auth_token = None
    if not os.environ.get("NEBIUS_IAM_TOKEN"):
        try:
            from .vpngw_sa import ensure_cli_access_token
            tok = ensure_cli_access_token()
            if tok:
                os.environ["NEBIUS_IAM_TOKEN"] = tok
                auth_token = tok
        except Exception:
            pass
    
    vm_mgr = VMManager(
        project_id=proj_id, 
        zone=zone or plan.gateway_group.region, 
        auth_token=auth_token, 
        tenant_id=tenant_id, 
        region_id=region_id
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
                except Exception:
                    print("[red]Error: Nebius SDK not available. Install with 'pip install nebius'.[/red]")
                    raise typer.Exit(code=1)
        
        if vm_mgr.tenant_id and vm_mgr.project_id and (vm_mgr.region_id or plan.gateway_group.region):
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
        from nebius.api.nebius.compute.v1 import InstanceServiceClient, DiskServiceClient, ListInstancesRequest
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient
        isc = InstanceServiceClient(client)
        dsc = DiskServiceClient(client)
        asc = AllocationServiceClient(client)
        
        # List existing VMs matching the gateway group name
        print(f"[bold]Step 1/4: Listing VMs matching pattern '{plan.gateway_group.name}-*'...[/bold]")
        ilist_op = isc.list(ListInstancesRequest(parent_id=proj_id or ""))
        ilist = ilist_op.wait() if hasattr(ilist_op, 'wait') else ilist_op
        
        # Extract items from the response
        items = []
        if hasattr(ilist, 'items'):
            items = ilist.items
        elif hasattr(ilist, '__iter__'):
            items = list(ilist)
        
        existing = [
            inst for inst in items
            if getattr(getattr(inst, "metadata", None), "name", "").startswith(f"{plan.gateway_group.name}-")
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
                            print(f"[dim]Found private allocation from VM {inst_name}: {ni.ip_address.allocation_id}[/dim]")
        
        # Method 2: Search by name pattern (catches allocations from already-deleted VMs)
        try:
            from nebius.api.nebius.vpc.v1 import ListAllocationsRequest
            alloc_list_op = asc.list(ListAllocationsRequest(parent_id=proj_id or ""))
            alloc_list = alloc_list_op.wait() if hasattr(alloc_list_op, 'wait') else alloc_list_op
            
            alloc_items = []
            if hasattr(alloc_list, 'items'):
                alloc_items = alloc_list.items
            elif hasattr(alloc_list, '__iter__'):
                alloc_items = list(alloc_list)
            
            # Look for private IP allocations matching our naming pattern
            for alloc in alloc_items:
                alloc_name = getattr(getattr(alloc, "metadata", None), "name", None)
                alloc_id = getattr(alloc, "id", None) or getattr(getattr(alloc, "metadata", None), "id", None)
                
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
                                print(f"[dim]Found private allocation by name pattern {alloc_name}: {alloc_id}[/dim]")
                            break
        except Exception as e:
            print(f"[dim]Could not search for allocations by name: {e}[/dim]")
        
        # Step 2: Delete VMs
        print(f"[bold]Step 2/5: Deleting VMs...[/bold]")
        for inst in existing:
            inst_id = getattr(inst, "id", None) or getattr(getattr(inst, "metadata", None), "id", None)
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
        print(f"[bold]Step 3/5: Deleting boot disks...[/bold]")
        from nebius.api.nebius.common.v1 import GetByNameRequest
        import time
        
        for i in range(plan.gateway_group.instance_count):
            inst_name = f"{plan.gateway_group.name}-{i}"
            boot_disk_name = f"{inst_name}-boot"
            
            try:
                disk_obj = dsc.get_by_name(GetByNameRequest(parent_id=proj_id, name=boot_disk_name)).wait()
                disk_id = getattr(disk_obj, "id", None) or getattr(getattr(disk_obj, "metadata", None), "id", None)
                
                if disk_id:
                    # Retry disk deletion up to 3 times
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            print(f"[VMManager] Deleting boot disk {boot_disk_name} (id={disk_id})...")
                            from nebius.api.nebius.compute.v1 import DeleteDiskRequest
                            delete_disk_req = DeleteDiskRequest(id=disk_id)
                            disk_op = dsc.delete(delete_disk_req)
                            if hasattr(disk_op, "wait"):
                                disk_op.wait()
                                print(f"[green]✓ Boot disk {boot_disk_name} deleted[/green]")
                            break
                        except Exception as disk_err:
                            if "FAILED_PRECONDITION" in str(disk_err) and "read-write attachments" in str(disk_err):
                                if attempt < max_retries - 1:
                                    wait_time = 10 * (attempt + 1)
                                    print(f"[yellow]Disk still attached, waiting {wait_time}s before retry {attempt + 2}/{max_retries}...[/yellow]")
                                    time.sleep(wait_time)
                                else:
                                    print(f"[red]Could not delete boot disk {boot_disk_name} after {max_retries} attempts: {disk_err}[/red]")
                            else:
                                print(f"[red]Could not delete boot disk {boot_disk_name}: {disk_err}[/red]")
                                break
            except Exception as e:
                # Non-fatal: disk might not exist
                print(f"[dim]Boot disk {boot_disk_name} not found (may have been already deleted)[/dim]")
        
        # Step 4: Delete VPC routes (MUST happen before deleting private IP allocations)
        print(f"[bold]Step 4/5: Deleting VPC routes pointing to gateway allocations...[/bold]")
        deleted_routes = []
        try:
            from nebius.api.nebius.vpc.v1 import RouteTableServiceClient, RouteServiceClient, ListRouteTablesRequest, ListRoutesRequest
            rtc = RouteTableServiceClient(client)
            rsc = RouteServiceClient(client)
            
            # List all route tables in the project
            rt_list_op = rtc.list(ListRouteTablesRequest(parent_id=proj_id or ""))
            rt_list = rt_list_op.wait() if hasattr(rt_list_op, 'wait') else rt_list_op
            
            rt_items = []
            if hasattr(rt_list, 'items'):
                rt_items = rt_list.items
            elif hasattr(rt_list, '__iter__'):
                rt_items = list(rt_list)
            
            # For each route table, list its routes
            for rt in rt_items:
                rt_id = getattr(rt, "id", None) or getattr(getattr(rt, "metadata", None), "id", None)
                rt_name = getattr(getattr(rt, "metadata", None), "name", None) or "unknown"
                
                if not rt_id:
                    continue
                
                # List routes in this table using ListRoutesRequest
                try:
                    routes_list_op = rsc.list(ListRoutesRequest(parent_id=rt_id))
                    routes_list = routes_list_op.wait() if hasattr(routes_list_op, 'wait') else routes_list_op
                    
                    route_items = []
                    if hasattr(routes_list, 'items'):
                        route_items = routes_list.items
                    elif hasattr(routes_list, '__iter__'):
                        route_items = list(routes_list)
                    
                    for route in route_items:
                        route_id = getattr(route, "id", None) or getattr(getattr(route, "metadata", None), "id", None)
                        route_name = getattr(getattr(route, "metadata", None), "name", None) or "unknown"
                        spec = getattr(route, "spec", None)
                        next_hop = getattr(spec, "next_hop", None) if spec else None
                        
                        # Check if this route uses one of our private allocations
                        # NextHop has an 'allocation' field with an 'id' sub-field
                        if next_hop and hasattr(next_hop, "allocation"):
                            allocation = next_hop.allocation
                            if hasattr(allocation, "id") and allocation.id:
                                nh_alloc_id = allocation.id
                                for inst_name, alloc_id in private_alloc_ids:
                                    if nh_alloc_id == alloc_id:
                                        # Delete this route
                                        try:
                                            print(f"Deleting route {route_name} → {alloc_id}")
                                            from nebius.api.nebius.vpc.v1 import DeleteRouteRequest
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
            print("[yellow]You may need to manually delete routes before private IP allocations can be removed[/yellow]")
        
        # Step 5: Delete static private IP allocations (after routes are deleted)
        print(f"[bold]Step 5/5: Deleting static private IP allocations...[/bold]")
        if private_alloc_ids:
            from nebius.api.nebius.vpc.v1 import DeleteAllocationRequest
            for inst_name, alloc_id in private_alloc_ids:
                try:
                    print(f"[VMManager] Deleting private IP allocation for {inst_name} (id={alloc_id})...")
                    delete_alloc_req = DeleteAllocationRequest(id=alloc_id)
                    alloc_op = asc.delete(delete_alloc_req)
                    if hasattr(alloc_op, "wait"):
                        alloc_op.wait()
                        print(f"[green]✓ Private IP allocation deleted[/green]")
                except Exception as e:
                    # Check if it's already deleted (lifecycle managed by network interface)
                    if "NOT_FOUND" in str(e):
                        print(f"[dim]Private IP allocation already deleted (auto-managed by network interface)[/dim]")
                    elif "FAILED_PRECONDITION" in str(e) and "used as next hop for routes" in str(e):
                        print(f"[yellow]Could not delete private IP allocation (still used by routes): {e}[/yellow]")
                        print(f"[yellow]This may require manual cleanup via console or CLI[/yellow]")
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
        print("[bold]  nebius-vpngw add-routes --local-config-file <your-config.yaml>[/bold]")
        print("[dim]This will create new routes with the new static private IP allocations.[/dim]")
        
    except Exception as e:
        print(f"[red]Error during destroy: {e}[/red]")
        raise typer.Exit(code=1)


# init_config command removed; auto-creation occurs on first run without --local-config-file


def main():  # console script entry point
    try:
        app()
    except Exception as e:
        print(f"[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
