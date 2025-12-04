from __future__ import annotations

import typing as t
import textwrap
import importlib.resources as resources

from ..config_loader import GatewayGroupSpec
from .vm_diff import VMDiffAnalyzer, VMSpec


class VMManager:
    """Manage Nebius gateway VM lifecycle.

    This is a scaffold placeholder. Integrate with Nebius Python SDK when available.
    """

    def __init__(
        self,
        project_id: t.Optional[str],
        zone: t.Optional[str],
        auth_token: t.Optional[str] = None,
        tenant_id: t.Optional[str] = None,
        region_id: t.Optional[str] = None,
    ) -> None:
        self.project_id = project_id
        self.zone = zone
        self.auth_token = auth_token
        self.tenant_id = tenant_id
        self.region_id = region_id
        self.diff_analyzer = VMDiffAnalyzer()
    
    def check_changes(self, spec: GatewayGroupSpec) -> t.List[t.Tuple[str, t.Any]]:
        """Check what changes would be applied without making them.
        
        Returns:
            List of (instance_name, VMDiff) tuples for all instances
        """
        print(f"[VMManager] Checking changes for {spec.instance_count} instance(s)...")
        
        # Setup SDK client (reuse logic from ensure_group)
        client = self._get_client()
        if client is None:
            print("[VMManager] Cannot check changes: SDK not available")
            return []
        
        results: t.List[t.Tuple[str, t.Any]] = []
        desired_spec = VMSpec.from_config(spec.vm_spec)
        
        for i in range(spec.instance_count):
            inst_name = f"{spec.name}-{i}"
            
            # Try to get existing VM and disk
            vm_obj = self._get_vm_by_name(client, inst_name)
            
            if vm_obj is None:
                # VM doesn't exist
                diff = self.diff_analyzer.compare(desired_spec, None)
                results.append((inst_name, diff))
                continue
            
            # Get boot disk
            boot_disk_name = f"{inst_name}-boot"
            disk_obj = self._get_disk_by_name(client, boot_disk_name)
            
            if disk_obj is None:
                print(f"[VMManager] Warning: VM {inst_name} exists but boot disk not found")
                diff = self.diff_analyzer.compare(desired_spec, None)
                results.append((inst_name, diff))
                continue
            
            # Extract actual spec from live resources
            actual_spec = VMSpec.from_live_vm(vm_obj, disk_obj)
            
            # Compare
            diff = self.diff_analyzer.compare(desired_spec, actual_spec)
            results.append((inst_name, diff))
        
        return results
    
    def _get_client(self) -> t.Optional[t.Any]:
        """Get Nebius SDK client (extracted from ensure_group for reuse)."""
        import os
        if self.auth_token and not os.environ.get("NEBIUS_IAM_TOKEN"):
            os.environ["NEBIUS_IAM_TOKEN"] = self.auth_token
        
        try:
            # Resolve Nebius SDK primary surface
            Client = None  # type: ignore
            try:
                from nebius.sdk import SDK as _C  # type: ignore
                Client = _C
            except Exception:
                try:
                    from nebius.sdk import Client as _C  # type: ignore
                    Client = _C
                except Exception:
                    try:
                        from nebius.client import Client as _C  # type: ignore
                        Client = _C
                    except Exception:
                        try:
                            from nebius import pysdk  # type: ignore
                            Client = pysdk.Client  # type: ignore[attr-defined]
                        except Exception:
                            try:
                                from nebius.pysdk import Client as _C  # type: ignore
                                Client = _C
                            except Exception:
                                pass
            if Client is None:
                return None
            
            # Initialize client
            if self.tenant_id and self.project_id and self.region_id:
                try:
                    return Client(
                        tenant_id=self.tenant_id,
                        project_id=self.project_id,
                        region_id=self.region_id,
                    )
                except TypeError:
                    return Client()
            else:
                return Client()
        except Exception:
            return None
    
    def _get_vm_by_name(self, client: t.Any, name: str) -> t.Optional[t.Any]:
        """Get VM by name, returns None if not found."""
        try:
            from nebius.api.nebius.compute.v1 import InstanceServiceClient  # type: ignore
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
            
            isc = InstanceServiceClient(client)
            if hasattr(isc, "get_by_name") and self.project_id:
                try:
                    vm = isc.get_by_name(GetByNameRequest(parent_id=self.project_id, name=name)).wait()
                    return vm
                except Exception:
                    return None
        except Exception:
            pass
        return None
    
    def _get_disk_by_name(self, client: t.Any, name: str) -> t.Optional[t.Any]:
        """Get disk by name, returns None if not found."""
        try:
            from nebius.api.nebius.compute.v1 import DiskServiceClient  # type: ignore
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
            
            dsc = DiskServiceClient(client)
            if hasattr(dsc, "get_by_name") and self.project_id:
                try:
                    disk = dsc.get_by_name(GetByNameRequest(parent_id=self.project_id, name=name)).wait()
                    return disk
                except Exception:
                    return None
        except Exception:
            pass
        return None
    
    def get_vm_public_ip(self, vm_name: str) -> t.Optional[str]:
        """Get the public IP address of a VM by querying its network interfaces.
        
        Args:
            vm_name: Name of the VM instance
        
        Returns:
            Public IP address string, or None if not found
        """
        try:
            client = self._get_client()
            if client is None:
                return None
            
            vm_obj = self._get_vm_by_name(client, vm_name)
            if vm_obj is None:
                return None
            
            # Try to get public IP from status.network_interfaces first (actual assigned IP)
            status = getattr(vm_obj, "status", None)
            if status is not None:
                network_interfaces = getattr(status, "network_interfaces", [])
                if network_interfaces:
                    first_nic = network_interfaces[0]
                    pub_ip_addr = getattr(first_nic, "public_ip_address", None)
                    if pub_ip_addr is not None:
                        address = getattr(pub_ip_addr, "address", None)
                        if address:
                            # Strip CIDR suffix if present (e.g., "66.201.7.110/32" -> "66.201.7.110")
                            ip_str = str(address).split('/')[0]
                            return ip_str
            
            # Fallback: check spec.network_interfaces (configured IP)
            spec = getattr(vm_obj, "spec", None)
            if spec is not None:
                network_interfaces = getattr(spec, "network_interfaces", [])
                if network_interfaces:
                    first_nic = network_interfaces[0]
                    pub_ip_addr = getattr(first_nic, "public_ip_address", None)
                    if pub_ip_addr is not None:
                        address = getattr(pub_ip_addr, "address", None)
                        if address:
                            # Strip CIDR suffix if present
                            ip_str = str(address).split('/')[0]
                            return ip_str
        except Exception:
            pass
        return None
    
    def get_allocation_ip(self, allocation_id: str) -> t.Optional[str]:
        """Get the IP address from an allocation.
        
        Args:
            allocation_id: The allocation ID
        
        Returns:
            IP address string, or None if not found
        """
        try:
            client = self._get_client()
            if client is None:
                return None
            
            from nebius.api.nebius.vpc.v1 import AllocationServiceClient  # type: ignore
            from nebius.api.nebius.vpc.v1 import GetAllocationRequest  # type: ignore
            
            asc = AllocationServiceClient(client)
            alloc = asc.get(GetAllocationRequest(id=allocation_id)).wait()
            
            # Try to extract IP from allocation
            spec = getattr(alloc, "spec", None)
            if spec:
                ipv4_public = getattr(spec, "ipv4_public", None)
                if ipv4_public:
                    address = getattr(ipv4_public, "address", None)
                    if address:
                        return str(address)
        except Exception:
            pass
        return None
    
    def wait_for_vm_network(self, vm_name: str, ip_address: str, timeout: int = 180) -> bool:
        """Wait for VM to be reachable via ping.
        
        Args:
            vm_name: Name of the VM instance
            ip_address: IP address to ping
            timeout: Maximum seconds to wait (default 180)
        
        Returns:
            True if VM became reachable, False if timeout
        """
        import subprocess
        import time
        
        print(f"[VMManager] Waiting for {vm_name} ({ip_address}) to be reachable...")
        start_time = time.time()
        attempt = 0
        
        while time.time() - start_time < timeout:
            attempt += 1
            try:
                # Ping with 1 second timeout, 1 packet
                result = subprocess.run(
                    ['ping', '-c', '1', '-W', '1', ip_address],
                    capture_output=True,
                    timeout=2
                )
                if result.returncode == 0:
                    elapsed = int(time.time() - start_time)
                    print(f"[green]✓ {vm_name} is reachable (took {elapsed}s)[/green]")
                    return True
                else:
                    # Show progress
                    if attempt % 3 == 0:  # Every 3 attempts
                        print(".", end="", flush=True)
            except Exception:
                pass
            
            time.sleep(1)
        
        print(f"\n[red]✗ Timeout waiting for {vm_name} to become reachable[/red]")
        return False
    
    def get_vm_allocations(self, vm_name: str) -> t.List[t.Tuple[int, str]]:
        """Get allocation IDs attached to a VM's network interfaces.
        
        Args:
            vm_name: Name of the VM instance
        
        Returns:
            List of (nic_index, allocation_id) tuples
        """
        allocations: t.List[t.Tuple[int, str]] = []
        try:
            client = self._get_client()
            if client is None:
                return allocations
            
            vm_obj = self._get_vm_by_name(client, vm_name)
            if vm_obj is None:
                return allocations
            
            # Extract allocation IDs from network interfaces
            spec = getattr(vm_obj, "spec", None)
            if spec is None:
                return allocations
            
            network_interfaces = getattr(spec, "network_interfaces", [])
            for idx, nic in enumerate(network_interfaces):
                pub_ip_addr = getattr(nic, "public_ip_address", None)
                if pub_ip_addr:
                    alloc_id = getattr(pub_ip_addr, "allocation_id", None)
                    if alloc_id:
                        allocations.append((idx, str(alloc_id)))
        except Exception:
            pass
        return allocations

    def check_vm_health(self, vm_name: str, public_ip: str) -> dict:
        """Check if VM bootstrap completed and services are running.
        
        Args:
            vm_name: Name of the VM instance
            public_ip: Public IP address to connect to
        
        Returns:
            Dict with health status: {
                'reachable': bool,
                'cloud_init_complete': bool,
                'strongswan_installed': bool,
                'frr_installed': bool,
                'agent_installed': bool,
                'message': str
            }
        """
        import subprocess
        import time
        
        result = {
            'reachable': False,
            'cloud_init_complete': False,
            'strongswan_installed': False,
            'frr_installed': False,
            'agent_installed': False,
            'message': 'VM not reachable'
        }
        
        # Wait a moment for VM to boot and network to initialize
        time.sleep(2)
        
        # Test SSH connectivity
        try:
            ssh_test = subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=no', 
                 '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR',
                 f'ubuntu@{public_ip}', 'echo connected'],
                capture_output=True,
                timeout=10
            )
            if ssh_test.returncode != 0:
                result['message'] = 'SSH not ready yet'
                return result
            result['reachable'] = True
        except Exception as e:
            result['message'] = f'SSH connection failed: {e}'
            return result
        
        # Check cloud-init status
        try:
            cloud_init_check = subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=no',
                 '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR',
                 f'ubuntu@{public_ip}', 'cloud-init status --wait --long 2>/dev/null || cloud-init status'],
                capture_output=True,
                timeout=30,
                text=True
            )
            if 'done' in cloud_init_check.stdout.lower() or 'status: done' in cloud_init_check.stdout.lower():
                result['cloud_init_complete'] = True
        except Exception:
            pass
        
        # Check installed packages
        try:
            pkg_check = subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=no',
                 '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR',
                 f'ubuntu@{public_ip}', 
                 'dpkg -l strongswan frr 2>/dev/null | grep \"^ii\" && systemctl is-active nebius-vpngw-agent 2>/dev/null'],
                capture_output=True,
                timeout=10,
                text=True
            )
            if 'strongswan' in pkg_check.stdout:
                result['strongswan_installed'] = True
            if 'frr' in pkg_check.stdout:
                result['frr_installed'] = True
            if 'active' in pkg_check.stdout:
                result['agent_installed'] = True
        except Exception:
            pass
        
        # Generate status message
        if result['cloud_init_complete'] and result['strongswan_installed'] and result['frr_installed']:
            result['message'] = '✓ VM ready: cloud-init complete, strongSwan and FRR installed'
            if result['agent_installed']:
                result['message'] += ', agent running'
        elif result['cloud_init_complete']:
            result['message'] = '⚠ Cloud-init complete but packages not verified'
        else:
            result['message'] = '⏳ Cloud-init still running (packages being installed)'
        
        return result

    def ensure_group(self, spec: GatewayGroupSpec, recreate: bool = False) -> t.Dict[str, str]:
        """Ensure gateway VMs exist per spec.

        Pseudocode for Nebius SDK integration:
        - client = InstanceServiceClient(auth=...)
        - existing = client.list(filter=name prefix)
        - if recreate: delete existing, wait; then create all
        - else: create missing, skip existing
        - attach public IPs according to spec.external_ips
        - set network interface subnet to spec.vm_spec.vpn_subnet_id
        
        Returns:
            Dict mapping VM names to their public IP addresses
        """
        print(
            f"[VMManager] ensure_group name={spec.name} count={spec.instance_count} region={spec.region} recreate={recreate}"
        )
        
        # Track created/existing VMs and their IPs
        vm_ips: t.Dict[str, str] = {}
        
        try:
            print(f"[VMManager] Using project_id={self.project_id} zone={self.zone or spec.region}")
        except Exception:
            pass
        # Try actual SDK integration with defensive guards
        client = None
        # If an auth token was provided, expose it to SDKs that read env
        try:
            import os
            if self.auth_token and not os.environ.get("NEBIUS_IAM_TOKEN"):
                os.environ["NEBIUS_IAM_TOKEN"] = self.auth_token
        except Exception:
            pass
        try:
            # Resolve Nebius SDK primary surface
            Client = None  # type: ignore
            try:
                from nebius.sdk import SDK as _C  # type: ignore
                Client = _C
            except Exception:
                # Fall back to older/alternate surfaces
                try:
                    from nebius.sdk import Client as _C  # type: ignore
                    Client = _C
                except Exception:
                    try:
                        from nebius.client import Client as _C  # type: ignore
                        Client = _C
                    except Exception:
                        try:
                            from nebius import pysdk  # type: ignore
                            Client = pysdk.Client  # type: ignore[attr-defined]
                        except Exception:
                            try:
                                from nebius.pysdk import Client as _C  # type: ignore
                                Client = _C
                            except Exception:
                                pass
            if Client is None:
                raise ImportError("Nebius SDK not found")

            # Prefer explicit context if available; support SDK-style constructor too
            if self.tenant_id and self.project_id and (self.region_id or spec.region):
                try:
                    client = Client(
                        tenant_id=self.tenant_id,
                        project_id=self.project_id,
                        region_id=self.region_id or spec.region,
                    )
                except TypeError:
                    # SDK may not accept context args; fall back to default constructor
                    client = Client()
                except Exception:
                    client = Client()
            else:
                # Fallback: initialize via CLI config reader but disable parent-id usage
                try:
                    from nebius.aio.cli_config import Config  # type: ignore
                    try:
                        client = Client(config_reader=Config(no_parent_id=True))  # type: ignore[call-arg]
                    except TypeError:
                        client = Client()
                except Exception:
                    # As last resort, rely on env token/defaults
                    try:
                        client = Client()
                    except Exception:
                        client = Client()
        except Exception as e:
            print(
                "[VMManager] Nebius SDK not available; install with 'pip install nebius'. "
                f"Running in dry scaffold mode: {e}"
            )

        try:
            if client is not None:
                # Resolve services defensively: attributes may be properties or callables
                def _svc(obj: t.Any, name: str) -> t.Any:
                    if obj is None:
                        return None
                    attr = getattr(obj, name, None)
                    if attr is None:
                        return None
                    try:
                        return attr() if callable(attr) else attr
                    except Exception:
                        return attr

                compute = _svc(client, "compute") or _svc(getattr(client, "cloud", None), "compute")
                vpc = _svc(client, "vpc") or _svc(getattr(client, "network", None), "vpc") or _svc(getattr(client, "cloud", None), "vpc")

                # Instance and disk APIs (try common names)
                instance_api = None
                if compute is not None:
                    for name in ("instance", "instances", "vm", "virtual_machine"):
                        instance_api = getattr(compute, name, None)
                        if instance_api is not None:
                            break
                disk_api = None
                if compute is not None:
                    for name in ("disk", "disks", "storage_disk"):
                        disk_api = getattr(compute, name, None)
                        if disk_api is not None:
                            break

                # Allocation/public IP API under VPC
                # Prefer explicit AllocationServiceClient if available
                alloc_api = None
                alloc_client = None
                try:
                    from nebius.api.nebius.vpc.v1 import AllocationServiceClient  # type: ignore
                    alloc_client = AllocationServiceClient(client)  # type: ignore
                except Exception:
                    alloc_client = None
                if alloc_client is None and vpc is not None:
                    for name in ("allocation", "allocations", "public_ip", "public_ips"):
                        alloc_api = getattr(vpc, name, None)
                        if alloc_api is not None:
                            break

                # Discover existing VMs (needed for --recreate-gw)
                # Use the same method as check_changes() for consistency
                existing = []
                for i in range(spec.instance_count):
                    inst_name = f"{spec.name}-{i}"
                    vm_obj = self._get_vm_by_name(client, inst_name)
                    if vm_obj:
                        existing.append(vm_obj)
                
                if not existing:
                    print("[VMManager] No existing VMs found")
                else:
                    print(f"[VMManager] Found {len(existing)} existing VM(s) for recreation")

                # Optionally delete existing VMs (preserves subnet and allocations)
                # IMPORTANT: Only VMs are deleted. The following are preserved:
                # - vpngw-subnet (reused via _ensure_vpngw_subnet below)
                # - Public IP allocations (automatically detached, remain unassigned for 30 days)
                # This ensures IP stability across VM recreations (critical for VPN gateways)
                
                # ALLOCATION PRESERVATION STRATEGY (Section 16):
                # Before deleting VMs, query and save their allocation IDs
                # After recreation, reuse the same allocations (unless external_ips explicitly provided in YAML)
                preserved_allocations: t.Dict[str, t.List[str]] = {}  # vm_name -> [allocation_id_eth0, ...]
                
                if recreate and existing:
                    print(f"[VMManager] Querying allocations from {len(existing)} existing VMs for preservation...")
                    for inst in existing:
                        vm_name = getattr(getattr(inst, "metadata", None), "name", None) or getattr(inst, "name", None)
                        if vm_name:
                            allocs = self.get_vm_allocations(vm_name)
                            if allocs:
                                # Store allocation IDs in NIC order
                                alloc_ids = [alloc_id for _, alloc_id in sorted(allocs, key=lambda x: x[0])]
                                preserved_allocations[vm_name] = alloc_ids
                                print(f"[VMManager] Preserved allocations for {vm_name}: {alloc_ids}")
                
                if recreate and existing:
                    print(f"[VMManager] Recreate requested; deleting {len(existing)} instances and boot disks (preserving subnet and allocations)")
                    # Get clients for deletion
                    isc = None
                    dsc = None
                    try:
                        from nebius.api.nebius.compute.v1 import InstanceServiceClient, DiskServiceClient  # type: ignore
                        isc = InstanceServiceClient(client)
                        dsc = DiskServiceClient(client)
                    except Exception as e:
                        print(f"[VMManager] Cannot get service clients for deletion: {e}")
                    
                    if isc:
                        # Step 1: Delete VMs (allocations will auto-detach)
                        for inst in existing:
                            # Try multiple ways to extract VM ID
                            inst_id = getattr(inst, "id", None)
                            if not inst_id:
                                # Try metadata.id (common in SDK responses)
                                metadata = getattr(inst, "metadata", None)
                                if metadata:
                                    inst_id = getattr(metadata, "id", None)
                            
                            inst_name = getattr(getattr(inst, "metadata", None), "name", None) or getattr(inst, "name", "unknown")
                            if inst_id:
                                try:
                                    print(f"[VMManager] Deleting VM {inst_name} (id={inst_id})...")
                                    from nebius.api.nebius.compute.v1 import DeleteInstanceRequest  # type: ignore
                                    delete_req = DeleteInstanceRequest(id=inst_id)
                                    op = isc.delete(delete_req)
                                    # Wait for deletion to complete before proceeding
                                    if hasattr(op, "wait"):
                                        op.wait()
                                        print(f"[VMManager] VM {inst_name} deletion initiated")
                                    else:
                                        # Fallback: brief sleep if no wait() available
                                        import time
                                        time.sleep(5)
                                except Exception as e:
                                    print(f"[VMManager] Failed to delete VM {inst_name}: {e}")
                    else:
                        print("[VMManager] ERROR: Cannot delete VMs - InstanceServiceClient not available")
                        raise RuntimeError("Cannot proceed with --recreate-gw: VM deletion failed")
                    
                    # Wait for VM deletions to fully propagate before deleting disks
                    if existing:
                        import time
                        print("[VMManager] Waiting for VM deletions to complete...")
                        time.sleep(15)
                    
                    # Step 2: Delete boot disks (with retry since disk detachment can take time)
                    if dsc:
                        from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
                        import time
                        for i in range(spec.instance_count):
                            inst_name = f"{spec.name}-{i}"
                            boot_disk_name = f"{inst_name}-boot"
                            try:
                                if self.project_id and hasattr(dsc, "get_by_name"):
                                    disk_obj = dsc.get_by_name(GetByNameRequest(parent_id=self.project_id, name=boot_disk_name)).wait()
                                    disk_id = getattr(disk_obj, "id", None) or getattr(getattr(disk_obj, "metadata", None), "id", None)
                                    if disk_id:
                                        # Retry disk deletion up to 3 times with backoff
                                        max_retries = 3
                                        for attempt in range(max_retries):
                                            try:
                                                print(f"[VMManager] Deleting boot disk {boot_disk_name} (id={disk_id})...")
                                                from nebius.api.nebius.compute.v1 import DeleteDiskRequest  # type: ignore
                                                delete_disk_req = DeleteDiskRequest(id=disk_id)
                                                disk_op = dsc.delete(delete_disk_req)
                                                if hasattr(disk_op, "wait"):
                                                    disk_op.wait()
                                                    print(f"[VMManager] Boot disk {boot_disk_name} deleted successfully")
                                                break  # Success - exit retry loop
                                            except Exception as disk_err:
                                                if "FAILED_PRECONDITION" in str(disk_err) and "read-write attachments" in str(disk_err):
                                                    if attempt < max_retries - 1:
                                                        wait_time = 10 * (attempt + 1)  # 10s, 20s, 30s
                                                        print(f"[VMManager] Disk still attached, waiting {wait_time}s before retry {attempt + 2}/{max_retries}...")
                                                        time.sleep(wait_time)
                                                    else:
                                                        print(f"[VMManager] Could not delete boot disk {boot_disk_name} after {max_retries} attempts: {disk_err}")
                                                else:
                                                    # Different error - don't retry
                                                    print(f"[VMManager] Could not delete boot disk {boot_disk_name}: {disk_err}")
                                                    break
                            except Exception as e:
                                # Non-fatal: disk might not exist or already deleted
                                print(f"[VMManager] Could not find or delete boot disk {boot_disk_name} (non-fatal): {e}")
                    
                    # Additional wait to ensure allocations are fully detached and disks fully deleted
                    if existing:
                        import time
                        print("[VMManager] Waiting for allocations to fully detach and disk deletions to complete...")
                        time.sleep(15)  # Wait for disk deletion to fully propagate

                # Ensure each instance
                for i in range(spec.instance_count):
                    inst_name = f"{spec.name}-{i}"
                    # Check if exists (skip creation if recreate=False)
                    vm_exists = False
                    try:
                        # Try SDK client first (more reliable)
                        vm_obj = self._get_vm_by_name(client, inst_name)
                        if vm_obj is not None:
                            vm_exists = True
                        # Fallback to legacy instance_api if SDK check failed
                        elif instance_api is not None and hasattr(instance_api, "get_by_name"):
                            try:
                                inst = instance_api.get_by_name(name=inst_name, project_id=self.project_id)
                            except TypeError:
                                inst = instance_api.get_by_name(name=inst_name)
                            vm_exists = inst is not None
                    except Exception:
                        vm_exists = False
                    
                    if vm_exists and not recreate:
                        print(f"[VMManager] VM {inst_name} already exists (recreate=False), skipping creation")
                        # Get public IP for reporting
                        vm_ip = self.get_vm_public_ip(inst_name)
                        if vm_ip:
                            vm_ips[inst_name] = vm_ip
                            print(f"[VMManager] {inst_name} IP: {vm_ip}")
                        continue
                    elif vm_exists and recreate:
                        # This should not happen - VMs should have been deleted above
                        print(f"[VMManager] WARNING: VM {inst_name} still exists after deletion (race condition?)")
                        continue

                    # Step 1: Ensure boot disk exists (following CLI pattern)
                    # Determine/ensure gateway subnet (vpngw-subnet) in desired network
                    # Note: _ensure_vpngw_subnet only creates if missing, never deletes
                    # This preserves the subnet and any unassigned allocations during --recreate-gw
                    subnet_id = self._ensure_vpngw_subnet(client, spec)
                    nic = {"subnet_id": subnet_id}
                    # Preset-based CPU/mem; fall back to cores/memory_gb if preset missing
                    platform = spec.vm_spec.get("platform") or "cpu-d3"
                    preset = spec.vm_spec.get("preset")
                    # Boot disk/image fields per template
                    boot_image = spec.vm_spec.get("disk_boot_image") or spec.vm_spec.get("image_family") or "ubuntu24.04-driverless"
                    disk_gb = spec.vm_spec.get("disk_gb", 200)
                    disk_type = spec.vm_spec.get("disk_type", "network_ssd")
                    # Normalize disk type to SDK enum values
                    try:
                        dt = str(disk_type).upper()
                        # Map common aliases
                        if dt in {"NETWORK_SSD", "NETWORK_HDD", "NETWORK_SSD_NON_REPLICATED", "NETWORK_SSD_IO_M3"}:
                            disk_type = dt
                        elif dt in {"SSD", "NVME"}:
                            disk_type = "NETWORK_SSD"
                        elif dt in {"HDD"}:
                            disk_type = "NETWORK_HDD"
                        else:
                            # Default to NETWORK_SSD if unknown value provided
                            disk_type = "NETWORK_SSD"
                    except Exception:
                        disk_type = "NETWORK_SSD"
                    disk_block_bytes = spec.vm_spec.get("disk_block_bytes", 4096)
                    ssh_key = spec.vm_spec.get("ssh_public_key")
                    cloud_init = self._build_cloud_init(ssh_key=ssh_key)
                    boot_disk_name = f"{inst_name}-boot"
                    boot_disk_id = None
                    # Prefer explicit DiskServiceClient with CreateDiskRequest per schema
                    try:
                        from nebius.api.nebius.compute.v1 import (
                            DiskServiceClient,
                            CreateDiskRequest,
                            DiskSpec,
                            ImageServiceClient,
                            GetImageLatestByFamilyRequest,
                        )  # type: ignore
                        from nebius.api.nebius.common.v1 import ResourceMetadata, GetByNameRequest  # type: ignore
                        dsc = DiskServiceClient(client)  # type: ignore
                        # Try get_by_name first (skip if recreating to force new disk creation)
                        boot_disk_id = None
                        if self.project_id and not recreate:
                            try:
                                # Prefer get_by_name if available on client
                                if hasattr(dsc, "get_by_name"):
                                    disk_obj = dsc.get_by_name(GetByNameRequest(parent_id=self.project_id, name=boot_disk_name)).wait()
                                    boot_disk_id = getattr(disk_obj, "id", None) or getattr(getattr(disk_obj, "metadata", None), "id", None)
                                    if boot_disk_id:
                                        print(f"[VMManager] Found existing disk {boot_disk_name} id={boot_disk_id}")
                            except Exception:
                                # Disk doesn't exist yet - will create below
                                boot_disk_id = None
                        if not boot_disk_id:
                            print(f"[VMManager] Creating boot disk {boot_disk_name} (project_id={self.project_id}) ...")
                            # Resolve image id: prefer explicit config, else latest-by-family using region-matched public catalog
                            image_id = spec.vm_spec.get("image_id") or None
                            if not image_id and boot_image:
                                try:
                                    imgc = ImageServiceClient(client)  # type: ignore
                                    # Derive routing code from project_id (e.g., project-e01... -> e01)
                                    routing_code = None
                                    try:
                                        if self.project_id and self.project_id.startswith("project-"):
                                            routing_code = (self.project_id.split("-")[1] or "")[:3]
                                    except Exception:
                                        routing_code = None
                                    # Prefer region-matched public images catalog
                                    parents_to_try = []
                                    if routing_code:
                                        parents_to_try.append(f"project-{routing_code}public-images")
                                    # Also try no parent and the global u00 catalog as fallbacks
                                    parents_to_try.extend([None, "project-u00public-images"])
                                    for parent in parents_to_try:
                                        try:
                                            req = GetImageLatestByFamilyRequest(image_family=boot_image, **({"parent_id": parent} if parent else {}))
                                            img = imgc.get_latest_by_family(req).wait()
                                            cand_id = getattr(img, "id", None) or getattr(getattr(img, "metadata", None), "id", None)
                                            # Ensure routing code compatibility: ImageID must share routing prefix with project
                                            if cand_id and routing_code:
                                                if cand_id.startswith(f"computeimage-{routing_code}"):
                                                    image_id = cand_id
                                                    break
                                                else:
                                                    # keep searching other parents
                                                    continue
                                            if cand_id and not routing_code:
                                                image_id = cand_id
                                                break
                                        except Exception:
                                            continue
                                    if not image_id:
                                        image_id = None
                                except Exception:
                                    image_id = None
                            # Build DiskSpec using source_image_id if resolved; otherwise fallback to family
                            if image_id:
                                spec_msg = DiskSpec(
                                    block_size_bytes=disk_block_bytes,
                                    size_gibibytes=disk_gb,
                                    type=disk_type,
                                    source_image_id=image_id,
                                )
                            else:
                                raise RuntimeError(
                                    f"[VMManager] Unable to resolve image id for family '{boot_image}'. "
                                    "Ensure the image family exists or provide vm_spec.image_id."
                                )
                            req = CreateDiskRequest(
                                metadata=ResourceMetadata(name=boot_disk_name, parent_id=self.project_id or ""),
                                spec=spec_msg,
                            )
                            try:
                                op = dsc.create(req).wait()
                                try:
                                    op.sync_wait()
                                except Exception:
                                    pass
                                # Try to extract id from operation result first
                                try:
                                    res = getattr(op, "result", None)
                                    rid = getattr(getattr(res, "resource", None), "id", None)
                                    if rid:
                                        boot_disk_id = rid
                                except Exception:
                                    pass
                                # If still missing, refetch by name
                                if not boot_disk_id:
                                    try:
                                        # Prefer list with filter to locate by name
                                        from nebius.api.nebius.compute.v1 import ListDisksRequest  # type: ignore
                                        lst = dsc.list(ListDisksRequest(parent_id=self.project_id or "")).wait()
                                        items = getattr(lst, "items", []) or []
                                        for d in items:
                                            if getattr(getattr(d, "metadata", None), "name", None) == boot_disk_name:
                                                boot_disk_id = getattr(d, "id", None) or getattr(getattr(d, "metadata", None), "id", None)
                                                break
                                    except Exception:
                                        # As a last resort, attempt get_by_name again
                                        if self.project_id:
                                            try:
                                                disk_obj = dsc.get_by_name(GetByNameRequest(parent_id=self.project_id, name=boot_disk_name)).wait()
                                                boot_disk_id = getattr(disk_obj, "id", None) or getattr(getattr(disk_obj, "metadata", None), "id", None)
                                            except Exception:
                                                boot_disk_id = None
                            except Exception as e:
                                # If disk already exists, it might be in deleting state from recreation
                                msg = str(e)
                                print(f"[VMManager] Disk create exception: {msg}")
                                if "ALREADY_EXISTS" in msg or f"disk with name \"{boot_disk_name}\" already exists" in msg:
                                    # During recreation, old disk might still be deleting
                                    if recreate:
                                        print(f"[VMManager] Disk {boot_disk_name} still exists (likely deleting), waiting for deletion to complete...")
                                        import time
                                        max_wait = 60  # Wait up to 60 seconds
                                        wait_interval = 5
                                        for wait_attempt in range(max_wait // wait_interval):
                                            time.sleep(wait_interval)
                                            try:
                                                # Check if disk still exists
                                                if hasattr(dsc, "get_by_name"):
                                                    disk_obj = dsc.get_by_name(GetByNameRequest(parent_id=self.project_id, name=boot_disk_name)).wait()
                                                    # Disk still exists - keep waiting
                                                    print(f"[VMManager] Disk still exists, waiting... ({(wait_attempt + 1) * wait_interval}s)")
                                                    continue
                                            except Exception:
                                                # Disk no longer found - deletion complete, retry creation
                                                print("[VMManager] Disk deletion complete, retrying creation...")
                                                try:
                                                    op = dsc.create(req).wait()
                                                    try:
                                                        op.sync_wait()
                                                    except Exception:
                                                        pass
                                                    # Extract disk ID from operation result
                                                    try:
                                                        res = getattr(op, "result", None)
                                                        rid = getattr(getattr(res, "resource", None), "id", None)
                                                        if rid:
                                                            boot_disk_id = rid
                                                    except Exception:
                                                        pass
                                                    if not boot_disk_id:
                                                        # Refetch to get ID
                                                        try:
                                                            from nebius.api.nebius.compute.v1 import ListDisksRequest  # type: ignore
                                                            lst = dsc.list(ListDisksRequest(parent_id=self.project_id or "")).wait()
                                                            items = getattr(lst, "items", []) or []
                                                            for d in items:
                                                                if getattr(getattr(d, "metadata", None), "name", None) == boot_disk_name:
                                                                    boot_disk_id = getattr(d, "id", None) or getattr(getattr(d, "metadata", None), "id", None)
                                                                    break
                                                        except Exception:
                                                            pass
                                                except Exception as retry_err:
                                                    print(f"[VMManager] Disk creation retry failed: {retry_err}")
                                                break
                                        else:
                                            # Timeout waiting for deletion
                                            print("[VMManager] Timeout waiting for disk deletion to complete")
                                    else:
                                        # Not recreating - just refetch existing disk
                                        print("[VMManager] Disk already exists, refetching ID...")
                                        if self.project_id:
                                            try:
                                                if hasattr(dsc, "get_by_name"):
                                                    disk_obj = dsc.get_by_name(GetByNameRequest(parent_id=self.project_id, name=boot_disk_name)).wait()
                                                    boot_disk_id = getattr(disk_obj, "id", None) or getattr(getattr(disk_obj, "metadata", None), "id", None)
                                                    print(f"[VMManager] Refetched existing disk id={boot_disk_id}")
                                                else:
                                                    from nebius.api.nebius.compute.v1 import ListDisksRequest  # type: ignore
                                                    lst = dsc.list(ListDisksRequest(parent_id=self.project_id)).wait()
                                                    items = getattr(lst, "items", []) or []
                                                    for d in items:
                                                        if getattr(getattr(d, "metadata", None), "name", None) == boot_disk_name:
                                                            boot_disk_id = getattr(d, "id", None) or getattr(getattr(d, "metadata", None), "id", None)
                                                            print(f"[VMManager] Refetched via list: disk id={boot_disk_id}")
                                                            break
                                            except Exception as refetch_err:
                                                print(f"[VMManager] Refetch failed: {refetch_err}")
                                        else:
                                            print("[VMManager] Cannot refetch: project_id is None")
                                else:
                                    print(f"[VMManager] boot disk create failed: {e}")
                    except Exception:
                        # Fallback to legacy disk_api surfaces if present
                        if disk_api is not None:
                            try:
                                if hasattr(disk_api, "get_by_name"):
                                    disk_obj = disk_api.get_by_name(name=boot_disk_name)
                                    boot_disk_id = getattr(disk_obj, "id", None) or getattr(getattr(disk_obj, "metadata", None), "id", None)
                            except Exception:
                                boot_disk_id = None
                            if not boot_disk_id and hasattr(disk_api, "create"):
                                disk_req = {
                                    "name": boot_disk_name,
                                    "size_gibibytes": disk_gb,
                                    "type": disk_type,
                                    "source_image_family": boot_image,
                                    "block_size_bytes": disk_block_bytes,
                                    **({"project_id": self.project_id} if self.project_id else {}),
                                    **({"zone": self.zone or spec.region} if (self.zone or spec.region) else {}),
                                }
                                try:
                                    print(f"[VMManager] Creating boot disk {boot_disk_name} ...")
                                    try:
                                        disk_obj = disk_api.create(**disk_req)  # type: ignore
                                    except TypeError:
                                        disk_obj = disk_api.create(disk_req)
                                    boot_disk_id = getattr(disk_obj, "id", None) or getattr(getattr(disk_obj, "metadata", None), "id", None)
                                except Exception as e:
                                    print(f"[VMManager] boot disk create failed: {e}. Attempted: {disk_req}")

                    # Step 2: Ensure public IP allocations (1 per NIC)
                    # ALLOCATION STRATEGY:
                    # - Create num_nics allocations (one per network interface)
                    # - Allocations are named: {instance}-eth0-ip, {instance}-eth1-ip, etc.
                    # - Map external_ips array in order: external_ips[0] → eth0, external_ips[1] → eth1
                    # - If external_ips not provided or insufficient, auto-create allocations
                    # - PRESERVATION: When recreating VMs, reuse preserved allocations (same IPs)
                    # CURRENT PLATFORM LIMITATION: num_nics=1 (enforced by config_loader)
                    # FUTURE NIC EXPANSION:
                    #   - When platform supports multi-NIC, increasing num_nics is SAFE (non-destructive)
                    #   - Process: Create new allocation → Attach new NIC to existing VM
                    #   - No VM recreation needed, existing NICs/tunnels unaffected
                    #   - Detected by vm_diff.py as safe expansion (similar to disk expansion)
                    num_nics = int(spec.vm_spec.get("num_nics", 1))
                    if num_nics > 1:
                        print(f"[VMManager] WARNING: num_nics={num_nics} but current platform only supports 1 NIC. Using num_nics=1.")
                        num_nics = 1
                    
                    desired_ips = []
                    if spec.external_ips:
                        # Take first num_nics IPs from external_ips array
                        desired_ips = [ip for ip in spec.external_ips[:num_nics] if ip]
                    
                    # Check if we have preserved allocations from VM recreation (Section 16)
                    preserved_alloc_ids = preserved_allocations.get(inst_name, [])
                    
                    alloc_ids: list[str] = []
                    if alloc_api is not None or alloc_client is not None:
                        for nic_index in range(num_nics):
                            nic_name = f"eth{nic_index}"
                            desired_ip = desired_ips[nic_index] if nic_index < len(desired_ips) else None
                            alloc_obj = None
                            
                            # PRIORITY 1: If external_ips explicitly provided in YAML, use that
                            # PRIORITY 2: If we have preserved allocation from recreation, reuse it
                            # PRIORITY 3: Try to find existing allocation by name
                            # PRIORITY 4: Create new allocation
                            
                            # Define alloc_name upfront (used in multiple priority paths)
                            alloc_name = f"{inst_name}-{nic_name}-ip"
                            
                            # Priority 1: Match existing allocation by IP if provided in YAML
                            if desired_ip:
                                try:
                                    get_by_addr = getattr(alloc_api, "get_by_address", None)
                                    if get_by_addr:
                                        alloc_obj = get_by_addr(address=desired_ip, project_id=self.project_id)
                                        if alloc_obj:
                                            print(f"[VMManager] Using allocation from YAML external_ips: {desired_ip}")
                                except Exception:
                                    alloc_obj = None
                            
                            # Priority 2: Reuse preserved allocation from VM recreation
                            if alloc_obj is None and not desired_ip and nic_index < len(preserved_alloc_ids):
                                preserved_alloc_id = preserved_alloc_ids[nic_index]
                                try:
                                    if alloc_client is not None:
                                        from nebius.api.nebius.vpc.v1 import GetAllocationRequest  # type: ignore
                                        alloc_obj = alloc_client.get(GetAllocationRequest(id=preserved_alloc_id)).wait()
                                        if alloc_obj:
                                            # Get IP for display
                                            preserved_ip = self.get_allocation_ip(preserved_alloc_id)
                                            print(f"[VMManager] Reusing preserved allocation {preserved_alloc_id} ({preserved_ip}) for {inst_name} {nic_name}")
                                except Exception as e:
                                    print(f"[VMManager] Could not retrieve preserved allocation {preserved_alloc_id}: {e}")
                                    alloc_obj = None
                            
                            # Priority 3: Try by-name before creating to avoid ALREADY_EXISTS
                            if alloc_obj is None and alloc_client is not None:
                                try:
                                    from nebius.api.nebius.vpc.v1 import GetAllocationByNameRequest  # type: ignore
                                    alloc_name = f"{inst_name}-{nic_name}-ip"
                                    alloc_obj = alloc_client.get_by_name(
                                        GetAllocationByNameRequest(parent_id=self.project_id or "", name=alloc_name)
                                    ).wait()
                                    if alloc_obj:
                                        print(f"[VMManager] Found existing allocation by name: {alloc_name}")
                                except Exception:
                                    alloc_obj = None
                            
                            # Priority 4: Create allocation in vpngw-subnet when not found
                            if alloc_obj is None:
                                # Ensure subnet_id is present before attempting allocation creation
                                if not nic.get("subnet_id"):
                                    raise RuntimeError(
                                        "[VMManager] Cannot create public IP allocation: subnet_id is not set. "
                                        "Resolve subnet creation first or provide a valid network_id."
                                    )
                                try:
                                    alloc_name = f"{inst_name}-{nic_name}-ip"
                                    print(f"[VMManager] Creating public IP allocation {alloc_name} for {nic_name} in vpngw-subnet ...")
                                    if alloc_client is not None:
                                        try:
                                            from nebius.api.nebius.vpc.v1 import CreateAllocationRequest, AllocationSpec, IPv4PublicAllocationSpec  # type: ignore
                                            from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
                                            req = CreateAllocationRequest(
                                                metadata=ResourceMetadata(
                                                    name=alloc_name,
                                                    parent_id=self.project_id or "",
                                                ),
                                                spec=AllocationSpec(
                                                    ipv4_public=IPv4PublicAllocationSpec(
                                                        subnet_id=nic["subnet_id"],
                                                        cidr="/32" if not desired_ip else desired_ip,
                                                    )
                                                ),
                                            )
                                            op = alloc_client.create(req).wait()  # Operation
                                            try:
                                                op.sync_wait()
                                            except Exception:
                                                pass
                                            # Try to read allocation id from operation result or refetch by name
                                            alloc_obj = None
                                            try:
                                                # Some SDKs expose result.resource.id
                                                res = getattr(op, "result", None)
                                                rid = getattr(getattr(res, "resource", None), "id", None)
                                                if rid:
                                                    # Construct a minimal object-like dict to carry id
                                                    alloc_obj = type("Alloc", (), {"id": rid})()
                                            except Exception:
                                                alloc_obj = None
                                            if alloc_obj is None:
                                                try:
                                                    from nebius.api.nebius.vpc.v1 import GetAllocationByNameRequest  # type: ignore
                                                    alloc_obj = alloc_client.get_by_name(
                                                        GetAllocationByNameRequest(parent_id=self.project_id or "", name=alloc_name)
                                                    ).wait()
                                                except Exception:
                                                    alloc_obj = None
                                        except Exception as e:
                                            print(f"[VMManager] allocation create via client failed: {e}")
                                            # If it already exists, refetch by name and proceed
                                            try:
                                                from nebius.api.nebius.vpc.v1 import GetAllocationByNameRequest  # type: ignore
                                                alloc_obj = alloc_client.get_by_name(
                                                    GetAllocationByNameRequest(parent_id=self.project_id or "", name=alloc_name)
                                                ).wait()
                                            except Exception:
                                                pass
                                    elif alloc_api is not None:
                                        # Fallback legacy surface; prefer explicit client above.
                                        create_args = {
                                            "name": alloc_name,
                                            "ipv_4_public_subnet_id": nic["subnet_id"],
                                            **({"project_id": self.project_id} if self.project_id else {}),
                                        }
                                        try:
                                            alloc_obj = alloc_api.create(**create_args)  # type: ignore
                                        except TypeError:
                                            alloc_obj = alloc_api.create(create_args)
                                except Exception as e:
                                    print(f"[VMManager] allocation create failed: {e}")
                            
                            # Extract allocation id robustly and get IP address
                            alloc_id = None
                            if alloc_obj is not None:
                                alloc_id = getattr(alloc_obj, "id", None)
                                if not alloc_id:
                                    alloc_id = getattr(getattr(alloc_obj, "metadata", None), "id", None)
                            if alloc_id:
                                alloc_ids.append(alloc_id)
                                print(f"[VMManager] Allocation {alloc_name} ready: {alloc_id}")
                                
                                # Get the IP address from this allocation for the first NIC (eth0)
                                if nic_index == 0:
                                    alloc_ip = self.get_allocation_ip(alloc_id)
                                    if alloc_ip:
                                        vm_ips[inst_name] = alloc_ip
                                        print(f"[VMManager] {inst_name} will use IP: {alloc_ip}")


                    # Step 3: Create instance with proper metadata/spec per SDK schema
                    inst_req = {
                        "metadata": {
                            "name": inst_name,
                            **({"parent_id": self.project_id} if self.project_id else {}),
                        },
                        "spec": {
                            # Resources
                            "resources": {
                                "platform": platform,
                                **({"preset": preset} if preset else {}),
                            },
                            # Boot disk reference
                            **(
                                {
                                    "boot_disk": {
                                        "attach_mode": "READ_WRITE",
                                        "device_id": "boot",
                                        "existing_disk": {"id": boot_disk_id},
                                    }
                                }
                                if boot_disk_id
                                else {}
                            ),
                            # Network - build num_nics NICs with proper allocation mapping
                            # Legacy dict format for fallback API compatibility
                            "network_interfaces": [
                                {
                                    "name": f"eth{nic_idx}",
                                    "ip_address": {},
                                    "public_ip_address": (
                                        {"allocation_id": alloc_ids[nic_idx], "static": True}
                                        if nic_idx < len(alloc_ids)
                                        else {}
                                    ),
                                    "subnet_id": nic["subnet_id"],
                                }
                                for nic_idx in range(min(num_nics, 1))  # Platform limitation: max 1 NIC
                            ],
                            # Cloud-init user data
                            "cloud_init_user_data": cloud_init,
                        },
                    }
                    # If boot_disk_id is still missing, try to resolve by name before creating instance
                    if not boot_disk_id and self.project_id:
                        try:
                            from nebius.api.nebius.compute.v1 import DiskServiceClient  # type: ignore
                            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
                            dsc2 = DiskServiceClient(client)  # type: ignore
                            if hasattr(dsc2, "get_by_name"):
                                disk_obj = dsc2.get_by_name(GetByNameRequest(parent_id=self.project_id, name=boot_disk_name)).wait()
                                boot_disk_id = getattr(disk_obj, "id", None) or getattr(getattr(disk_obj, "metadata", None), "id", None)
                                if boot_disk_id:
                                    print(f"[VMManager] Final fallback: resolved disk id={boot_disk_id}")
                        except Exception as e:
                            print(f"[VMManager] Final fallback disk lookup failed: {e}")

                        # Prefer explicit InstanceServiceClient if available
                    created = False
                    try:
                        from nebius.api.nebius.compute.v1 import (
                            InstanceServiceClient,
                            CreateInstanceRequest,
                            InstanceSpec,
                            ResourcesSpec,
                            NetworkInterfaceSpec,
                            IPAddress,
                            PublicIPAddress,
                            AttachedDiskSpec,
                            ExistingDisk,
                        )  # type: ignore
                        from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
                        isc = InstanceServiceClient(client)  # type: ignore
                        print(f"[VMManager] Creating instance {inst_name} via InstanceServiceClient (project_id={self.project_id}) ...")
                        # Build protobuf messages per SDK types
                        metadata = ResourceMetadata(name=inst_name, parent_id=self.project_id or "")
                        resources = ResourcesSpec(platform=platform, **({"preset": preset} if preset else {}))
                        # Boot disk message if available
                        boot_disk_msg = None
                        if boot_disk_id:
                            boot_disk_msg = AttachedDiskSpec(
                                attach_mode="READ_WRITE",
                                device_id="boot",
                                existing_disk=ExistingDisk(id=boot_disk_id),
                            )
                        # If we still don't have a boot disk id, skip adding boot disk; some images may allow ephemeral boot
                        # We'll log a warning and proceed to let the API validate.
                        if not boot_disk_id:
                            print("[VMManager] Warning: boot_disk_id missing; proceeding without boot_disk in spec.")

                        # Network interfaces - build num_nics NICs with proper allocation mapping
                        # NIC CREATION STRATEGY:
                        # - Create num_nics NetworkInterfaceSpecs (eth0, eth1, ..., eth{n-1})
                        # - Map allocations in order: alloc_ids[0] → eth0, alloc_ids[1] → eth1, etc.
                        # - Each NIC attached to vpngw-subnet with private IP auto-assigned
                        # CURRENT PLATFORM LIMITATION: num_nics=1 enforced by config validation
                        # FUTURE: When platform supports multi-NIC, NICs will be created per num_nics config
                        ni_msgs = []
                        for nic_idx in range(num_nics):
                            nic_name = f"eth{nic_idx}"
                            pub = None
                            if nic_idx < len(alloc_ids):
                                pub = PublicIPAddress(allocation_id=alloc_ids[nic_idx], static=True)
                            ni_msgs.append(
                                NetworkInterfaceSpec(
                                    name=nic_name,
                                    ip_address=IPAddress(),
                                    public_ip_address=pub if pub is not None else PublicIPAddress(),
                                    subnet_id=nic["subnet_id"],
                                )
                            )
                            print(f"[VMManager] NIC {nic_name} configured with allocation={alloc_ids[nic_idx] if nic_idx < len(alloc_ids) else 'auto'}")
                        
                        # Validation: Ensure we don't exceed platform limits
                        if len(ni_msgs) > 1:
                            print(f"[VMManager] WARNING: {len(ni_msgs)} NICs configured but platform only supports 1. Using first NIC only.")
                            ni_msgs = ni_msgs[:1]
                        
                        # Log boot disk id for diagnostics
                        try:
                            print(f"[VMManager] Using boot_disk_id={boot_disk_id}")
                        except Exception:
                            pass
                        spec_kwargs = {
                            "resources": resources,
                            "network_interfaces": ni_msgs,
                            "cloud_init_user_data": cloud_init,
                        }
                        # Only include boot_disk when we have a valid id
                        if boot_disk_msg is not None and boot_disk_id:
                            spec_kwargs["boot_disk"] = boot_disk_msg
                        spec = InstanceSpec(**spec_kwargs)
                        req = CreateInstanceRequest(metadata=metadata, spec=spec)
                        try:
                            op = isc.create(req).wait()
                            try:
                                op.sync_wait()
                            except Exception:
                                pass
                            created = True
                            
                            # Wait for VM to be fully ready with public IP assigned
                            print(f"[VMManager] Waiting for {inst_name} to receive public IP...")
                            import time
                            max_ip_wait = 60  # Wait up to 60 seconds for IP assignment
                            ip_wait_interval = 5
                            for attempt in range(max_ip_wait // ip_wait_interval):
                                time.sleep(ip_wait_interval)
                                vm_ip = self.get_vm_public_ip(inst_name)
                                if vm_ip:
                                    print(f"[VMManager] {inst_name} ready with IP: {vm_ip}")
                                    vm_ips[inst_name] = vm_ip
                                    break
                                if attempt < (max_ip_wait // ip_wait_interval) - 1:
                                    print(f"[VMManager] Waiting for IP assignment ({(attempt + 1) * ip_wait_interval}s elapsed)...")
                            else:
                                print(f"[VMManager] Warning: {inst_name} did not receive public IP within {max_ip_wait}s")
                        except Exception as e:
                            print(f"[VMManager] InstanceServiceClient create failed: {e}")
                    except Exception:
                        pass
                    if not created:
                        if instance_api is not None and hasattr(instance_api, "create"):
                            print(f"[VMManager] Creating instance {inst_name} ...")
                            try:
                                try:
                                    instance_api.create(**inst_req)  # type: ignore[arg-type]
                                except TypeError:
                                    instance_api.create(inst_req)
                                created = True
                            except Exception as e:
                                print(f"[VMManager] create failed for {inst_name}: {e}")
                        if not created:
                            print(f"[VMManager] Would create with payload: {inst_req}")
            else:
                # Fallback logging only
                for i in range(spec.instance_count):
                    inst_name = f"{spec.name}-{i}"
                    pub_ip = spec.external_ips[i] if i < len(spec.external_ips) else None
                    print(f"[VMManager] ensure instance {inst_name} pub_ip={pub_ip} platform={spec.vm_spec.get('platform')} subnet=vpngw-subnet")
        except Exception as e:
            print(f"[VMManager] ensure_group failed: {e}. Proceeding in scaffold mode.")
        
        return vm_ips

    def _ensure_vpngw_subnet(self, client: t.Any, spec: GatewayGroupSpec) -> t.Optional[str]:
        """Ensure a single gateway subnet named 'vpngw-subnet' (/27) exists in the chosen network.

        Resolution:
        - If vm_spec.network_id is provided, use that VPC network.
        - Else, find network by name 'default-network'.
        - Find subnet by name 'vpngw-subnet' in that network; create if missing with CIDR /27.
        Returns the subnet_id or None if not available.
        """
        if client is None:
            return None
        try:
            # Use explicit service clients from the Nebius SDK
            from nebius.api.nebius.vpc.v1 import (
                NetworkServiceClient,
                SubnetServiceClient,
                GetNetworkRequest,
                GetNetworkByNameRequest,
                GetSubnetByNameRequest,
                ListSubnetsByNetworkRequest,
                CreateSubnetRequest,
                PoolServiceClient,
                GetPoolRequest,
                IPv4PrivateSubnetPools,
                SubnetPool,
                SubnetCidr,
            )  # type: ignore

            net_client = NetworkServiceClient(client)  # type: ignore
            # Resolve network by id or by name 'default-network'
            network_id = spec.vm_spec.get("network_id")
            network_obj = None
            if network_id:
                try:
                    network_obj = net_client.get(GetNetworkRequest(id=network_id)).wait()
                except Exception:
                    network_obj = None
            if network_obj is None:
                try:
                    # SDK expects parent_id for get-by-name (project scope)
                    network_obj = net_client.get_by_name(
                        GetNetworkByNameRequest(parent_id=self.project_id or "", name="default-network")
                    ).wait()
                except Exception:
                    network_obj = None
            if network_obj is None:
                raise RuntimeError(
                    "[VMManager] No network_id provided and default-network not found. "
                    "Please set an existing network_id in your YAML."
                )

            subnet_client = SubnetServiceClient(client)  # type: ignore
            # Derive network_id robustly from object (supports metadata.id)
            net_id = getattr(network_obj, "id", None) or getattr(getattr(network_obj, "metadata", None), "id", None)
            subnet_obj = None
            try:
                # First attempt: direct by-name lookup (project-scoped)
                candidate = subnet_client.get_by_name(
                    GetSubnetByNameRequest(parent_id=self.project_id or "", name="vpngw-subnet")
                ).wait()
                # Validate it belongs to the resolved network
                if candidate is not None:
                    c_spec = getattr(candidate, "spec", None)
                    c_net_id = getattr(c_spec, "network_id", None)
                    if c_net_id == net_id:
                        subnet_obj = candidate
                # If by-name found but network mismatch, search within the target network
                if subnet_obj is None:
                    lst = subnet_client.list_by_network(
                        ListSubnetsByNetworkRequest(network_id=net_id)
                    ).wait()
                    items = getattr(lst, "items", []) or []
                    for s in items:
                        if getattr(getattr(s, "metadata", None), "name", None) == "vpngw-subnet":
                            subnet_obj = s
                            break
            except Exception:
                subnet_obj = None
            if subnet_obj is None:
                # Attempt to create the subnet in default-network.
                # If the project has no IP space, the create call will fail and we
                # will surface a clear error to the user.
                try:
                    print("[VMManager] Creating gateway subnet 'vpngw-subnet' ...")
                    from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
                    from nebius.api.nebius.vpc.v1 import SubnetSpec  # type: ignore
                    # Derive first-free /27 from the network's private pool CIDR(s)
                    # 1) Read network's private pool id
                    try:
                        # network_obj.spec.ipv4_private_pools.pools[0].id
                        net_spec = getattr(network_obj, "spec", None)
                        pools_obj = getattr(getattr(net_spec, "ipv4_private_pools", None), "pools", []) or []
                        private_pool_id = None
                        if pools_obj:
                            private_pool_id = getattr(pools_obj[0], "id", None)
                    except Exception:
                        private_pool_id = None
                    cidr_to_use = None
                    if private_pool_id:
                        try:
                            pool_client = PoolServiceClient(client)  # type: ignore
                            pool_obj = pool_client.get(GetPoolRequest(id=private_pool_id)).wait()
                            # Read pool CIDRs (take the first)
                            pool_spec = getattr(pool_obj, "spec", None)
                            pool_cidrs = getattr(pool_spec, "cidrs", []) or []
                            pool_cidr_str = None
                            if pool_cidrs:
                                pool_cidr_str = getattr(pool_cidrs[0], "cidr", None)
                            import ipaddress as _ip
                            if pool_cidr_str:
                                pool_net = _ip.ip_network(pool_cidr_str)
                                # Collect existing subnets in this network
                                try:
                                    existing = []
                                    lst = subnet_client.list_by_network(
                                        ListSubnetsByNetworkRequest(network_id=net_id)
                                    ).wait()
                                    items = getattr(lst, "items", []) or []
                                    for s in items:
                                        s_spec = getattr(s, "spec", None)
                                        sp = getattr(s_spec, "ipv4_private_pools", None)
                                        sp_pools = getattr(sp, "pools", []) or []
                                        for sp_pool in sp_pools:
                                            sp_cidrs = getattr(sp_pool, "cidrs", []) or []
                                            for c in sp_cidrs:
                                                cstr = getattr(c, "cidr", None)
                                                if cstr:
                                                    try:
                                                        existing.append(_ip.ip_network(cstr))
                                                    except Exception:
                                                        pass
                                except Exception:
                                    existing = []
                                # Iterate /27s within pool until first unused
                                for candidate in pool_net.subnets(new_prefix=27):
                                    overlap = any(
                                        (cand.overlaps(ex) or ex.overlaps(cand)) for cand, ex in [(candidate, e) for e in existing]
                                    )
                                    if not overlap:
                                        cidr_to_use = str(candidate)
                                        break
                        except Exception:
                            cidr_to_use = None
                    # Build SubnetSpec with ipv4_private_pools assigning the /27 slice
                    # Use proper SDK message objects instead of dicts
                    ipv4_private_pools = None
                    if cidr_to_use:
                        # Create message objects matching SDK schema:
                        # IPv4PrivateSubnetPools with list of SubnetPool containing SubnetCidr
                        ipv4_private_pools = IPv4PrivateSubnetPools(
                            pools=[
                                SubnetPool(
                                    cidrs=[
                                        SubnetCidr(cidr=cidr_to_use)
                                    ]
                                )
                            ],
                            use_network_pools=False,
                        )
                    else:
                        # Inherit network pools if computation failed; user can pre-create subnet
                        ipv4_private_pools = IPv4PrivateSubnetPools(use_network_pools=True)
                    req = CreateSubnetRequest(
                        metadata=ResourceMetadata(
                            name="vpngw-subnet",
                            parent_id=self.project_id or "",
                        ),
                        spec=SubnetSpec(
                            network_id=net_id,
                            ipv4_private_pools=ipv4_private_pools,
                        ),
                    )
                    op = subnet_client.create(req).wait()  # Operation
                    try:
                        op.sync_wait()
                    except Exception:
                        pass
                    # After creation, refetch by name to obtain the id
                    try:
                        subnet_obj = subnet_client.get_by_name(
                            GetSubnetByNameRequest(parent_id=self.project_id or "", name="vpngw-subnet")
                        ).wait()
                    except Exception:
                        subnet_obj = None
                except Exception as e:
                    raise RuntimeError(
                        f"[VMManager] Failed to create 'vpngw-subnet' in default-network: {e}. "
                        "Please provide a network_id with sufficient IP space or pre-create the subnet."
                    )

            # Extract subnet id robustly: some SDKs expose id under metadata.id
            if subnet_obj is not None:
                sid = getattr(subnet_obj, "id", None)
                if not sid:
                    sid = getattr(getattr(subnet_obj, "metadata", None), "id", None)
                return sid
            return None
        except Exception as e:
            print(f"[VMManager] ensure_vpngw_subnet failed: {e}")
            return None
        except Exception as e:
            print(f"[VMManager] ensure_group encountered an error: {e}")

    def get_instance_ssh_target(self, instance_index: int) -> str:
        # Placeholder fallback if external IP wasn't available in plan
        return f"{instance_index}"

    def _build_cloud_init(self, ssh_key: t.Optional[str] = None) -> str:
        """Return a basic cloud-init to install deps and unit file.

        This does not install the agent binary itself; it prepares the unit and
        directories so the orchestrator/ops can deploy the binary later. The unit
        is enabled (but not forcibly started) to avoid failing if the binary is
        not yet present.
        
        Args:
            ssh_key: Optional SSH public key to add to ubuntu user's authorized_keys
        """
        try:
            with resources.as_file(resources.files("nebius_vpngw").joinpath("systemd/nebius-vpngw-agent.service")) as p:
                unit_text = p.read_text(encoding="utf-8")
        except Exception:
            unit_text = textwrap.dedent(
                """
                [Unit]
                Description=Nebius VPNGW Agent
                After=network.target

                [Service]
                Type=simple
                ExecStart=/usr/bin/nebius-vpngw-agent
                ExecReload=/bin/kill -HUP $MAINPID
                Restart=always
                RestartSec=3

                [Install]
                WantedBy=multi-user.target
                """
            ).strip()

        indented_unit = textwrap.indent(unit_text, " " * 12)
        
        # Build users section with SSH key if provided
        users_section = ""
        if ssh_key:
            users_section = (
                "users:\n"
                "  - name: ubuntu\n"
                "    ssh_authorized_keys:\n"
                f"      - {ssh_key}\n"
            )
        
        cloud = (
            "#cloud-config\n"
            f"{users_section}"
            "package_update: true\n"
            "package_upgrade: false\n"
            "packages:\n"
            "  - strongswan\n"
            "  - strongswan-pki\n"
            "  - libcharon-extra-plugins\n"
            "  - frr\n"
            "  - frr-pythontools\n"
            "  - python3\n"
            "  - python3-pip\n"
            "  - python3-yaml\n"
            "write_files:\n"
            "  - path: /etc/systemd/system/nebius-vpngw-agent.service\n"
            "    permissions: \"0644\"\n"
            "    owner: root:root\n"
            "    content: |\n"
            f"{indented_unit}\n"
            "  - path: /etc/frr/daemons\n"
            "    permissions: \"0644\"\n"
            "    owner: frr:frr\n"
            "    content: |\n"
            "            # FRR daemons configuration - enable bgpd\n"
            "            bgpd=yes\n"
            "            ospfd=no\n"
            "            ospf6d=no\n"
            "            ripd=no\n"
            "            ripngd=no\n"
            "            isisd=no\n"
            "            pimd=no\n"
            "            ldpd=no\n"
            "            nhrpd=no\n"
            "            eigrpd=no\n"
            "            babeld=no\n"
            "            sharpd=no\n"
            "            pbrd=no\n"
            "            bfdd=no\n"
            "            fabricd=no\n"
            "            vrrpd=no\n"
            "  - path: /etc/sysctl.d/99-vpn-gateway.conf\n"
            "    permissions: \"0644\"\n"
            "    owner: root:root\n"
            "    content: |\n"
            "            # IP forwarding for VPN gateway\n"
            "            net.ipv4.ip_forward=1\n"
            "            net.ipv6.conf.all.forwarding=1\n"
            "            # Disable RP filter for asymmetric routing in VPN scenarios\n"
            "            net.ipv4.conf.all.rp_filter=0\n"
            "            net.ipv4.conf.default.rp_filter=0\n"
            "runcmd:\n"
            "  - [ bash, -lc, \"mkdir -p /etc/nebius-vpngw\" ]\n"
            "  - [ bash, -lc, \"mkdir -p /etc/ipsec.d\" ]\n"
            "  - [ sysctl, -p, /etc/sysctl.d/99-vpn-gateway.conf ]\n"
            "  - [ systemctl, daemon-reload ]\n"
            "  - [ systemctl, enable, strongswan-starter ]\n"
            "  - [ systemctl, enable, frr ]\n"
            "  - [ systemctl, enable, nebius-vpngw-agent ]\n"
            "  - [ systemctl, start, strongswan-starter ]\n"
            "  - [ systemctl, start, frr ]\n"
        )
        return cloud
