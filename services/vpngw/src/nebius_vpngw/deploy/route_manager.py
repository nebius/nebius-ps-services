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

        gateway_prefixes = [ipaddress.ip_network(p) for p in (local_cfg.get("gateway", {}).get("local_prefixes") or [])]
        if not gateway_prefixes:
            print("[yellow]No gateway.local_prefixes; nothing to list.[/yellow]")
            return

        # Allocation mapping for pretty-printing next hop
        _, alloc_to_ip = self._list_allocations(channel)

        sstub = subnet_service_pb2_grpc.SubnetServiceStub(channel)
        rstub = route_service_pb2_grpc.RouteServiceStub(channel)

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
            print(f"[cyan]{sn.metadata.name}[/cyan] (subnet {sn.metadata.id}) route table: {rt_id or 'none'} (default={rt_default})")
            if not rt_id:
                continue
            routes = rstub.List(route_service_pb2.ListRoutesRequest(parent_id=rt_id)).items
            for r in routes:
                dest = r.spec.destination.cidr
                nh = r.spec.next_hop
                nh_desc = "-"
                if nh.allocation.id:
                    nh_desc = alloc_to_ip.get(nh.allocation.id, nh.allocation.id)
                elif nh.default_egress_gateway:
                    nh_desc = "default-egress"
                print(f"  - Destination: {dest} | Next hop: {nh_desc} | Route: {r.metadata.id}")

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

        # Collect remote prefixes from all connections
        remote_prefixes: List[str] = []
        defaults_mode = (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode") or "bgp"
        for conn in (local_cfg.get("connections") or []):
            mode = conn.get("routing_mode") or defaults_mode
            rp = conn.get("remote_prefixes") or (conn.get("bgp", {}) or {}).get("remote_prefixes") or []
            if rp:
                remote_prefixes.extend(rp)
        if not remote_prefixes:
            print("[yellow]No remote_prefixes defined at connection level; skipping route creation.[/yellow]")
            return

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
                    f"[yellow]Subnet {sn.metadata.name} already uses custom route table {rt_info.id}; "
                    "checking/adding missing routes...[/yellow]"
                )
                rt_id = rt_info.id
            else:
                # Create a custom route table and attach to subnet
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
                    # Create new route table
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
                            print(f"[yellow]Route table create returned no resource_id for subnet {sn.metadata.name}; skipping attach.[/yellow]")
                            continue
                        # Attach to subnet
                        sstub.Update(
                            subnet_service_pb2.UpdateSubnetRequest(
                                metadata=metadata_pb2.ResourceMetadata(id=sn.metadata.id),
                                spec=subnet_pb2.SubnetSpec(route_table_id=new_rt_id),
                            )
                        )
                        rt_id = new_rt_id
                        print(f"[green]Created and attached route table {rt_id} to subnet {sn.metadata.name}[/green]")
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

    def reconcile(self, plan: ResolvedDeploymentPlan) -> None:
        """Legacy hook; no-op now that routes are driven by --add-route/--list-route."""
        print("[RouteManager] reconcile is no-op; use --add-route or --list-route.")
