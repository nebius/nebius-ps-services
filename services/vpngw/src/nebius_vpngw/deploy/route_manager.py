from __future__ import annotations

import ipaddress
from pathlib import Path

from rich import print

from ..config_loader import ResolvedDeploymentPlan


class RouteManager:
    @staticmethod
    def _normalize_value(value) -> str:
        if hasattr(value, "value"):
            value = value.value
        return str(value or "").strip().lower()

    def __init__(self, project_id: str | None, auth_token: str | None = None) -> None:
        self.project_id = project_id
        self.auth_token = auth_token
        self.endpoint = "vpc.api.nebius.cloud:443"

    def _channel(self):
        """Create a synchronous gRPC channel for VPC API."""
        import os

        import grpc  # type: ignore

        token = self.auth_token or os.environ.get("NEBIUS_IAM_TOKEN")
        if not token:
            raise ValueError(
                "No authentication token available. Set NEBIUS_IAM_TOKEN or pass auth_token."
            )

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
        from nebius.api.nebius.vpc.v1 import (
            allocation_service_pb2,
            allocation_service_pb2_grpc,
        )

        nets: list[tuple[ipaddress._BaseNetwork, str]] = []
        alloc_to_ip: dict[str, str] = {}
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
            ip_str = str(
                net.network_address
                if net.prefixlen == net.max_prefixlen
                else next(net.hosts(), net.network_address)
            )
            alloc_to_ip[alloc.metadata.id] = ip_str
        return nets, alloc_to_ip

    def _find_gateway_private_allocations_by_index(
        self, compute_channel, plan: ResolvedDeploymentPlan
    ) -> dict[int, str]:
        import ipaddress

        from nebius.api.nebius.compute.v1 import (
            instance_service_pb2,
            instance_service_pb2_grpc,
        )

        host_to_index = {
            inst.hostname: inst.instance_index for inst in plan.iter_instance_configs()
        }
        allocations_by_index: dict[int, str] = {}

        # List instances in the project and find the private (static) IP allocation
        # With the VM manager refactoring, private IPs now use static allocations
        istub = instance_service_pb2_grpc.InstanceServiceStub(compute_channel)
        ilist = istub.List(
            instance_service_pb2.ListInstancesRequest(parent_id=self.project_id or "")
        )
        for inst in ilist.items:
            instance_index = host_to_index.get(inst.metadata.name)
            if instance_index is None:
                continue
            for ni in inst.status.network_interfaces:
                # Check if this network interface has a private IP with a static allocation
                if ni.ip_address and ni.ip_address.allocation_id:
                    # Extract the IP address string (without CIDR notation)
                    ip_str = ni.ip_address.address.split("/")[0]
                    # Verify it's a private IP
                    if ipaddress.ip_address(ip_str).is_private:
                        allocations_by_index[instance_index] = ni.ip_address.allocation_id
                        break

        return allocations_by_index

    def _connection_instance_indices(self, conn: dict) -> list[int]:
        indices: set[int] = set()
        for tunnel in conn.get("tunnels") or []:
            if self._normalize_value(tunnel.get("ha_role") or "active") == "disable":
                continue
            try:
                indices.add(int(tunnel.get("gateway_instance_index", 0) or 0))
            except (TypeError, ValueError):
                continue
        return sorted(indices)

    def _connection_peer_ips(
        self,
        conn: dict,
        *,
        instance_index: int | None = None,
    ) -> set[str]:
        peer_ips: set[str] = set()
        for tunnel in conn.get("tunnels") or []:
            if self._normalize_value(tunnel.get("ha_role") or "active") == "disable":
                continue
            if instance_index is not None:
                try:
                    tunnel_instance_index = int(tunnel.get("gateway_instance_index", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if tunnel_instance_index != instance_index:
                    continue
            peer_ip = (
                ((tunnel.get("bgp", {}) or {}).get("remote_ip"))
                or tunnel.get("inner_remote_ip")
                or ""
            )
            if peer_ip:
                peer_ips.add(str(peer_ip))
        return peer_ips

    def _collect_remote_prefix_targets(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        allocations_by_index: dict[int, str],
    ) -> dict[str, str]:
        defaults_mode = (
            self._normalize_value(
                (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode")
            )
            or "bgp"
        )

        prefix_targets: dict[str, str] = {}
        prefix_source_labels: dict[str, set[str]] = {}
        conflict_sources: dict[str, set[str]] = {}

        for conn in local_cfg.get("connections") or []:
            conn_name = str(conn.get("name") or "unnamed")
            mode = self._normalize_value(conn.get("routing_mode") or defaults_mode)
            instance_indices = self._connection_instance_indices(conn)

            if not instance_indices:
                continue
            if len(instance_indices) != 1:
                raise ValueError(
                    f"Connection '{conn_name}' spans multiple gateway VMs "
                    f"({instance_indices}). add-routes-local requires one owning "
                    "gateway_instance_index per connection so each remote prefix has "
                    "an unambiguous next-hop."
                )

            instance_index = instance_indices[0]
            alloc_id = allocations_by_index.get(instance_index)
            if not alloc_id:
                raise ValueError(
                    f"Could not resolve the private IP allocation for connection "
                    f"'{conn_name}' on gateway_instance_index={instance_index}."
                )

            if mode == "bgp":
                conn_prefixes = self._get_bgp_learned_routes(plan, conn, local_cfg)
            else:
                conn_prefixes = list(conn.get("remote_prefixes") or [])

            source_label = f"{conn_name}@vm{instance_index}"
            for prefix in conn_prefixes:
                prefix_source_labels.setdefault(prefix, set()).add(source_label)
                previous_alloc = prefix_targets.get(prefix)
                if previous_alloc and previous_alloc != alloc_id:
                    conflict_sources.setdefault(prefix, set()).update(
                        prefix_source_labels.get(prefix, set())
                    )
                    continue
                prefix_targets[prefix] = alloc_id

        if conflict_sources:
            details = "; ".join(
                f"{prefix} via {', '.join(sources)}"
                for prefix, sources in sorted(
                    (pfx, sorted(srcs)) for pfx, srcs in conflict_sources.items()
                )
            )
            raise ValueError(
                "The same remote prefix is present on more than one gateway VM. "
                "add-routes-local cannot choose a single next-hop. Resolve the "
                f"overlap first: {details}"
            )

        return prefix_targets

    @staticmethod
    def _gateway_subnet_name(local_cfg: dict) -> str:
        gateway_group = local_cfg.get("gateway_group", {}) or {}
        subnet_cfg = gateway_group.get("subnet", {}) or {}
        return str(subnet_cfg.get("name") or "vpngw-subnet")

    @staticmethod
    def _subnet_uses_network_pools(subnet_obj) -> bool:
        subnet_spec = getattr(subnet_obj, "spec", None)
        ipv4_private_pools = getattr(subnet_spec, "ipv4_private_pools", None)
        return bool(getattr(ipv4_private_pools, "use_network_pools", False))

    @staticmethod
    def _extract_explicit_subnet_cidrs(subnet_obj) -> list[ipaddress.IPv4Network]:
        subnet_spec = getattr(subnet_obj, "spec", None)
        ipv4_private_pools = getattr(subnet_spec, "ipv4_private_pools", None)
        if not ipv4_private_pools or getattr(ipv4_private_pools, "use_network_pools", False):
            return []

        networks: list[ipaddress.IPv4Network] = []
        for pool in getattr(ipv4_private_pools, "pools", []) or []:
            for cidr_obj in getattr(pool, "cidrs", []) or []:
                cidr = getattr(cidr_obj, "cidr", None)
                if not cidr:
                    continue
                try:
                    networks.append(ipaddress.ip_network(cidr, strict=False))
                except Exception:
                    continue
        return networks

    @staticmethod
    def _extract_status_subnet_cidrs(subnet_obj) -> list[ipaddress.IPv4Network]:
        subnet_status = getattr(subnet_obj, "status", None)
        cidrs = getattr(subnet_status, "ipv4_private_cidrs", []) or []
        networks: list[ipaddress.IPv4Network] = []
        for cidr in cidrs:
            try:
                networks.append(ipaddress.ip_network(cidr, strict=False))
            except Exception:
                continue
        return networks

    def _effective_subnet_cidrs(self, subnet_obj) -> list[ipaddress.IPv4Network]:
        explicit_cidrs = self._extract_explicit_subnet_cidrs(subnet_obj)
        if explicit_cidrs:
            return explicit_cidrs
        return self._extract_status_subnet_cidrs(subnet_obj)

    @staticmethod
    def _ssh_base_command(local_cfg: dict) -> list[str]:
        import os

        gateway_group = local_cfg.get("gateway_group", {}) or {}
        vm_spec = gateway_group.get("vm_spec", {}) or {}
        ssh_key = vm_spec.get("ssh_private_key_path") or os.environ.get("VPNGW_SSH_KEY")

        cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=10",
        ]
        if ssh_key:
            cmd.extend(["-i", str(Path(str(ssh_key)).expanduser())])
        return cmd

    @staticmethod
    def _ssh_target(local_cfg: dict, external_ip: str) -> str:
        import os

        gateway_group = local_cfg.get("gateway_group", {}) or {}
        vm_spec = gateway_group.get("vm_spec", {}) or {}
        username = str(vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER") or "ubuntu")
        return f"{username}@{external_ip}"

    def _run_ssh(
        self,
        local_cfg: dict,
        external_ip: str,
        remote_cmd: str,
        *,
        timeout: int = 15,
        stdin_text: str | None = None,
    ):
        import subprocess

        return subprocess.run(
            self._ssh_base_command(local_cfg)
            + [self._ssh_target(local_cfg, external_ip), remote_cmd],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _query_bgp_summary(self, external_ip: str, local_cfg: dict) -> dict | None:
        import json

        result = self._run_ssh(
            local_cfg,
            external_ip,
            "sudo vtysh -c 'show bgp summary json'",
            timeout=15,
        )
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    def _query_bgp_advertised_routes(
        self,
        external_ip: str,
        peer_ip: str,
        local_cfg: dict,
    ) -> dict | None:
        import json

        result = self._run_ssh(
            local_cfg,
            external_ip,
            f"sudo vtysh -c 'show bgp ipv4 unicast neighbors {peer_ip} advertised-routes json'",
            timeout=15,
        )
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    def _expected_advertised_prefixes(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
    ) -> dict[str, dict[str, set[str]]]:
        defaults_mode = (
            self._normalize_value(
                (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode")
            )
            or "bgp"
        )
        normalized_local_prefixes = {
            str(network)
            for prefix in (local_cfg.get("gateway", {}).get("local_prefixes") or [])
            if (network := self._parse_prefix(prefix)) is not None
        }
        expected_by_host: dict[str, dict[str, set[str]]] = {
            inst_cfg.hostname: {} for inst_cfg in plan.iter_instance_configs()
        }
        instances_by_index = {
            inst_cfg.instance_index: inst_cfg for inst_cfg in plan.iter_instance_configs()
        }

        for conn in local_cfg.get("connections", []) or []:
            if self._normalize_value(conn.get("routing_mode") or defaults_mode) != "bgp":
                continue

            conn_bgp = conn.get("bgp", {}) or {}
            advertised_prefixes = (
                normalized_local_prefixes
                if conn_bgp.get("advertise_local_prefixes", True)
                else set()
            )

            for tunnel in conn.get("tunnels", []) or []:
                if self._normalize_value(tunnel.get("ha_role") or "active") == "disable":
                    continue

                peer_ip = (
                    ((tunnel.get("bgp", {}) or {}).get("remote_ip"))
                    or tunnel.get("inner_remote_ip")
                    or ""
                )
                if not peer_ip:
                    continue

                instance_index = int(tunnel.get("gateway_instance_index", 0) or 0)
                inst_cfg = instances_by_index.get(instance_index)
                if not inst_cfg:
                    continue

                expected_by_host.setdefault(inst_cfg.hostname, {})[peer_ip] = set(
                    advertised_prefixes
                )

        return expected_by_host

    @staticmethod
    def _parse_prefix(prefix: str) -> ipaddress.IPv4Network | None:
        try:
            return ipaddress.ip_network(prefix, strict=False)
        except Exception:
            return None

    @staticmethod
    def _path_nexthops(path: dict) -> set[str]:
        return {
            str(nh_ip)
            for nh in path.get("nexthops", []) or []
            if (nh_ip := nh.get("ip")) and nh_ip != "0.0.0.0"
        }

    @staticmethod
    def _sort_prefix_targets(prefix_targets: dict[str, str]) -> dict[str, str]:
        def _sort_key(item: tuple[str, str]) -> tuple[int, int, str]:
            prefix, _alloc_id = item
            network = ipaddress.ip_network(prefix, strict=False)
            return (int(network.network_address), network.prefixlen, prefix)

        return dict(sorted(prefix_targets.items(), key=_sort_key))

    def _summarize_prefix_targets(self, prefix_targets: dict[str, str]) -> dict[str, str]:
        grouped: dict[str, list[ipaddress.IPv4Network]] = {}
        for prefix, alloc_id in prefix_targets.items():
            try:
                grouped.setdefault(alloc_id, []).append(ipaddress.ip_network(prefix, strict=False))
            except Exception:
                continue

        summarized: dict[str, str] = {}
        for alloc_id, networks in grouped.items():
            for collapsed in ipaddress.collapse_addresses(networks):
                summarized[str(collapsed)] = alloc_id

        return self._sort_prefix_targets(summarized)

    @staticmethod
    def _route_destination_network(route) -> ipaddress.IPv4Network | None:
        spec = getattr(route, "spec", None)
        destination = getattr(spec, "destination", None) if spec else None
        cidr = getattr(destination, "cidr", None) if destination else None
        if not cidr:
            return None
        try:
            return ipaddress.ip_network(cidr, strict=False)
        except Exception:
            return None

    @staticmethod
    def _route_next_hop_allocation_id(route) -> str | None:
        spec = getattr(route, "spec", None)
        next_hop = getattr(spec, "next_hop", None) if spec else None
        allocation = getattr(next_hop, "allocation", None) if next_hop else None
        allocation_id = getattr(allocation, "id", None) if allocation else None
        return str(allocation_id) if allocation_id else None

    @staticmethod
    def _route_name(route) -> str:
        metadata = getattr(route, "metadata", None)
        return str(getattr(metadata, "name", None) or "")

    def _find_redundant_managed_routes(
        self,
        existing_routes,
        desired_prefix_targets: dict[str, str],
    ) -> list[object]:
        desired_by_alloc: dict[str, list[ipaddress.IPv4Network]] = {}
        desired_prefixes = set(desired_prefix_targets)

        for prefix, alloc_id in desired_prefix_targets.items():
            try:
                desired_by_alloc.setdefault(alloc_id, []).append(
                    ipaddress.ip_network(prefix, strict=False)
                )
            except Exception:
                continue

        redundant = []
        for route in existing_routes:
            route_name = self._route_name(route)
            if not route_name.startswith("vpngw-"):
                continue

            alloc_id = self._route_next_hop_allocation_id(route)
            if not alloc_id:
                continue

            route_network = self._route_destination_network(route)
            if route_network is None:
                continue

            route_prefix = str(route_network)
            if route_prefix in desired_prefixes:
                continue

            desired_networks = desired_by_alloc.get(alloc_id) or []
            if any(route_network.subnet_of(summary_net) for summary_net in desired_networks):
                redundant.append(route)

        return redundant

    def _delete_routes(
        self,
        rstub,
        route_service_pb2,
        routes_to_delete,
        *,
        route_table_id: str,
    ) -> int:
        deleted = 0
        for route in routes_to_delete:
            route_id = getattr(getattr(route, "metadata", None), "id", None)
            if not route_id:
                continue

            dest_network = self._route_destination_network(route)
            dest_label = str(dest_network) if dest_network else "unknown"
            try:
                rstub.Delete(route_service_pb2.DeleteRouteRequest(id=route_id))
                print(
                    f"[green]Deleted redundant managed route {dest_label} from {route_table_id}[/green]"
                )
                deleted += 1
            except Exception as e:
                print(
                    f"[yellow]Failed to delete redundant managed route {dest_label} "
                    f"from {route_table_id}: {e}[/yellow]"
                )
        return deleted

    @staticmethod
    def _bgp_advertisements_need_refresh(
        expected_by_peer: dict[str, set[str]],
        observed_peers: set[str],
        observed_prefixes_by_peer: dict[str, set[str]],
    ) -> bool:
        if observed_peers != set(expected_by_peer):
            return True

        for peer_ip, expected_prefixes in expected_by_peer.items():
            observed_prefixes = observed_prefixes_by_peer.get(peer_ip)
            if observed_prefixes is None:
                continue
            if observed_prefixes != expected_prefixes:
                return True

        return False

    def _collect_observed_bgp_advertisements(
        self,
        external_ip: str,
        local_cfg: dict,
    ) -> tuple[set[str], dict[str, set[str]]] | None:
        bgp_summary = self._query_bgp_summary(external_ip, local_cfg)
        if not bgp_summary:
            return None

        peers = bgp_summary.get("ipv4Unicast", {}).get("peers", {}) or {}
        observed_peers = set(peers)
        observed_prefixes_by_peer: dict[str, set[str]] = {}

        for peer_ip, peer_info in peers.items():
            if peer_info.get("state") != "Established":
                continue

            adv_data = self._query_bgp_advertised_routes(external_ip, peer_ip, local_cfg)
            if adv_data is None:
                continue

            advertised_routes = adv_data.get("advertisedRoutes", {}) or {}
            observed_prefixes_by_peer[peer_ip] = set(advertised_routes)

        return observed_peers, observed_prefixes_by_peer

    def _reload_runtime_config(self, inst_cfg, local_cfg: dict) -> bool:
        remote_tmp_path = f"/tmp/nebius-config-{inst_cfg.instance_index}.yaml"
        upload_result = self._run_ssh(
            local_cfg,
            inst_cfg.external_ip,
            f"cat > {remote_tmp_path}",
            timeout=20,
            stdin_text=inst_cfg.config_yaml,
        )
        if upload_result.returncode != 0:
            print(
                f"[yellow]Failed to upload refreshed config to {inst_cfg.hostname}: "
                f"{upload_result.stderr.strip() or upload_result.stdout.strip()}[/yellow]"
            )
            return False

        reload_cmd = (
            "sudo mkdir -p /etc/nebius-vpngw"
            f" && sudo mv {remote_tmp_path} /etc/nebius-vpngw/config-resolved.yaml"
            " && sudo chown root:root /etc/nebius-vpngw/config-resolved.yaml"
            " && sudo chmod 0644 /etc/nebius-vpngw/config-resolved.yaml"
            " && (sudo systemctl is-active --quiet nebius-vpngw-agent"
            " && sudo systemctl reload nebius-vpngw-agent"
            " || sudo systemctl start nebius-vpngw-agent)"
        )
        reload_result = self._run_ssh(
            local_cfg,
            inst_cfg.external_ip,
            reload_cmd,
            timeout=30,
        )
        if reload_result.returncode != 0:
            print(
                f"[yellow]Failed to reload nebius-vpngw-agent on {inst_cfg.hostname}: "
                f"{reload_result.stderr.strip() or reload_result.stdout.strip()}[/yellow]"
            )
            return False

        return True

    def ensure_bgp_advertisements_current(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
    ) -> None:
        import time

        expected_by_host = self._expected_advertised_prefixes(plan, local_cfg)
        if not expected_by_host:
            return

        print("[bold]Checking live BGP advertisements against current YAML...[/bold]")

        for inst_cfg in plan.iter_instance_configs():
            if not inst_cfg.external_ip:
                continue

            expected_by_peer = expected_by_host.get(inst_cfg.hostname, {})
            print(
                f"[cyan]  • Inspecting gateway {inst_cfg.hostname} ({inst_cfg.external_ip})...[/cyan]"
            )
            print("[dim]    Step 1/3: Querying current BGP advertisements[/dim]")
            observed_state = self._collect_observed_bgp_advertisements(
                inst_cfg.external_ip,
                local_cfg,
            )
            if observed_state is None:
                print(
                    f"[yellow]    Could not query live BGP advertisements from {inst_cfg.hostname}. "
                    "Skipping reconciliation for this gateway.[/yellow]"
                )
                continue

            observed_peers, observed_prefixes_by_peer = observed_state
            if not self._bgp_advertisements_need_refresh(
                expected_by_peer,
                observed_peers,
                observed_prefixes_by_peer,
            ):
                print(
                    f"[green]    ✓ Live BGP advertisements on {inst_cfg.hostname} already match the current YAML.[/green]"
                )
                continue

            expected_prefixes = sorted(
                {prefix for prefixes in expected_by_peer.values() for prefix in prefixes}
            )
            print(
                f"[yellow]    Detected stale BGP advertisement state on {inst_cfg.hostname}.[/yellow]"
            )
            if expected_prefixes:
                print(
                    "[dim]    Expected advertised prefixes from current YAML: "
                    f"{', '.join(expected_prefixes)}[/dim]"
                )

            print("[dim]    Step 2/3: Uploading current resolved config and reloading agent[/dim]")
            if not self._reload_runtime_config(inst_cfg, local_cfg):
                continue

            print("[dim]    Step 3/3: Re-checking live BGP advertisements[/dim]")
            time.sleep(3)
            refreshed_state = self._collect_observed_bgp_advertisements(
                inst_cfg.external_ip,
                local_cfg,
            )
            if refreshed_state is None:
                print(
                    f"[yellow]    Reloaded {inst_cfg.hostname}, but could not verify live BGP advertisements yet.[/yellow]"
                )
                continue

            refreshed_peers, refreshed_prefixes_by_peer = refreshed_state
            if self._bgp_advertisements_need_refresh(
                expected_by_peer,
                refreshed_peers,
                refreshed_prefixes_by_peer,
            ):
                print(
                    f"[yellow]    {inst_cfg.hostname} still advertises prefixes that do not match the current YAML. "
                    "Run 'nebius-vpngw apply' if this persists.[/yellow]"
                )
                continue

            print(
                f"[green]    ✓ Refreshed live BGP advertisements on {inst_cfg.hostname} to match the current YAML.[/green]"
            )

    def _resolve_target_network_id(self, channel, local_cfg: dict) -> str | None:
        from nebius.api.nebius.vpc.v1 import (
            network_service_pb2,
            network_service_pb2_grpc,
            subnet_service_pb2,
            subnet_service_pb2_grpc,
        )

        gateway_group = local_cfg.get("gateway_group", {}) or {}
        explicit_network_id = str(gateway_group.get("network_id") or "").strip()
        if explicit_network_id:
            return explicit_network_id

        subnet_name = self._gateway_subnet_name(local_cfg)
        sstub = subnet_service_pb2_grpc.SubnetServiceStub(channel)
        try:
            subnet_obj = sstub.GetByName(
                subnet_service_pb2.GetSubnetByNameRequest(
                    parent_id=self.project_id or "",
                    name=subnet_name,
                )
            )
            subnet_spec = getattr(subnet_obj, "spec", None)
            subnet_network_id = getattr(subnet_spec, "network_id", None)
            if subnet_network_id:
                return subnet_network_id
        except Exception:
            pass

        nstub = network_service_pb2_grpc.NetworkServiceStub(channel)
        try:
            network_obj = nstub.GetByName(
                network_service_pb2.GetNetworkByNameRequest(
                    parent_id=self.project_id or "",
                    name="default-network",
                )
            )
            network_id = getattr(network_obj, "id", None) or getattr(
                getattr(network_obj, "metadata", None),
                "id",
                None,
            )
            if network_id:
                return network_id
        except Exception:
            pass

        try:
            networks = nstub.List(
                network_service_pb2.ListNetworksRequest(parent_id=self.project_id or "")
            ).items
        except Exception:
            return None

        if len(networks) == 1:
            network_obj = networks[0]
            return getattr(network_obj, "id", None) or getattr(
                getattr(network_obj, "metadata", None),
                "id",
                None,
            )

        return None

    def _select_local_prefix_subnets(
        self,
        subnets,
        gateway_prefixes: list[ipaddress.IPv4Network],
        *,
        target_network_id: str,
        gateway_subnet_name: str,
    ) -> tuple[list[object], list[tuple[str, list[str]]]]:
        selected: list[object] = []
        inherited_selected: list[tuple[str, list[str]]] = []

        for subnet_obj in subnets:
            subnet_spec = getattr(subnet_obj, "spec", None)
            subnet_network_id = getattr(subnet_spec, "network_id", None)
            if subnet_network_id != target_network_id:
                continue

            subnet_name = getattr(getattr(subnet_obj, "metadata", None), "name", None) or ""
            if subnet_name == gateway_subnet_name:
                continue

            effective_cidrs = self._effective_subnet_cidrs(subnet_obj)
            if not any(
                effective_cidr.overlaps(prefix)
                for effective_cidr in effective_cidrs
                for prefix in gateway_prefixes
            ):
                continue

            selected.append(subnet_obj)
            if self._subnet_uses_network_pools(subnet_obj):
                inherited_selected.append(
                    (subnet_name, [str(effective_cidr) for effective_cidr in effective_cidrs])
                )

        return selected, inherited_selected

    def list_routes(self, plan: ResolvedDeploymentPlan, local_cfg: dict) -> None:
        """List route tables attached to subnets matching gateway.local_prefixes."""
        try:
            channel = self._channel()
        except Exception as e:
            print(f"[red]Failed to open VPC SDK channel:[/red] {e}")
            return
        from nebius.api.nebius.vpc.v1 import (
            route_service_pb2,
            route_service_pb2_grpc,
            subnet_service_pb2,
            subnet_service_pb2_grpc,
        )
        from rich.console import Console
        from rich.table import Table

        gateway_prefixes = [
            ipaddress.ip_network(p)
            for p in (local_cfg.get("gateway", {}).get("local_prefixes") or [])
        ]
        if not gateway_prefixes:
            print("[yellow]No gateway.local_prefixes; nothing to list.[/yellow]")
            return

        # Allocation mapping for pretty-printing next hop
        _, alloc_to_ip = self._list_allocations(channel)

        sstub = subnet_service_pb2_grpc.SubnetServiceStub(channel)
        rstub = route_service_pb2_grpc.RouteServiceStub(channel)
        console = Console()
        gateway_subnet_name = self._gateway_subnet_name(local_cfg)
        target_network_id = self._resolve_target_network_id(channel, local_cfg)
        if not target_network_id:
            print(
                "[yellow]Could not resolve the target network for route listing. "
                "Set gateway_group.network_id explicitly.[/yellow]"
            )
            return

        subnets = sstub.List(
            subnet_service_pb2.ListSubnetsRequest(parent_id=self.project_id or "")
        ).items
        selected_subnets, inherited_selected = self._select_local_prefix_subnets(
            subnets,
            gateway_prefixes,
            target_network_id=target_network_id,
            gateway_subnet_name=gateway_subnet_name,
        )

        for subnet_name, inherited_cidrs in inherited_selected:
            print(
                f"[dim]Subnet {subnet_name} inherits parent network pools "
                f"(use_network_pools=true); matching via effective CIDRs: "
                f"{', '.join(inherited_cidrs)}[/dim]"
            )

        if not selected_subnets:
            print(
                "[yellow]No workload subnets matched gateway.local_prefixes "
                f"in network {target_network_id}.[/yellow]"
            )

        for sn in selected_subnets:
            subnet_cidrs = [str(net) for net in self._effective_subnet_cidrs(sn)]

            rt_id = sn.status.route_table.id
            rt_default = sn.status.route_table.default

            print(
                f"\n[bold cyan]Subnet: {sn.metadata.name}[/bold cyan] ({', '.join(subnet_cidrs)})"
            )

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

        # Add BGP advertised routes section
        self._list_bgp_advertised_routes(plan, local_cfg, console)

    def _list_bgp_advertised_routes(
        self, plan: ResolvedDeploymentPlan, local_cfg: dict, console
    ) -> None:
        """List BGP routes being advertised from gateway to peer routers.

        Shows what routes are announced to remote sites, organized by connection and tunnel.
        """
        from rich.table import Table

        print(
            "\n[bold magenta]BGP Routes Advertised to Peer Routers (Local → Remote)[/bold magenta]"
        )

        # Get routing mode and connections
        defaults_mode = (
            self._normalize_value(
                (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode")
            )
            or "bgp"
        )
        connections = local_cfg.get("connections", [])

        # Check if BGP is enabled for any connection
        has_bgp = any(
            self._normalize_value(conn.get("routing_mode") or defaults_mode) == "bgp"
            for conn in connections
        )

        if not has_bgp:
            print("[dim]No BGP connections configured - routes are static[/dim]")
            return

        self.ensure_bgp_advertisements_current(plan, local_cfg)

        # Query each gateway VM
        for inst_cfg in plan.iter_instance_configs():
            hostname = inst_cfg.hostname
            external_ip = inst_cfg.external_ip

            if not external_ip:
                print(f"[yellow]Skipping {hostname}: no external IP[/yellow]")
                continue

            print(f"\n[bold cyan]Gateway VM: {hostname} ({external_ip})[/bold cyan]")

            # Query BGP summary to get peer list
            try:
                bgp_summary = self._query_bgp_summary(external_ip, local_cfg)
                if bgp_summary is None:
                    print(f"[yellow]Failed to query BGP summary from {hostname}[/yellow]")
                    continue

                ipv4_peers = bgp_summary.get("ipv4Unicast", {}).get("peers", {})

                if not ipv4_peers:
                    print("[dim]No BGP peers configured or active[/dim]")
                    continue

                # For each peer, query advertised routes
                for peer_ip, peer_info in sorted(ipv4_peers.items()):
                    peer_state = peer_info.get("state", "Unknown")
                    peer_asn = peer_info.get("remoteAs", "?")

                    # Find connection and tunnel name for this peer
                    conn_name, tunnel_name, inner_local_ip, ha_role = (
                        self._find_connection_for_peer(peer_ip, connections, inst_cfg)
                    )
                    role_label = ha_role or "unknown"

                    if peer_state != "Established":
                        print(
                            f"\n[bold]Connection: {conn_name} | Tunnel: {tunnel_name} ({role_label})[/bold]"
                        )
                        print(
                            f"[yellow]  BGP Peer {peer_ip} (ASN {peer_asn}): {peer_state}[/yellow]"
                        )
                        continue

                    # Query advertised routes to this peer
                    adv_data = self._query_bgp_advertised_routes(external_ip, peer_ip, local_cfg)
                    if adv_data is None:
                        print(
                            f"\n[bold]Connection: {conn_name} | Tunnel: {tunnel_name} ({role_label})[/bold]"
                        )
                        print(f"[yellow]  Failed to query advertised routes to {peer_ip}[/yellow]")
                        continue

                    advertised_routes = adv_data.get("advertisedRoutes", {})

                    if not advertised_routes:
                        print(
                            f"\n[bold]Connection: {conn_name} | Tunnel: {tunnel_name} ({role_label})[/bold]"
                        )
                        print(
                            f"[dim]  BGP Peer {peer_ip} (ASN {peer_asn}): No routes advertised[/dim]"
                        )
                        continue

                    # Build table for this peer
                    print(
                        f"\n[bold]Connection: {conn_name} | Tunnel: {tunnel_name} ({role_label})[/bold]"
                    )

                    # Get local ASN from adv_data
                    local_asn = adv_data.get("localAS", "")

                    table = Table(title=f"→ Peer {peer_ip} (ASN {peer_asn})")
                    table.add_column("Prefix", style="cyan")
                    table.add_column("Next-Hop", style="blue")
                    table.add_column("AS Path", style="yellow")
                    table.add_column("Origin", style="green")

                    for prefix, route_info in sorted(advertised_routes.items()):
                        next_hop = route_info.get("nextHop", "-")
                        # Replace 0.0.0.0 with actual XFRM interface IP
                        if next_hop == "0.0.0.0":
                            next_hop = inner_local_ip

                        # AS path is empty for locally originated routes
                        # When sent on wire, FRR automatically prepends local ASN
                        as_path = route_info.get("path", "")
                        if not as_path and local_asn:
                            as_path = str(local_asn)  # Show local ASN for clarity
                        elif not as_path:
                            as_path = "local"

                        # For user clarity, show "BGP" for locally originated routes (IGP origin code)
                        # since these are routes injected via BGP 'network' statement
                        origin = route_info.get("origin", "?")
                        if origin == "IGP":
                            origin = "BGP"

                        table.add_row(prefix, next_hop, as_path, origin)

                    console.print(table)

            except Exception as e:
                print(f"[yellow]Error querying BGP data: {e}[/yellow]")

    def _find_connection_for_peer(self, peer_ip: str, connections: list[dict], inst_cfg) -> tuple:
        """Find connection and tunnel name for a BGP peer IP.

        Returns (connection_name, tunnel_name, inner_local_ip, ha_role) tuple.
        """
        inst_index = getattr(inst_cfg, "instance_index", None)

        # Peer IP is the tunnel's inner_remote_ip (APIPA address on the tunnel interface)
        for conn in connections:
            conn_name = conn.get("name", "unnamed")
            tunnels = conn.get("tunnels", [])

            for tunnel in tunnels:
                if self._normalize_value(tunnel.get("ha_role") or "active") == "disable":
                    continue
                if inst_index is not None:
                    try:
                        tunnel_inst_index = int(tunnel.get("gateway_instance_index", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if tunnel_inst_index != inst_index:
                        continue

                # Check inner_remote_ip (the remote peer's BGP IP on the tunnel)
                inner_remote = tunnel.get("inner_remote_ip", "")

                # Also check bgp.remote_ip if present (alternative config format)
                bgp_cfg = tunnel.get("bgp", {})
                bgp_remote = bgp_cfg.get("remote_ip", "")

                if inner_remote == peer_ip or bgp_remote == peer_ip:
                    tunnel_name = tunnel.get("name") or "unnamed-tunnel"
                    inner_local_ip = tunnel.get("inner_local_ip", "0.0.0.0")
                    ha_role = self._normalize_value(tunnel.get("ha_role") or "active") or "active"
                    return (conn_name, tunnel_name, inner_local_ip, ha_role)

        # Fallback if not found
        return ("unknown", "unknown", "0.0.0.0", "unknown")

    def add_routes(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        *,
        summarize: bool = False,
    ) -> None:
        """Ensure routes for connection.remote_prefixes and assign custom route tables when needed."""
        try:
            channel = self._channel()
        except Exception as e:
            print(f"[red]Failed to open VPC SDK channel:[/red] {e}")
            return

        # Create compute channel using same auth pattern as VPC channel
        try:
            import os

            import grpc  # type: ignore

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

        from nebius.api.nebius.common.v1 import metadata_pb2
        from nebius.api.nebius.vpc.v1 import (
            route_pb2,
            route_service_pb2,
            route_service_pb2_grpc,
            route_table_pb2,
            route_table_service_pb2,
            route_table_service_pb2_grpc,
            subnet_pb2,
            subnet_service_pb2,
            subnet_service_pb2_grpc,
        )

        gateway_prefixes = [
            ipaddress.ip_network(p)
            for p in (local_cfg.get("gateway", {}).get("local_prefixes") or [])
        ]
        if not gateway_prefixes:
            print("[yellow]No gateway.local_prefixes; cannot determine relevant subnets.[/yellow]")
            return

        allocations_by_index: dict[int, str] = {}
        if compute_channel:
            allocations_by_index = self._find_gateway_private_allocations_by_index(
                compute_channel, plan
            )
        if not allocations_by_index:
            print(
                "[yellow]Could not resolve private gateway allocations; cannot create routes.[/yellow]"
            )
            return

        try:
            prefix_targets = self._collect_remote_prefix_targets(
                plan, local_cfg, allocations_by_index
            )
        except ValueError as e:
            print(f"[red]{e}[/red]")
            return

        if not prefix_targets:
            print(
                "[yellow]No remote prefixes found (BGP: no learned routes; Static: no configured remote_prefixes)[/yellow]"
            )
            return

        # Filter out local prefixes (don't create routes for our own VPC networks)
        local_networks = [
            ipaddress.ip_network(p)
            for p in (local_cfg.get("gateway", {}).get("local_prefixes") or [])
        ]
        filtered_prefix_targets: dict[str, str] = {}
        for pfx, alloc_id in prefix_targets.items():
            try:
                pfx_net = ipaddress.ip_network(pfx)
                # Skip if this prefix overlaps with any local prefix
                is_local = any(pfx_net.overlaps(local_net) for local_net in local_networks)
                if is_local:
                    print(f"[dim]Skipping {pfx} (overlaps with local_prefixes)[/dim]")
                    continue
                filtered_prefix_targets[str(pfx_net)] = alloc_id
            except Exception:
                continue

        prefix_targets = self._sort_prefix_targets(filtered_prefix_targets)

        if not prefix_targets:
            print(
                "[yellow]No remote prefixes to add (all learned routes are local networks)[/yellow]"
            )
            return

        original_prefix_count = len(prefix_targets)
        if summarize:
            summarized_targets = self._summarize_prefix_targets(prefix_targets)
            if len(summarized_targets) < original_prefix_count:
                print(
                    f"[cyan]Summarized remote prefixes from {original_prefix_count} "
                    f"to {len(summarized_targets)} exact route(s)[/cyan]"
                )
            else:
                print("[dim]Summarization produced no exact route reduction[/dim]")
            prefix_targets = summarized_targets

        print(f"[cyan]Found {len(prefix_targets)} remote prefix(es) to add as VPC routes[/cyan]")

        sstub = subnet_service_pb2_grpc.SubnetServiceStub(channel)
        rtstub = route_table_service_pb2_grpc.RouteTableServiceStub(channel)
        rstub = route_service_pb2_grpc.RouteServiceStub(channel)
        gateway_subnet_name = self._gateway_subnet_name(local_cfg)
        target_network_id = self._resolve_target_network_id(channel, local_cfg)
        if not target_network_id:
            print(
                "[yellow]Could not resolve the target network for route updates. "
                "Set gateway_group.network_id explicitly.[/yellow]"
            )
            return

        subnets = sstub.List(
            subnet_service_pb2.ListSubnetsRequest(parent_id=self.project_id or "")
        ).items
        selected_subnets, inherited_selected = self._select_local_prefix_subnets(
            subnets,
            gateway_prefixes,
            target_network_id=target_network_id,
            gateway_subnet_name=gateway_subnet_name,
        )

        for subnet_name, inherited_cidrs in inherited_selected:
            print(
                f"[dim]Subnet {subnet_name} inherits parent network pools "
                f"(use_network_pools=true); matching via effective CIDRs: "
                f"{', '.join(inherited_cidrs)}[/dim]"
            )

        if not selected_subnets:
            print(
                "[yellow]No workload subnets matched gateway.local_prefixes "
                f"in network {target_network_id}. Nothing to update.[/yellow]"
            )
            return

        for sn in selected_subnets:
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
                    print(
                        f"[green]Using existing route table {rt_id} ({rt_name}) for subnet {sn.metadata.name}[/green]"
                    )
                    # Attach to subnet if not already attached
                    if rt_info.id != rt_id:
                        try:
                            # Preserve existing IP pool configuration when updating route table
                            existing_ipv4_private_pools = getattr(
                                sn.spec, "ipv4_private_pools", None
                            )
                            existing_ipv4_public_pools = getattr(sn.spec, "ipv4_public_pools", None)

                            sstub.Update(
                                subnet_service_pb2.UpdateSubnetRequest(
                                    metadata=metadata_pb2.ResourceMetadata(id=sn.metadata.id),
                                    spec=subnet_pb2.SubnetSpec(
                                        network_id=sn.spec.network_id,
                                        route_table_id=rt_id,
                                        ipv4_private_pools=existing_ipv4_private_pools,
                                        ipv4_public_pools=existing_ipv4_public_pools,
                                    ),
                                )
                            )
                            print(
                                f"[green]Attached route table {rt_id} to subnet {sn.metadata.name}[/green]"
                            )
                        except Exception as e:
                            print(
                                f"[yellow]Failed to attach route table to subnet {sn.metadata.name}: {e}[/yellow]"
                            )
                            continue
                else:
                    # Create new route table and copy routes from default RT
                    print(f"[yellow]⚠ Subnet {sn.metadata.name} uses default route table[/yellow]")
                    print(
                        f"[yellow]  Creating custom route table '{rt_name}' to add VPN routes[/yellow]"
                    )

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
                            print(
                                f"[red]Route table create returned no resource_id for subnet {sn.metadata.name}; skipping.[/red]"
                            )
                            continue

                        # Copy existing routes from default route table
                        if default_rt_id:
                            try:
                                default_routes = rstub.List(
                                    route_service_pb2.ListRoutesRequest(parent_id=default_rt_id)
                                ).items

                                if default_routes:
                                    print(
                                        f"[cyan]  Copying {len(default_routes)} route(s) from default route table...[/cyan]"
                                    )
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
                                            print(
                                                f"[dim]  Could not copy route {dr.spec.destination.cidr}: {copy_err}[/dim]"
                                            )
                                else:
                                    print("[dim]  No routes in default route table to copy[/dim]")
                            except Exception as list_err:
                                print(
                                    f"[yellow]  Could not list default route table routes: {list_err}[/yellow]"
                                )

                        # Attach to subnet - preserve IP pool configuration
                        existing_ipv4_private_pools = getattr(sn.spec, "ipv4_private_pools", None)
                        existing_ipv4_public_pools = getattr(sn.spec, "ipv4_public_pools", None)

                        sstub.Update(
                            subnet_service_pb2.UpdateSubnetRequest(
                                metadata=metadata_pb2.ResourceMetadata(id=sn.metadata.id),
                                spec=subnet_pb2.SubnetSpec(
                                    network_id=sn.spec.network_id,
                                    route_table_id=new_rt_id,
                                    ipv4_private_pools=existing_ipv4_private_pools,
                                    ipv4_public_pools=existing_ipv4_public_pools,
                                ),
                            )
                        )
                        rt_id = new_rt_id
                        print(
                            f"[green]✓ Created custom route table {rt_id} and attached to subnet {sn.metadata.name}[/green]"
                        )
                        print(
                            "[yellow]  NOTE: Future changes to the default route table will NOT apply to this subnet.[/yellow]"
                        )
                        print(
                            f"[yellow]  Add any required routes manually to route table: {rt_id}[/yellow]"
                        )
                    except Exception as e:
                        print(
                            f"[yellow]Failed to create/attach route table for subnet {sn.metadata.name}: {e}[/yellow]"
                        )
                        continue

            # Get existing routes to check for duplicates (idempotency)
            try:
                existing_routes = rstub.List(
                    route_service_pb2.ListRoutesRequest(parent_id=rt_id)
                ).items
                existing_route_cidrs = {r.spec.destination.cidr for r in existing_routes}
            except Exception as e:
                print(f"[yellow]Failed to list existing routes on {rt_id}: {e}[/yellow]")
                existing_routes = []
                existing_route_cidrs = set()

            # Add routes for each remote prefix (skip if already exists)
            for pfx, alloc_id in prefix_targets.items():
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
                    existing_route_cidrs.add(pfx)
                except Exception as e:
                    err_str = str(e).lower()
                    if "already exists" in err_str or "duplicate" in err_str:
                        print(f"[blue]Route {pfx} already exists on {rt_id}; skipping[/blue]")
                        existing_route_cidrs.add(pfx)
                    elif summarize and (
                        "max-route-count" in err_str or "quota exceeded" in err_str
                    ):
                        redundant_routes = self._find_redundant_managed_routes(
                            existing_routes,
                            {pfx: alloc_id},
                        )
                        if not redundant_routes:
                            print(f"[yellow]Failed to add route {pfx} on {rt_id}: {e}[/yellow]")
                            continue

                        print(
                            f"[yellow]Route table {rt_id} hit its route limit while adding {pfx}. "
                            f"Deleting {len(redundant_routes)} covered vpngw-managed route(s) and retrying.[/yellow]"
                        )
                        deleted = self._delete_routes(
                            rstub,
                            route_service_pb2,
                            redundant_routes,
                            route_table_id=rt_id,
                        )
                        if not deleted:
                            print(f"[yellow]Failed to add route {pfx} on {rt_id}: {e}[/yellow]")
                            continue

                        existing_routes = [
                            route
                            for route in existing_routes
                            if getattr(getattr(route, "metadata", None), "id", None)
                            not in {
                                getattr(getattr(dead_route, "metadata", None), "id", None)
                                for dead_route in redundant_routes
                            }
                        ]
                        existing_route_cidrs = {r.spec.destination.cidr for r in existing_routes}

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
                            print(
                                f"[green]Added summarized route {pfx} -> allocation {alloc_id} "
                                f"on {rt_id} after pruning redundant specifics[/green]"
                            )
                            existing_route_cidrs.add(pfx)
                        except Exception as retry_err:
                            print(
                                f"[yellow]Failed to add summarized route {pfx} on {rt_id} "
                                f"after pruning redundant specifics: {retry_err}[/yellow]"
                            )
                    else:
                        print(f"[yellow]Failed to add route {pfx} on {rt_id}: {e}[/yellow]")

            if summarize:
                effective_prefix_targets = {
                    pfx: alloc_id
                    for pfx, alloc_id in prefix_targets.items()
                    if pfx in existing_route_cidrs
                }
                redundant_routes = self._find_redundant_managed_routes(
                    existing_routes,
                    effective_prefix_targets,
                )
                if redundant_routes:
                    print(
                        f"[cyan]Pruning {len(redundant_routes)} redundant vpngw-managed "
                        f"route(s) from {rt_id} after summary reconciliation[/cyan]"
                    )
                    deleted = self._delete_routes(
                        rstub,
                        route_service_pb2,
                        redundant_routes,
                        route_table_id=rt_id,
                    )
                    if deleted:
                        existing_routes = [
                            route
                            for route in existing_routes
                            if getattr(getattr(route, "metadata", None), "id", None)
                            not in {
                                getattr(getattr(dead_route, "metadata", None), "id", None)
                                for dead_route in redundant_routes
                            }
                        ]

    def _get_bgp_learned_routes(
        self, plan: ResolvedDeploymentPlan, conn: dict, local_cfg: dict
    ) -> list[str]:
        """Query FRR on gateway VMs to get BGP-learned routes (filtered by whitelist if configured)."""
        import ipaddress
        import json
        import subprocess

        conn_name = conn.get("name", "unnamed")
        whitelist = (
            conn.get("remote_prefixes") or (conn.get("bgp", {}) or {}).get("remote_prefixes") or []
        )

        # Create whitelist networks for matching
        whitelist_networks = []
        if whitelist:
            for pfx in whitelist:
                try:
                    whitelist_networks.append(ipaddress.ip_network(pfx))
                except Exception:
                    pass

        learned_prefixes = []

        target_instance_indices = set(self._connection_instance_indices(conn))

        # Query only the gateway VM(s) that own this connection
        for inst_cfg in plan.iter_instance_configs():
            if target_instance_indices and inst_cfg.instance_index not in target_instance_indices:
                continue
            hostname = inst_cfg.hostname
            external_ip = inst_cfg.external_ip
            peer_ips = self._connection_peer_ips(conn, instance_index=inst_cfg.instance_index)

            if not external_ip:
                print(f"[yellow]Skipping {hostname}: no external IP for BGP route query[/yellow]")
                continue

            try:
                result = subprocess.run(
                    [
                        "ssh",
                        "-o",
                        "StrictHostKeyChecking=no",
                        "-o",
                        "ConnectTimeout=10",
                        f"ubuntu@{external_ip}",
                        "sudo vtysh -c 'show bgp ipv4 unicast json'",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )

                if result.returncode != 0:
                    print(
                        f"[yellow]Failed to query BGP routes from {hostname}: {result.stderr}[/yellow]"
                    )
                    continue

                bgp_data = json.loads(result.stdout)
                routes = bgp_data.get("routes", {})

                for prefix, route_data in routes.items():
                    if isinstance(route_data, dict):
                        paths = [route_data]
                    elif isinstance(route_data, list):
                        paths = route_data
                    else:
                        continue

                    relevant_paths = []
                    for path in paths:
                        path_nexthops = self._path_nexthops(path)
                        if not path_nexthops:
                            continue
                        if peer_ips and not (path_nexthops & peer_ips):
                            continue
                        relevant_paths.append(path)

                    if not relevant_paths:
                        continue

                    # Apply whitelist filter if configured
                    if whitelist_networks:
                        try:
                            prefix_net = ipaddress.ip_network(prefix)
                            allowed = any(
                                prefix_net.subnet_of(wl) or prefix_net == wl
                                for wl in whitelist_networks
                            )
                            if not allowed:
                                continue
                        except Exception:
                            continue

                    if prefix not in learned_prefixes:
                        learned_prefixes.append(prefix)

                print(
                    f"[cyan]Learned {len(routes)} BGP route(s) from {hostname} (connection: {conn_name})[/cyan]"
                )

            except subprocess.TimeoutExpired:
                print(f"[yellow]Timeout querying BGP routes from {hostname}[/yellow]")
            except json.JSONDecodeError:
                print(f"[yellow]Failed to parse BGP JSON from {hostname}[/yellow]")
            except Exception as e:
                print(f"[yellow]Error querying BGP routes from {hostname}: {e}[/yellow]")

        return learned_prefixes

    def list_remote_routes(
        self,
        plan: ResolvedDeploymentPlan,
        local_cfg: dict,
        connection_filter: str | None = None,
    ) -> None:
        """List remote routes learned via BGP or configured as static routes.

        - BGP mode: Query FRR for learned routes and check against remote_prefixes whitelist
        - Static mode: Show static routes configured from remote_prefixes
        """
        from rich.console import Console

        console = Console()

        # Get routing mode and connections
        defaults_mode = (
            self._normalize_value(
                (local_cfg.get("defaults", {}).get("routing", {}) or {}).get("mode")
            )
            or "bgp"
        )
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
                routing_mode = self._normalize_value(conn.get("routing_mode") or defaults_mode)
                owner_indices = self._connection_instance_indices(conn)
                if owner_indices and inst_cfg.instance_index not in owner_indices:
                    continue
                remote_prefixes = conn.get("remote_prefixes", []) or (
                    conn.get("bgp", {}) or {}
                ).get("remote_prefixes", [])

                print(f"\n[bold]Connection: {conn_name}[/bold] (routing_mode: {routing_mode})")

                if routing_mode == "bgp":
                    self._list_bgp_routes(
                        hostname,
                        external_ip,
                        conn_name,
                        conn,
                        inst_cfg.instance_index,
                        remote_prefixes,
                        console,
                    )
                else:  # static mode
                    self._list_static_routes(
                        hostname, external_ip, conn_name, remote_prefixes, console
                    )

    def _list_bgp_routes(
        self,
        hostname: str,
        external_ip: str,
        conn_name: str,
        conn: dict,
        instance_index: int,
        whitelist: list[str],
        console,
    ) -> None:
        """Query FRR BGP routes and check against whitelist."""
        import ipaddress
        import json
        import re
        import subprocess

        from rich.table import Table

        peer_ips = self._connection_peer_ips(conn, instance_index=instance_index)
        if not peer_ips:
            print("[dim]No BGP tunnel peers configured on this gateway VM for the connection[/dim]")
            return

        # Query BGP routes via SSH
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=10",
                    f"ubuntu@{external_ip}",
                    "sudo vtysh -c 'show bgp ipv4 unicast json'",
                ],
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
                    path_nexthops = self._path_nexthops(path)
                    if path_nexthops & peer_ips:
                        unique_nexthops.update(path_nexthops & peer_ips)

            # Query interface for each unique next-hop
            for nh_ip in unique_nexthops:
                try:
                    route_result = subprocess.run(
                        [
                            "ssh",
                            "-o",
                            "StrictHostKeyChecking=no",
                            "-o",
                            "ConnectTimeout=5",
                            f"ubuntu@{external_ip}",
                            f"ip route get {nh_ip}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if route_result.returncode == 0:
                        # Parse output like: "169.254.5.153 dev xfrm1 src 169.254.5.154 uid 1000"
                        match = re.search(r"dev\s+(\S+)", route_result.stdout)
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
            row_count = 0

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
                    path_nexthops = self._path_nexthops(path)
                    if not path_nexthops or not (path_nexthops & peer_ips):
                        continue

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
                            allowed = any(
                                prefix_net.subnet_of(wl) or prefix_net == wl
                                for wl in whitelist_networks
                            )
                            status = "allowed" if allowed else "[red]not-allowed[/red]"
                        except Exception:
                            status = "unknown"
                    else:
                        status = "[dim]no-filter[/dim]"

                    table.add_row(prefix, nexthop_ip, via_iface, as_path, status)
                    row_count += 1

            if not row_count:
                print(
                    "[dim]No BGP routes currently learned from this connection's tunnel peer(s)[/dim]"
                )
                return

            console.print(table)

            if whitelist:
                print(f"[dim]Note: remote_prefixes whitelist has {len(whitelist)} entries[/dim]")
            else:
                print(
                    "[dim]Note: No remote_prefixes whitelist configured - all BGP routes accepted[/dim]"
                )

        except subprocess.TimeoutExpired:
            print(f"[yellow]Timeout querying BGP routes from {hostname}[/yellow]")
        except json.JSONDecodeError:
            print(f"[yellow]Failed to parse BGP JSON output from {hostname}[/yellow]")
        except Exception as e:
            print(f"[yellow]Error querying BGP routes: {e}[/yellow]")

    def _list_static_routes(
        self,
        hostname: str,
        external_ip: str,
        conn_name: str,
        remote_prefixes: list[str],
        console,
    ) -> None:
        """List static routes configured on gateway VM."""
        import subprocess

        from rich.table import Table

        if not remote_prefixes:
            print("[yellow]No remote_prefixes configured in YAML for this connection[/yellow]")
            return

        # Query kernel routing table via SSH
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=10",
                    f"ubuntu@{external_ip}",
                    "ip route show",
                ],
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
