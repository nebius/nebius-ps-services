from __future__ import annotations

import ipaddress
from typing import Dict, List, Tuple, Optional

from rich import print

from ..config_loader import ResolvedDeploymentPlan


class RouteManager:
    def __init__(self, project_id: str | None, auth_token: str | None = None) -> None:
        self.project_id = project_id
        self.auth_token = auth_token
        self.endpoint = "vpc.api.nebius.cloud:443"

    def _channel(self):
        """Create a synchronous gRPC channel for VPC API."""
        import grpc  # type: ignore
        import os

        token = self.auth_token or os.environ.get("NEBIUS_IAM_TOKEN")
        if not token:
            raise ValueError("No authentication token available. Set NEBIUS_IAM_TOKEN or pass auth_token.")
        
        # Create a metadata callback for authentication
        def auth_metadata_plugin(context, callback):
            callback([("authorization", f"Bearer {token}")], None)
        
        # Create channel credentials with auth metadata
        auth_creds = grpc.metadata_call_credentials(auth_metadata_plugin)
        ssl_creds = grpc.ssl_channel_credentials()
        composite_creds = grpc.composite_channel_credentials(ssl_creds, auth_creds)
        
        # Return channel with composite credentials
        return grpc.secure_channel(self.endpoint, composite_creds)

    def _list_allocations(self, channel):
        """Return list of (network, allocation_id) and a lookup map for pretty-printing."""
        from nebius.api.nebius.vpc.v1 import allocation_service_pb2, allocation_service_pb2_grpc, allocation_pb2

        nets: List[Tuple[ipaddress._BaseNetwork, str]] = []
        alloc_to_ip: Dict[str, str] = {}
        stub = allocation_service_pb2_grpc.AllocationServiceStub(channel)
        req = allocation_service_pb2.ListAllocationsRequest(parent_id=self.project_id or "")
        resp = stub.List(req)
        for alloc in resp.items:
            cidr = alloc.status.details.allocated_cidr
            try:
                net = ipaddress.ip_network(cidr, strict=False)
            except Exception:
                continue
            nets.append((net, alloc.metadata.id))
            # pick first host address to represent this allocation
            ip_str = str(net.network_address if net.prefixlen == net.max_prefixlen else next(net.hosts(), net.network_address))
            alloc_to_ip[alloc.metadata.id] = ip_str
        return nets, alloc_to_ip

    def _find_gateway_private_allocation(self, vpc_channel, compute_channel, plan: ResolvedDeploymentPlan) -> Optional[str]:
        from nebius.api.nebius.compute.v1 import instance_service_pb2, instance_service_pb2_grpc
        import ipaddress

        # Collect target instance names from the plan
        target_names = {inst.hostname for inst in plan.iter_instance_configs()}

        # List instances in the project and find the private (static) IP allocation
        # With the VM manager refactoring, private IPs now use static allocations
        istub = instance_service_pb2_grpc.InstanceServiceStub(compute_channel)
        ilist = istub.List(instance_service_pb2.ListInstancesRequest(parent_id=self.project_id or ""))
        for inst in ilist.items:
            if inst.metadata.name in target_names:
                for ni in inst.status.network_interfaces:
                    # Check if this network interface has a private IP with a static allocation
                    if ni.ip_address and ni.ip_address.allocation_id:
                        # Extract the IP address string (without CIDR notation)
                        ip_str = ni.ip_address.address.split('/')[0]
                        # Verify it's a private IP
                        if ipaddress.ip_address(ip_str).is_private:
                            return ni.ip_address.allocation_id

        return None

    def list_routes(self, plan: ResolvedDeploymentPlan, local_cfg: dict) -> None:
        """List route tables attached to subnets matching gateway.local_prefixes."""
        try:
            channel = self._channel()
        except Exception as e:
            print(f"[red]Failed to open VPC SDK channel:[/red] {e}")
            return
        from nebius.api.nebius.vpc.v1 import subnet_service_pb2, subnet_service_pb2_grpc, route_service_pb2, route_service_pb2_grpc
        from rich.table import Table
        from rich.console import Console

        gateway_prefixes = [ipaddress.ip_network(p) for p in (local_cfg.get("gateway", {}).get("local_prefixes") or [])]
        if not gateway_prefixes:
            print("[yellow]No gateway.local_prefixes; nothing to list.[/yellow]")
            return

        # Allocation mapping for pretty-printing next hop
        _, alloc_to_ip = self._list_allocations(channel)

        sstub = subnet_service_pb2_grpc.SubnetServiceStub(channel)
        rstub = route_service_pb2_grpc.RouteServiceStub(channel)
        console = Console()

        subnets = sstub.List(subnet_service_pb2.ListSubnetsRequest(parent_id=self.project_id or "")).items
        for sn in subnets:
            # Filter by overlap with gateway.local_prefixes
            if not any(
                ipaddress.ip_network(cidr, strict=False).overlaps(pfx)
                for cidr in sn.status.ipv4_private_cidrs
                for pfx in gateway_prefixes
            ):
                continue
            
            rt_id = sn.status.route_table.id
            rt_default = sn.status.route_table.default
            
            print(f"\n[bold cyan]Subnet: {sn.metadata.name}[/bold cyan] ({', '.join(sn.status.ipv4_private_cidrs)})")
            
            if not rt_id:
                print("[yellow]  No route table attached[/yellow]")
                continue
            
            print(f"[dim]  Route Table ID: {rt_id} (default={rt_default})[/dim]")
            
            routes = rstub.List(route_service_pb2.ListRoutesRequest(parent_id=rt_id)).items
            
            if not routes:
                print("[dim]  No routes in route table[/dim]")
                continue
            
            # Build table
            table = Table(show_header=True, header_style="bold")
            table.add_column("Destination", style="cyan")
            table.add_column("Next Hop", style="green")
            
            for r in routes:
                dest = r.spec.destination.cidr
                nh = r.spec.next_hop
                nh_desc = "-"
                if nh.allocation.id:
                    nh_desc = alloc_to_ip.get(nh.allocation.id, nh.allocation.id)
                elif nh.default_egress_gateway:
                    nh_desc = "default-egress"
                
                table.add_row(dest, nh_desc)
            
            console.print(table)

    def add_routes(self, plan: ResolvedDeploymentPlan, local_cfg: dict) -> None:
        """Ensure routes for connection.remote_prefixes and assign custom route tables when needed."""
        try:
            channel = self._channel()
        except Exception as e:
            print(f"[red]Failed to open VPC SDK channel:[/red] {e}")
            return
        
        # Create compute channel using same auth pattern as VPC channel
        try:
            import grpc  # type: ignore
            import os
            
            token = self.auth_token or os.environ.get("NEBIUS_IAM_TOKEN")
            if not token:
                raise ValueError("No authentication token available.")
            
            def auth_metadata_plugin(context, callback):
                callback([("authorization", f"Bearer {token}")], None)
            
            auth_creds = grpc.metadata_call_credentials(auth_metadata_plugin)
            ssl_creds = grpc.ssl_channel_credentials()
            composite_creds = grpc.composite_channel_credentials(ssl_creds, auth_creds)
            compute_channel = grpc.secure_channel("compute.api.nebius.cloud:443", composite_creds)
        except Exception:
            compute_channel = None

        from nebius.api.nebius.vpc.v1 import (
            subnet_service_pb2,
            subnet_service_pb2_grpc,
            route_table_service_pb2,
            route_table_service_pb2_grpc,
            route_table_pb2,
            route_service_pb2,
            route_service_pb2_grpc,
            route_pb2,
            subnet_pb2,
        )
        from nebius.api.nebius.common.v1 import metadata_pb2

        gateway_prefixes = [ipaddress.ip_network(p) for p in (local_cfg.get("gateway", {}).get("local_prefixes") or [])]
        if not gateway_prefixes:
            print("[yellow]No gateway.local_prefixes; cannot determine relevant subnets.[/yellow]")
            return

        # Determine routing mode and collect remote prefixes
        defaults_mode = (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode") or "bgp"
        
        # Collect remote prefixes based on routing mode
        remote_prefixes: List[str] = []
        
        for conn in (local_cfg.get("connections") or []):
            mode = conn.get("routing_mode") or defaults_mode
            
            if mode == "bgp":
                # For BGP mode: query learned routes from FRR
                bgp_prefixes = self._get_bgp_learned_routes(plan, conn, local_cfg)
                if bgp_prefixes:
                    remote_prefixes.extend(bgp_prefixes)
            else:
                # For static mode: use configured remote_prefixes
                rp = conn.get("remote_prefixes") or []
                if rp:
                    remote_prefixes.extend(rp)
        
        if not remote_prefixes:
            print("[yellow]No remote prefixes found (BGP: no learned routes; Static: no configured remote_prefixes)[/yellow]")
            return

        # Filter out local prefixes (don't create routes for our own VPC networks)
        local_networks = [ipaddress.ip_network(p) for p in (local_cfg.get("gateway", {}).get("local_prefixes") or [])]
        filtered_prefixes = []
        for pfx in remote_prefixes:
            try:
                pfx_net = ipaddress.ip_network(pfx)
                # Skip if this prefix overlaps with any local prefix
                is_local = any(pfx_net.overlaps(local_net) for local_net in local_networks)
                if is_local:
                    print(f"[dim]Skipping {pfx} (overlaps with local_prefixes)[/dim]")
                    continue
                filtered_prefixes.append(pfx)
            except Exception:
                continue
        
        remote_prefixes = sorted(set(filtered_prefixes))
        
        if not remote_prefixes:
            print("[yellow]No remote prefixes to add (all learned routes are local networks)[/yellow]")
            return
        
        print(f"[cyan]Found {len(remote_prefixes)} remote prefix(es) to add as VPC routes[/cyan]")

        # Map gateway external IP to allocation id
        alloc_id = None
        if compute_channel:
            alloc_id = self._find_gateway_private_allocation(channel, compute_channel, plan)
        if not alloc_id:
            print("[yellow]Could not resolve private allocation_id for gateway; cannot create routes.[/yellow]")
            return

        sstub = subnet_service_pb2_grpc.SubnetServiceStub(channel)
        rtstub = route_table_service_pb2_grpc.RouteTableServiceStub(channel)
        rstub = route_service_pb2_grpc.RouteServiceStub(channel)

        subnets = sstub.List(subnet_service_pb2.ListSubnetsRequest(parent_id=self.project_id or "")).items
        for sn in subnets:
            # Consider only subnets overlapping gateway.local_prefixes
            overlaps = any(
                ipaddress.ip_network(cidr, strict=False).overlaps(pfx)
                for cidr in sn.status.ipv4_private_cidrs
                for pfx in gateway_prefixes
            )
            if not overlaps:
                continue

            rt_info = sn.status.route_table
            if not rt_info.default and rt_info.id:
                print(
                    f"[cyan]Subnet {sn.metadata.name} already uses custom route table {rt_info.id}; "
                    "adding VPN routes...[/cyan]"
                )
                rt_id = rt_info.id
            else:
                # Subnet uses default route table - need to create custom RT
                rt_name = f"{sn.metadata.name}-vpngw-rt"
                
                # Check if route table already exists (idempotency)
                existing_rts = rtstub.List(
                    route_table_service_pb2.ListRouteTablesRequest(parent_id=self.project_id)
                ).items
                existing_rt = next((rt for rt in existing_rts if rt.metadata.name == rt_name), None)
                
                if existing_rt:
                    rt_id = existing_rt.metadata.id
                    print(f"[green]Using existing route table {rt_id} ({rt_name}) for subnet {sn.metadata.name}[/green]")
                    # Attach to subnet if not already attached
                    if rt_info.id != rt_id:
                        try:
                            sstub.Update(
                                subnet_service_pb2.UpdateSubnetRequest(
                                    metadata=metadata_pb2.ResourceMetadata(id=sn.metadata.id),
                                    spec=subnet_pb2.SubnetSpec(route_table_id=rt_id),
                                )
                            )
                            print(f"[green]Attached route table {rt_id} to subnet {sn.metadata.name}[/green]")
                        except Exception as e:
                            print(f"[yellow]Failed to attach route table to subnet {sn.metadata.name}: {e}[/yellow]")
                            continue
                else:
                    # Create new route table and copy routes from default RT
                    print(f"[yellow]⚠ Subnet {sn.metadata.name} uses default route table[/yellow]")
                    print(f"[yellow]  Creating custom route table '{rt_name}' to add VPN routes[/yellow]")
                    
                    # Get default route table ID to copy routes from
                    default_rt_id = rt_info.id if rt_info.id else None
                    
                    try:
                        op = rtstub.Create(
                            route_table_service_pb2.CreateRouteTableRequest(
                                metadata=metadata_pb2.ResourceMetadata(
                                    name=rt_name,
                                    parent_id=self.project_id,
                                ),
                                spec=route_table_pb2.RouteTableSpec(network_id=sn.spec.network_id),
                            )
                        )
                        new_rt_id = op.resource_id or ""
                        if not new_rt_id:
                            print(f"[red]Route table create returned no resource_id for subnet {sn.metadata.name}; skipping.[/red]")
                            continue
                        
                        # Copy existing routes from default route table
                        if default_rt_id:
                            try:
                                default_routes = rstub.List(
                                    route_service_pb2.ListRoutesRequest(parent_id=default_rt_id)
                                ).items
                                
                                if default_routes:
                                    print(f"[cyan]  Copying {len(default_routes)} route(s) from default route table...[/cyan]")
                                    for dr in default_routes:
                                        try:
                                            rstub.Create(
                                                route_service_pb2.CreateRouteRequest(
                                                    metadata=metadata_pb2.ResourceMetadata(
                                                        parent_id=new_rt_id,
                                                        name=f"{dr.metadata.name}-copy"[:63],
                                                    ),
                                                    spec=dr.spec,
                                                )
                                            )
                                        except Exception as copy_err:
                                            # Ignore errors for copying (might be system routes that can't be copied)
                                            print(f"[dim]  Could not copy route {dr.spec.destination.cidr}: {copy_err}[/dim]")
                                else:
                                    print(f"[dim]  No routes in default route table to copy[/dim]")
                            except Exception as list_err:
                                print(f"[yellow]  Could not list default route table routes: {list_err}[/yellow]")
                        
                        # Attach to subnet
                        sstub.Update(
                            subnet_service_pb2.UpdateSubnetRequest(
                                metadata=metadata_pb2.ResourceMetadata(id=sn.metadata.id),
                                spec=subnet_pb2.SubnetSpec(route_table_id=new_rt_id),
                            )
                        )
                        rt_id = new_rt_id
                        print(f"[green]✓ Created custom route table {rt_id} and attached to subnet {sn.metadata.name}[/green]")
                        print(f"[yellow]  NOTE: Future changes to the default route table will NOT apply to this subnet.[/yellow]")
                        print(f"[yellow]  Add any required routes manually to route table: {rt_id}[/yellow]")
                    except Exception as e:
                        print(f"[yellow]Failed to create/attach route table for subnet {sn.metadata.name}: {e}[/yellow]")
                        continue

            # Get existing routes to check for duplicates (idempotency)
            try:
                existing_routes = rstub.List(
                    route_service_pb2.ListRoutesRequest(parent_id=rt_id)
                ).items
                existing_route_cidrs = {r.spec.destination.cidr for r in existing_routes}
            except Exception as e:
                print(f"[yellow]Failed to list existing routes on {rt_id}: {e}[/yellow]")
                existing_route_cidrs = set()

            # Add routes for each remote prefix (skip if already exists)
            for pfx in remote_prefixes:
                if pfx in existing_route_cidrs:
                    print(f"[blue]Route {pfx} already exists on {rt_id}; skipping[/blue]")
                    continue
                    
                try:
                    rstub.Create(
                        route_service_pb2.CreateRouteRequest(
                            metadata=metadata_pb2.ResourceMetadata(
                                parent_id=rt_id,
                                name=f"vpngw-{pfx.replace('/', '-')}"[:63],
                            ),
                            spec=route_pb2.RouteSpec(
                                destination=route_pb2.DestinationMatch(cidr=pfx),
                                next_hop=route_pb2.NextHop(
                                    allocation=route_pb2.AllocationNextHop(id=alloc_id)
                                ),
                            ),
                        )
                    )
                    print(f"[green]Added route {pfx} -> allocation {alloc_id} on {rt_id}[/green]")
                except Exception as e:
                    err_str = str(e).lower()
                    if "already exists" in err_str or "duplicate" in err_str:
                        print(f"[blue]Route {pfx} already exists on {rt_id}; skipping[/blue]")
                    else:
                        print(f"[yellow]Failed to add route {pfx} on {rt_id}: {e}[/yellow]")

    def _get_bgp_learned_routes(self, plan: ResolvedDeploymentPlan, conn: dict, local_cfg: dict) -> List[str]:
        """Query FRR on gateway VMs to get BGP-learned routes (filtered by whitelist if configured)."""
        import subprocess
        import json
        import ipaddress
        
        conn_name = conn.get("name", "unnamed")
        whitelist = conn.get("remote_prefixes") or (conn.get("bgp", {}) or {}).get("remote_prefixes") or []
        
        # Create whitelist networks for matching
        whitelist_networks = []
        if whitelist:
            for pfx in whitelist:
                try:
                    whitelist_networks.append(ipaddress.ip_network(pfx))
                except Exception:
                    pass
        
        learned_prefixes = []
        
        # Query each gateway VM
        for inst_cfg in plan.iter_instance_configs():
            hostname = inst_cfg.hostname
            external_ip = inst_cfg.external_ip
            
            if not external_ip:
                print(f"[yellow]Skipping {hostname}: no external IP for BGP route query[/yellow]")
                continue
            
            try:
                result = subprocess.run(
                    ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                     f"ubuntu@{external_ip}", "sudo vtysh -c 'show bgp ipv4 unicast json'"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                
                if result.returncode != 0:
                    print(f"[yellow]Failed to query BGP routes from {hostname}: {result.stderr}[/yellow]")
                    continue
                
                bgp_data = json.loads(result.stdout)
                routes = bgp_data.get("routes", {})
                
                for prefix, route_data in routes.items():
                    # Skip locally originated routes (next-hop 0.0.0.0)
                    if isinstance(route_data, dict):
                        paths = [route_data]
                    elif isinstance(route_data, list):
                        paths = route_data
                    else:
                        continue
                    
                    # Check if any path has a real next-hop (not 0.0.0.0)
                    has_real_nexthop = False
                    for path in paths:
                        nexthops = path.get("nexthops", [])
                        if nexthops:
                            nh_ip = nexthops[0].get("ip", "0.0.0.0")
                            if nh_ip != "0.0.0.0":
                                has_real_nexthop = True
                                break
                    
                    if not has_real_nexthop:
                        continue  # Skip locally originated routes
                    
                    # Apply whitelist filter if configured
                    if whitelist_networks:
                        try:
                            prefix_net = ipaddress.ip_network(prefix)
                            allowed = any(prefix_net.subnet_of(wl) or prefix_net == wl for wl in whitelist_networks)
                            if not allowed:
                                continue
                        except Exception:
                            continue
                    
                    learned_prefixes.append(prefix)
                
                print(f"[cyan]Learned {len(routes)} BGP route(s) from {hostname} (connection: {conn_name})[/cyan]")
                
            except subprocess.TimeoutExpired:
                print(f"[yellow]Timeout querying BGP routes from {hostname}[/yellow]")
            except json.JSONDecodeError:
                print(f"[yellow]Failed to parse BGP JSON from {hostname}[/yellow]")
            except Exception as e:
                print(f"[yellow]Error querying BGP routes from {hostname}: {e}[/yellow]")
        
        return learned_prefixes

    def list_remote_routes(self, plan: ResolvedDeploymentPlan, local_cfg: dict, connection_filter: Optional[str] = None) -> None:
        """List remote routes learned via BGP or configured as static routes.
        
        - BGP mode: Query FRR for learned routes and check against remote_prefixes whitelist
        - Static mode: Show static routes configured from remote_prefixes
        """
        import subprocess
        import json
        from rich.table import Table
        from rich.console import Console
        
        console = Console()
        
        # Get routing mode and connections
        defaults_mode = (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode") or "bgp"
        connections = local_cfg.get("connections", [])
        
        if connection_filter:
            connections = [c for c in connections if c.get("name") == connection_filter]
            if not connections:
                print(f"[yellow]No connection found with name '{connection_filter}'[/yellow]")
                return
        
        # Group by gateway VM and connection
        for inst_cfg in plan.iter_instance_configs():
            hostname = inst_cfg.hostname
            external_ip = inst_cfg.external_ip
            
            if not external_ip:
                print(f"[yellow]Skipping {hostname}: no external IP[/yellow]")
                continue
            
            print(f"\n[bold cyan]Gateway VM: {hostname} ({external_ip})[/bold cyan]")
            
            # Process each connection for this VM
            for conn in connections:
                conn_name = conn.get("name", "unnamed")
                routing_mode = conn.get("routing_mode") or defaults_mode
                remote_prefixes = conn.get("remote_prefixes", []) or (conn.get("bgp", {}) or {}).get("remote_prefixes", [])
                
                print(f"\n[bold]Connection: {conn_name}[/bold] (routing_mode: {routing_mode})")
                
                if routing_mode == "bgp":
                    self._list_bgp_routes(hostname, external_ip, conn_name, remote_prefixes, console)
                else:  # static mode
                    self._list_static_routes(hostname, external_ip, conn_name, remote_prefixes, console)
    
    def _list_bgp_routes(self, hostname: str, external_ip: str, conn_name: str, whitelist: List[str], console) -> None:
        """Query FRR BGP routes and check against whitelist."""
        import subprocess
        import json
        import re
        from rich.table import Table
        import ipaddress
        
        # Query BGP routes via SSH
        try:
            result = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", 
                 f"ubuntu@{external_ip}", "sudo vtysh -c 'show bgp ipv4 unicast json'"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            
            if result.returncode != 0:
                print(f"[yellow]Failed to query BGP routes: {result.stderr}[/yellow]")
                return
            
            bgp_data = json.loads(result.stdout)
            routes = bgp_data.get("routes", {})
            
            if not routes:
                print("[dim]No BGP routes learned yet[/dim]")
                return
            
            # Build a cache of next-hop IP -> interface mappings
            nexthop_to_iface = {}
            unique_nexthops = set()
            for route_data in routes.values():
                paths = [route_data] if isinstance(route_data, dict) else route_data
                for path in paths:
                    for nh in path.get("nexthops", []):
                        nh_ip = nh.get("ip")
                        if nh_ip and nh_ip != "0.0.0.0":
                            unique_nexthops.add(nh_ip)
            
            # Query interface for each unique next-hop
            for nh_ip in unique_nexthops:
                try:
                    route_result = subprocess.run(
                        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
                         f"ubuntu@{external_ip}", f"ip route get {nh_ip}"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if route_result.returncode == 0:
                        # Parse output like: "169.254.5.153 dev vti1 src 169.254.5.154 uid 1000"
                        match = re.search(r'dev\s+(\S+)', route_result.stdout)
                        if match:
                            nexthop_to_iface[nh_ip] = match.group(1)
                except Exception:
                    pass
            
            # Create whitelist networks for matching
            whitelist_networks = []
            if whitelist:
                for pfx in whitelist:
                    try:
                        whitelist_networks.append(ipaddress.ip_network(pfx))
                    except Exception:
                        pass
            
            # Build table
            table = Table(title=f"Remote Routes (BGP-learned) - {conn_name}")
            table.add_column("Prefix", style="cyan")
            table.add_column("Next-Hop", style="blue")
            table.add_column("Via", style="magenta")
            table.add_column("AS Path", style="yellow")
            table.add_column("Status", style="green")
            
            for prefix, route_data in sorted(routes.items()):
                if isinstance(route_data, dict):
                    # Handle single path
                    paths = [route_data]
                elif isinstance(route_data, list):
                    # Handle multiple paths
                    paths = route_data
                else:
                    continue
                
                for path in paths:
                    nexthops = path.get("nexthops", [])
                    as_path = path.get("path", "")
                    
                    # Determine next-hop and interface
                    nexthop_ip = "-"
                    via_iface = "-"
                    
                    if nexthops:
                        nh = nexthops[0]
                        nexthop_ip = nh.get("ip", "-")
                        # Look up interface from our cache
                        via_iface = nexthop_to_iface.get(nexthop_ip, "-")
                    
                    # Skip locally originated routes (next-hop 0.0.0.0)
                    if nexthop_ip == "0.0.0.0":
                        continue
                    
                    # Check whitelist status
                    status = "allowed"
                    if whitelist_networks:
                        try:
                            prefix_net = ipaddress.ip_network(prefix)
                            allowed = any(prefix_net.subnet_of(wl) or prefix_net == wl for wl in whitelist_networks)
                            status = "allowed" if allowed else "[red]not-allowed[/red]"
                        except Exception:
                            status = "unknown"
                    else:
                        status = "[dim]no-filter[/dim]"
                    
                    table.add_row(prefix, nexthop_ip, via_iface, as_path, status)
            
            console.print(table)
            
            if whitelist:
                print(f"[dim]Note: remote_prefixes whitelist has {len(whitelist)} entries[/dim]")
            else:
                print("[dim]Note: No remote_prefixes whitelist configured - all BGP routes accepted[/dim]")
        
        except subprocess.TimeoutExpired:
            print(f"[yellow]Timeout querying BGP routes from {hostname}[/yellow]")
        except json.JSONDecodeError:
            print(f"[yellow]Failed to parse BGP JSON output from {hostname}[/yellow]")
        except Exception as e:
            print(f"[yellow]Error querying BGP routes: {e}[/yellow]")
    
    def _list_static_routes(self, hostname: str, external_ip: str, conn_name: str, remote_prefixes: List[str], console) -> None:
        """List static routes configured on gateway VM."""
        import subprocess
        from rich.table import Table
        
        if not remote_prefixes:
            print("[yellow]No remote_prefixes configured in YAML for this connection[/yellow]")
            return
        
        # Query kernel routing table via SSH
        try:
            result = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                 f"ubuntu@{external_ip}", "ip route show"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            
            if result.returncode != 0:
                print(f"[yellow]Failed to query routes: {result.stderr}[/yellow]")
                return
            
            # Parse routing table
            kernel_routes = {}
            for line in result.stdout.splitlines():
                parts = line.split()
                if not parts:
                    continue
                dest = parts[0]
                # Extract next-hop and interface
                nexthop = "-"
                via_dev = "-"
                if "via" in parts:
                    idx = parts.index("via")
                    if idx + 1 < len(parts):
                        nexthop = parts[idx + 1]
                if "dev" in parts:
                    idx = parts.index("dev")
                    if idx + 1 < len(parts):
                        via_dev = parts[idx + 1]
                kernel_routes[dest] = (nexthop, via_dev)
            
            # Build table
            table = Table(title=f"Remote Routes (Static) - {conn_name}")
            table.add_column("Prefix (YAML)", style="cyan")
            table.add_column("Status", style="yellow")
            table.add_column("Next-Hop", style="blue")
            table.add_column("Via", style="magenta")
            
            for pfx in sorted(remote_prefixes):
                if pfx in kernel_routes:
                    nexthop, via_dev = kernel_routes[pfx]
                    table.add_row(pfx, "[green]installed[/green]", nexthop, via_dev)
                else:
                    table.add_row(pfx, "[red]missing[/red]", "-", "-")
            
            console.print(table)
            print(f"[dim]Showing {len(remote_prefixes)} configured static remote prefixes[/dim]")
        
        except subprocess.TimeoutExpired:
            print(f"[yellow]Timeout querying routes from {hostname}[/yellow]")
        except Exception as e:
            print(f"[yellow]Error querying routes: {e}[/yellow]")
