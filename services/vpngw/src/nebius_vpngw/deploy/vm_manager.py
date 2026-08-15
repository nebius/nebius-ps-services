from __future__ import annotations

import copy
import importlib.resources as resources
import ipaddress
import os
import subprocess
import textwrap
import time
import typing as t
from dataclasses import dataclass, replace
from pathlib import Path

from ..config_loader import GatewayGroupSpec
from ..schema import (
    VMHACredentialReferences,
    VMHARole,
    VMHARouteTarget,
    VMHARuntimeBinding,
    VMHARuntimeNodeBinding,
)
from .ssh_policy import SSHTrustPolicy, build_openssh_base_command
from .vm_diff import VMDiffAnalyzer, VMSpec
from .vm_ha_identity import (
    PROVISIONING_MARKER_PREFIX,
    FormerVMHAEvidence,
    FormerVMHAProvenance,
    LegacyVMHAIdentity,
    classify_former_vm_ha_evidence,
    compute_provisioning_provenance,
    render_provisioning_marker,
)
from .vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    lifecycle_member_map,
)

if t.TYPE_CHECKING:
    from .vm_ha_cloud import VMHACloudAdapter


def _read_firewall_setup_script() -> str:
    script_path = Path(__file__).resolve().parents[1] / "systemd" / "setup-vpngw-firewall.sh"
    return script_path.read_text(encoding="utf-8")


def _read_esp4_preflight_script() -> str:
    script_path = Path(__file__).resolve().parents[1] / "systemd" / "nebius-vpngw-esp4-preflight.sh"
    return script_path.read_text(encoding="utf-8")


def _parse_ipv4_network(cidr: str) -> ipaddress.IPv4Network | None:
    """Parse a CIDR as IPv4 only.

    The gateway subnet flow is IPv4-only. `ipaddress.ip_network()` returns an
    IPv4/IPv6 union, so we narrow it explicitly for both runtime safety and
    static type-checkers.
    """
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return None
    if isinstance(network, ipaddress.IPv4Network):
        return network
    return None


@dataclass
class VMProvisioningConfig:
    subnet_id: str | None
    num_nics: int
    platform: str
    preset: str | None
    boot_image: str
    disk_gb: int
    disk_type: str
    disk_block_bytes: int
    cloud_init: str


class VMProvisioningResult(dict[str, str]):
    """VM addresses plus the authoritative binding emitted only for explicit HA."""

    def __init__(
        self,
        vm_ips: dict[str, str],
        *,
        vm_ha_runtime_binding: VMHARuntimeBinding,
    ) -> None:
        super().__init__(vm_ips)
        self.vm_ha_runtime_binding = vm_ha_runtime_binding


class VMManager:
    """Manage Nebius gateway VM lifecycle.

    This is a scaffold placeholder. Integrate with Nebius Python SDK when available.
    """

    def __init__(
        self,
        project_id: str | None,
        zone: str | None,
        auth_token: str | None = None,
        tenant_id: str | None = None,
        region_id: str | None = None,
        ssh_policy: SSHTrustPolicy | None = None,
        management_key_path: Path | None = None,
    ) -> None:
        self.project_id = project_id
        self.zone = zone
        self.auth_token = auth_token
        self.tenant_id = tenant_id
        self.region_id = region_id
        self._ssh_policy = ssh_policy
        self._management_key_path = management_key_path
        self.diff_analyzer = VMDiffAnalyzer()
        self._private_alloc_ids: dict[str, list[str]] = {}
        self._vm_ha_shared_allocation_id: str | None = None
        self._former_vm_ha_snapshot: dict[str, tuple[t.Any, str]] | None = None
        self._former_vm_ha_evidence: FormerVMHAEvidence | None = None
        self._former_vm_ha_candidate_provenance: FormerVMHAProvenance | None = None
        self._former_vm_ha_lifecycle: VMHALifecycleState | None = None
        self._vm_ha_route_targets: tuple[VMHARouteTarget, ...] | None = None

    def get_ha_instance(self, instance_id: str) -> t.Any:
        """Read one Compute instance without permissive provisioning fallback."""
        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client is unavailable for VM-HA fencing")
        from nebius.api.nebius.compute.v1 import (  # type: ignore
            GetInstanceRequest,
            InstanceServiceClient,
        )

        return InstanceServiceClient(client).get(GetInstanceRequest(id=instance_id)).wait()

    def _get_ha_instance_by_name(self, client: t.Any, name: str) -> t.Any:
        """Resolve one HA Compute identity without treating SDK errors as absence."""
        if not self.project_id:
            raise RuntimeError("VM-HA Compute lookup requires a project ID")
        from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
        from nebius.api.nebius.compute.v1 import InstanceServiceClient  # type: ignore

        instance = (
            InstanceServiceClient(client)
            .get_by_name(GetByNameRequest(parent_id=self.project_id, name=name))
            .wait()
        )
        instance_id = self._resource_id(instance)
        if not instance_id:
            raise RuntimeError(f"VM-HA Compute {name} has no authoritative identity")
        authoritative = self.get_ha_instance(instance_id)
        if self._resource_id(authoritative) != instance_id:
            raise RuntimeError(f"VM-HA Compute {name} changed identity during re-read")
        return authoritative

    def stop_ha_instance(self, instance_id: str) -> None:
        """Request a Compute stop without accepting scaffold-mode success."""
        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client is unavailable for VM-HA fencing")
        from nebius.api.nebius.compute.v1 import (  # type: ignore
            InstanceServiceClient,
            StopInstanceRequest,
        )

        InstanceServiceClient(client).stop(StopInstanceRequest(id=instance_id)).wait()

    def get_ha_allocation(self, allocation_id: str) -> t.Any:
        """Read one allocation without converting access failure into absence."""
        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client is unavailable for VM-HA fencing")
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            AllocationServiceClient,
            GetAllocationRequest,
        )

        return AllocationServiceClient(client).get(GetAllocationRequest(id=allocation_id)).wait()

    def set_ha_private_allocation(
        self,
        instance_id: str,
        network_interface_name: str,
        allocation_id: str | None,
    ) -> None:
        """Idempotently set the exact private allocation on one instance NIC."""
        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client is unavailable for VM-HA fencing")
        from nebius.api.nebius.compute.v1 import (  # type: ignore
            InstanceServiceClient,
            IPAddress,
            UpdateInstanceRequest,
        )

        instance = self.get_ha_instance(instance_id)
        spec = copy.deepcopy(getattr(instance, "spec", None))
        metadata = copy.deepcopy(getattr(instance, "metadata", None))
        if spec is None or metadata is None:
            raise RuntimeError(f"Compute instance {instance_id} has no mutable spec metadata")

        interfaces = list(getattr(spec, "network_interfaces", []) or [])
        matching = [
            (index, interface)
            for index, interface in enumerate(interfaces)
            if str(getattr(interface, "name", "")) == network_interface_name
        ]
        if len(matching) != 1:
            raise RuntimeError(
                f"Compute instance {instance_id} must have exactly one NIC named "
                f"{network_interface_name}; found {len(matching)}"
            )

        index, interface = matching[0]
        current_id = getattr(getattr(interface, "ip_address", None), "allocation_id", None)
        normalized_current = str(current_id) if current_id else None
        if normalized_current == allocation_id:
            return

        updated_interface = copy.deepcopy(interface)
        updated_interface.ip_address = (
            IPAddress(allocation_id=allocation_id) if allocation_id else IPAddress()
        )
        interfaces[index] = updated_interface
        spec.network_interfaces = interfaces
        InstanceServiceClient(client).update(
            UpdateInstanceRequest(metadata=metadata, spec=spec)
        ).wait()

    def vm_ha_cloud_adapter(
        self,
        *,
        attempts: int = 10,
        poll_interval: float = 1.0,
        sleeper: t.Callable[[float], None] = time.sleep,
    ) -> VMHACloudAdapter:
        """Build the strict policy-facing adapter over the SDK translations."""
        from .vm_ha_cloud import VMHACloudAdapter

        return VMHACloudAdapter(
            instance_reader=self.get_ha_instance,
            instance_stopper=self.stop_ha_instance,
            allocation_reader=self.get_ha_allocation,
            allocation_setter=self.set_ha_private_allocation,
            attempts=attempts,
            poll_interval=poll_interval,
            sleeper=sleeper,
        )

    @staticmethod
    def _vm_ha_credential_references(node_id: str) -> VMHACredentialReferences:
        base = f"/etc/nebius-vpngw/vm-ha/{node_id}"
        return VMHACredentialReferences(
            certificate_authority="/etc/nebius-vpngw/vm-ha/ca.crt",
            certificate=f"{base}.crt",
            private_key=f"{base}.key",
        )

    def _build_vm_ha_runtime_binding(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
    ) -> VMHARuntimeBinding:
        vm_ha = spec.vm_ha
        allocation_id = self._vm_ha_shared_allocation_id
        route_targets = self._vm_ha_route_targets
        if vm_ha is None or not allocation_id or not route_targets:
            raise RuntimeError("VM-HA runtime binding requires complete provisioning intent")
        allocation = self.get_ha_allocation(allocation_id)
        if self._resource_id(allocation) != allocation_id:
            raise RuntimeError("VM-HA shared allocation changed identity during final re-read")
        from .vm_ha_cloud import AllocationOwner, allocation_observation

        allocation_owner = allocation_observation(allocation_id, allocation).owner

        nodes: list[VMHARuntimeNodeBinding] = []
        for member in vm_ha.members:
            instance_name = f"{spec.name}-{member.instance_index}"
            instance = self._get_ha_instance_by_name(client, instance_name)
            compute_id = self._resource_id(instance)
            if not compute_id:
                raise RuntimeError(f"VM-HA Compute identity unavailable for {instance_name}")
            interfaces = list(
                getattr(getattr(instance, "spec", None), "network_interfaces", []) or []
            )
            if len(interfaces) != 1 or not getattr(interfaces[0], "name", None):
                raise RuntimeError(f"VM-HA {instance_name} must have one authoritative NIC")
            interface_name = str(interfaces[0].name)
            compute_allocation_id = str(
                getattr(getattr(interfaces[0], "ip_address", None), "allocation_id", None) or ""
            )
            if member.role.value == "active" and compute_allocation_id != allocation_id:
                raise RuntimeError(
                    "VM-HA configured active Compute NIC does not own the shared allocation"
                )
            if member.role.value != "active" and compute_allocation_id == allocation_id:
                raise RuntimeError(
                    "VM-HA passive Compute NIC conflicts with shared allocation ownership"
                )
            status_interfaces = list(
                getattr(getattr(instance, "status", None), "network_interfaces", []) or []
            )
            if len(status_interfaces) != 1:
                raise RuntimeError(f"VM-HA {instance_name} has ambiguous runtime NIC state")
            address = getattr(getattr(status_interfaces[0], "ip_address", None), "address", None)
            endpoint_address = self._normalize_ip_value(str(address)) if address else None
            if not endpoint_address:
                raise RuntimeError(f"VM-HA {instance_name} has no authoritative peer endpoint")
            nodes.append(
                VMHARuntimeNodeBinding(
                    node_id=member.node_id,
                    role=VMHARole(member.role.value),
                    compute_id=compute_id,
                    network_interface_name=interface_name,
                    peer_endpoint=f"{endpoint_address}:9443",
                    credentials=self._vm_ha_credential_references(member.node_id),
                )
            )

        active = next(node for node in nodes if node.role is VMHARole.ACTIVE)
        if allocation_owner != AllocationOwner(
            active.compute_id,
            active.network_interface_name,
        ):
            raise RuntimeError("VM-HA shared allocation is not exact on configured active")
        final_allocation = self.get_ha_allocation(allocation_id)
        if self._resource_id(final_allocation) != allocation_id or (
            allocation_observation(allocation_id, final_allocation).owner != allocation_owner
        ):
            raise RuntimeError("VM-HA shared allocation ownership changed during final binding")

        digests = vm_ha.generation.digests
        return VMHARuntimeBinding(
            cluster_id=vm_ha.cluster_id,
            shared_allocation_id=allocation_id,
            nodes=t.cast(tuple[VMHARuntimeNodeBinding, VMHARuntimeNodeBinding], tuple(nodes)),
            route_targets=route_targets,
            route_runtime_id=VMHARuntimeBinding.derive_route_runtime_id(
                vm_ha.cluster_id, allocation_id, route_targets
            ),
            generation_id=vm_ha.generation.generation_id,
            configuration_digest=digests.configuration,
            static_routes_digest=digests.static_routes,
            bgp_policy_digest=digests.bgp_policy,
        )

    def _resolve_vm_ha_route_targets(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
    ) -> tuple[VMHARouteTarget, ...]:
        if not self.project_id:
            raise RuntimeError("VM-HA route targets require an exact project ID")
        if not local_prefixes:
            raise RuntimeError("VM-HA route targets require gateway.local_prefixes")
        _, network_id, _, subnet_client = self._resolve_gateway_network(client, spec)
        from nebius.api.nebius.vpc.v1 import ListSubnetsByNetworkRequest  # type: ignore

        from .route_manager import RouteManager

        route_manager = RouteManager(project_id=self.project_id)

        def observe() -> tuple[VMHARouteTarget, ...]:
            observed = subnet_client.list_by_network(
                ListSubnetsByNetworkRequest(network_id=network_id)
            ).wait()
            return route_manager.resolve_vm_ha_route_targets(
                getattr(observed, "items", ()) or (),
                local_prefixes,
                project_id=self.project_id or "",
                target_network_id=network_id,
                gateway_subnet_name=str(self._gateway_subnet_settings(spec)["name"]),
            )

        first = observe()
        if observe() != first:
            raise RuntimeError("VM-HA route target membership changed during binding")
        return first

    def _attach_vm_ha_shared_allocation_initially(
        self,
        *,
        allocation_id: str,
        active_compute_id: str,
        active_network_interface_name: str,
    ) -> None:
        """Attach only from detached state; provisioning never performs a takeover."""
        from .vm_ha_cloud import AllocationOwner, allocation_observation

        expected = AllocationOwner(active_compute_id, active_network_interface_name)
        observed = allocation_observation(
            allocation_id,
            self.get_ha_allocation(allocation_id),
        ).owner
        if observed == expected:
            return
        if observed is not None:
            raise RuntimeError(
                "VM-HA shared allocation is already attached outside configured active"
            )
        self.set_ha_private_allocation(
            active_compute_id,
            active_network_interface_name,
            allocation_id,
        )

    def check_changes(self, spec: GatewayGroupSpec) -> list[tuple[str, t.Any]]:
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

        results: list[tuple[str, t.Any]] = []
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

    def _get_client(self) -> t.Any | None:
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
            client_cls = t.cast(t.Any, Client)
            if self.tenant_id and self.project_id and self.region_id:
                try:
                    return client_cls(
                        tenant_id=self.tenant_id,
                        project_id=self.project_id,
                        region_id=self.region_id,
                    )
                except TypeError:
                    return client_cls()
            else:
                return client_cls()
        except Exception:
            return None

    def _get_vm_by_name(self, client: t.Any, name: str) -> t.Any | None:
        """Get VM by name, returns None if not found."""
        try:
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
            from nebius.api.nebius.compute.v1 import InstanceServiceClient  # type: ignore

            isc = InstanceServiceClient(client)
            if hasattr(isc, "get_by_name") and self.project_id:
                try:
                    vm = isc.get_by_name(
                        GetByNameRequest(parent_id=self.project_id, name=name)
                    ).wait()
                    return vm
                except Exception:
                    return None
        except Exception:
            pass
        return None

    def _get_disk_by_name(self, client: t.Any, name: str) -> t.Any | None:
        """Get disk by name, returns None if not found."""
        try:
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
            from nebius.api.nebius.compute.v1 import DiskServiceClient  # type: ignore

            dsc = DiskServiceClient(client)
            if hasattr(dsc, "get_by_name") and self.project_id:
                try:
                    disk = dsc.get_by_name(
                        GetByNameRequest(parent_id=self.project_id, name=name)
                    ).wait()
                    return disk
                except Exception:
                    return None
        except Exception:
            pass
        return None

    @staticmethod
    def _gateway_subnet_settings(spec: GatewayGroupSpec) -> dict[str, t.Any]:
        subnet_cfg = spec.subnet or {}
        prefix_length_value = subnet_cfg.get("prefix_length")
        return {
            "name": str(subnet_cfg.get("name") or "vpngw-subnet").strip() or "vpngw-subnet",
            "cidr": str(subnet_cfg.get("cidr")).strip() if subnet_cfg.get("cidr") else None,
            "prefix_length": int(prefix_length_value) if prefix_length_value is not None else 24,
        }

    @staticmethod
    def _gateway_subnet_name(spec: GatewayGroupSpec) -> str:
        return str(VMManager._gateway_subnet_settings(spec)["name"])

    @staticmethod
    def _gateway_route_table_name(subnet_name: str) -> str:
        return f"{subnet_name}-routing-table"[:63]

    @staticmethod
    def _extract_explicit_subnet_networks(subnet_obj: t.Any) -> list[ipaddress.IPv4Network]:
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
                network = _parse_ipv4_network(str(cidr))
                if network is not None:
                    networks.append(network)
        return networks

    def _get_network_private_pools(
        self, client: t.Any, network_obj: t.Any
    ) -> list[tuple[str, t.Any]]:
        from nebius.api.nebius.vpc.v1 import GetPoolRequest, PoolServiceClient  # type: ignore

        net_spec = getattr(network_obj, "spec", None)
        pool_refs = getattr(getattr(net_spec, "ipv4_private_pools", None), "pools", []) or []
        pool_client = PoolServiceClient(client)  # type: ignore

        pools: list[tuple[str, t.Any]] = []
        for pool_ref in pool_refs:
            pool_id = getattr(pool_ref, "pool_id", None) or getattr(pool_ref, "id", None)
            if not pool_id:
                continue
            pool_id = str(pool_id)
            pool_obj = pool_client.get(GetPoolRequest(id=pool_id)).wait()
            pools.append((pool_id, pool_obj))
        return pools

    def _ensure_network_pool_contains_cidr(
        self, client: t.Any, network_obj: t.Any, desired_network: ipaddress.IPv4Network
    ) -> None:
        from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
        from nebius.api.nebius.vpc.v1 import (
            PoolCidr,
            PoolServiceClient,
            PoolSpec,
            UpdatePoolRequest,
        )  # type: ignore

        network_pools = self._get_network_private_pools(client, network_obj)
        for _, pool_obj in network_pools:
            for pool_cidr_obj in getattr(getattr(pool_obj, "spec", None), "cidrs", []) or []:
                pool_cidr = getattr(pool_cidr_obj, "cidr", None)
                if not pool_cidr:
                    continue
                pool_network = _parse_ipv4_network(str(pool_cidr))
                if pool_network is None:
                    continue
                if desired_network.subnet_of(pool_network):
                    return

        if len(network_pools) != 1:
            raise RuntimeError(
                f"Gateway subnet CIDR {desired_network} is outside the current network pool(s). "
                "Automatic pool extension is supported only when the target network has exactly "
                "one private pool. Extend the network pool manually or specify gateway_group.network_id "
                "for a network with a single private pool."
            )

        pool_id, pool_obj = network_pools[0]
        pool_client = PoolServiceClient(client)  # type: ignore
        pool_meta = getattr(pool_obj, "metadata", None)
        pool_spec = getattr(pool_obj, "spec", None)
        if not pool_meta or not pool_spec:
            raise RuntimeError(f"Network private pool {pool_id} is missing metadata/spec.")

        updated_cidrs = [
            PoolCidr(
                cidr=getattr(pool_cidr, "cidr", ""),
                max_mask_length=getattr(pool_cidr, "max_mask_length", 0),
                state=getattr(pool_cidr, "state", None),
            )
            for pool_cidr in getattr(pool_spec, "cidrs", []) or []
        ]
        updated_cidrs.append(PoolCidr(cidr=str(desired_network)))

        updated_spec = PoolSpec(
            cidrs=updated_cidrs,
            version=getattr(pool_spec, "version", None),
            visibility=getattr(pool_spec, "visibility", None),
            source_pool_id=getattr(pool_spec, "source_pool_id", ""),
        )

        pool_name = getattr(pool_meta, "name", None) or pool_id
        print(
            f"[VMManager] Extending network private pool '{pool_name}' with {desired_network} ..."
        )

        pool_client.update(
            UpdatePoolRequest(
                metadata=ResourceMetadata(
                    id=getattr(pool_meta, "id", pool_id),
                    parent_id=getattr(pool_meta, "parent_id", ""),
                    name=getattr(pool_meta, "name", ""),
                ),
                spec=updated_spec,
            )
        ).wait()

    @staticmethod
    def _find_first_free_subnet_cidr(
        network_pool_cidrs: list[ipaddress.IPv4Network],
        existing_subnet_cidrs: list[ipaddress.IPv4Network],
        *,
        prefix_length: int,
    ) -> str | None:
        for pool_network in network_pool_cidrs:
            if prefix_length < pool_network.prefixlen:
                continue

            candidates: t.Iterable[ipaddress.IPv4Network]
            if prefix_length == pool_network.prefixlen:
                candidates = [pool_network]
            else:
                candidates = pool_network.subnets(new_prefix=prefix_length)

            for candidate in candidates:
                if any(candidate.overlaps(existing) for existing in existing_subnet_cidrs):
                    continue
                return str(candidate)

        return None

    def get_vm_public_ip(self, vm_name: str) -> str | None:
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
                            ip_str = str(address).split("/")[0]
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
                            ip_str = str(address).split("/")[0]
                            return ip_str
        except Exception:
            pass
        return None

    def get_allocation_ip(self, allocation_id: str) -> str | None:
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

            from nebius.api.nebius.vpc.v1 import (
                AllocationServiceClient,  # type: ignore
                GetAllocationRequest,  # type: ignore
            )

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
        printed_progress = False

        while time.time() - start_time < timeout:
            attempt += 1
            try:
                # Ping with 1 second timeout, 1 packet
                result = subprocess.run(
                    ["ping", "-c", "1", "-W", "1", ip_address],
                    capture_output=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    elapsed = int(time.time() - start_time)
                    if printed_progress:
                        print()  # finish the progress line
                    print(f"[VMManager] ✓ {vm_name} is reachable (took {elapsed}s)")
                    return True
                else:
                    # Show progress
                    if attempt % 3 == 0:  # Every 3 attempts
                        print(".", end="", flush=True)
                        printed_progress = True
            except Exception:
                pass

            time.sleep(1)

        print(f"\n✗ Timeout waiting for {vm_name} to become reachable")
        return False

    def get_vm_allocations(self, vm_name: str) -> list[tuple[int, str]]:
        """Get allocation IDs attached to a VM's network interfaces.

        Args:
            vm_name: Name of the VM instance

        Returns:
            List of (nic_index, allocation_id) tuples
        """
        allocations: list[tuple[int, str]] = []
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
                'esp4_ready': bool,
                'esp4_reboot_pending': bool,
                'message': str
            }
        """
        import subprocess
        import time

        result: dict[str, t.Any] = {
            "reachable": False,
            "cloud_init_complete": False,
            "strongswan_installed": False,
            "frr_installed": False,
            "agent_installed": False,
            "esp4_ready": False,
            "esp4_reboot_pending": False,
            "message": "VM not reachable",
        }

        # Wait a moment for VM to boot and network to initialize
        time.sleep(2)
        ssh_base = build_openssh_base_command(
            key_path=self._management_key_path,
            connect_timeout=5,
            policy=self._ssh_policy,
            hostname=vm_name if self._ssh_policy is not None else None,
        )

        # Test SSH connectivity
        try:
            ssh_test = subprocess.run(
                ssh_base
                + [
                    f"ubuntu@{public_ip}",
                    "echo connected",
                ],
                capture_output=True,
                timeout=10,
            )
        except Exception as e:
            result["message"] = f"SSH connection failed: {e}"
            return result
        if ssh_test.returncode != 0:
            detail = ssh_test.stderr.decode(errors="replace").lower()
            if (
                "host key verification failed" in detail
                or "remote host identification has changed" in detail
            ):
                raise RuntimeError(f"SSH host identity verification failed for {public_ip}")
            result["message"] = "SSH not ready yet"
            return result
        result["reachable"] = True

        # Check cloud-init status
        try:
            cloud_init_check = subprocess.run(
                ssh_base
                + [
                    f"ubuntu@{public_ip}",
                    "cloud-init status --wait --long 2>/dev/null || cloud-init status",
                ],
                capture_output=True,
                timeout=30,
                text=True,
            )
            if (
                "done" in cloud_init_check.stdout.lower()
                or "status: done" in cloud_init_check.stdout.lower()
            ):
                result["cloud_init_complete"] = True
                # Also verify pip module is available (critical for package installation)
                pip_check = subprocess.run(
                    ssh_base
                    + [
                        f"ubuntu@{public_ip}",
                        "python3 -m pip --version 2>/dev/null",
                    ],
                    capture_output=True,
                    timeout=10,
                    text=True,
                )
                if pip_check.returncode != 0:
                    # pip module not available yet, cloud-init might not be fully done
                    result["cloud_init_complete"] = False
        except Exception:
            pass

        if result["cloud_init_complete"]:
            try:
                esp4_check = subprocess.run(
                    ssh_base
                    + [
                        f"ubuntu@{public_ip}",
                        (
                            "if [ -f /var/lib/nebius-vpngw/esp4-reboot-pending ]; then "
                            "echo reboot-pending; exit 75; "
                            "fi; "
                            "if [ -x /usr/local/bin/nebius-vpngw-esp4-preflight.sh ]; then "
                            "sudo /usr/local/bin/nebius-vpngw-esp4-preflight.sh --verify "
                            ">/dev/null 2>&1; "
                            "else "
                            "sudo modprobe esp4 >/dev/null 2>&1; "
                            "fi"
                        ),
                    ],
                    capture_output=True,
                    timeout=20,
                    text=True,
                )
                if esp4_check.returncode == 0:
                    result["esp4_ready"] = True
                elif esp4_check.returncode == 75 or "reboot-pending" in esp4_check.stdout:
                    result["esp4_reboot_pending"] = True
                    result["esp4_ready"] = False
            except Exception:
                result["esp4_ready"] = False

        # Check installed packages
        try:
            pkg_check = subprocess.run(
                ssh_base
                + [
                    f"ubuntu@{public_ip}",
                    'dpkg -l strongswan frr 2>/dev/null | grep "^ii" && systemctl is-active nebius-vpngw-agent 2>/dev/null',
                ],
                capture_output=True,
                timeout=10,
                text=True,
            )
            if "strongswan" in pkg_check.stdout:
                result["strongswan_installed"] = True
            if "frr" in pkg_check.stdout:
                result["frr_installed"] = True
            if "active" in pkg_check.stdout:
                result["agent_installed"] = True
        except Exception:
            pass

        # Generate status message
        if (
            result["cloud_init_complete"]
            and result["strongswan_installed"]
            and result["frr_installed"]
            and result["esp4_ready"]
        ):
            result["message"] = (
                "✓ VM ready: cloud-init complete, strongSwan and FRR installed, ESP4 ready"
            )
            if result["agent_installed"]:
                result["message"] += ", agent running"
        elif result["esp4_reboot_pending"]:
            result["message"] = "⏳ ESP4/kernel update prepared; waiting for gateway reboot"
        elif result["cloud_init_complete"] and not result["esp4_ready"]:
            result["message"] = "⚠ Cloud-init complete but ESP4 is not ready"
        elif result["cloud_init_complete"]:
            result["message"] = "⚠ Cloud-init complete but packages not verified"
        else:
            result["message"] = "⏳ Cloud-init still running (packages being installed)"

        return result

    @staticmethod
    def _resolve_service_handle(obj: t.Any, name: str) -> t.Any:
        if obj is None:
            return None
        attr = getattr(obj, name, None)
        if attr is None:
            return None
        try:
            return attr() if callable(attr) else attr
        except Exception:
            return attr

    def _build_sdk_client(self, region: str) -> t.Any | None:
        try:
            import os

            if self.auth_token and not os.environ.get("NEBIUS_IAM_TOKEN"):
                os.environ["NEBIUS_IAM_TOKEN"] = self.auth_token
        except Exception:
            pass

        try:
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
                                Client = None

            if Client is None:
                raise ImportError("Nebius SDK not found")

            client_cls = t.cast(t.Any, Client)
            if self.tenant_id and self.project_id and (self.region_id or region):
                try:
                    return client_cls(
                        tenant_id=self.tenant_id,
                        project_id=self.project_id,
                        region_id=self.region_id or region,
                    )
                except TypeError:
                    return client_cls()
                except Exception:
                    return client_cls()

            try:
                from nebius.aio.cli_config import Config  # type: ignore

                try:
                    return client_cls(config_reader=Config(no_parent_id=True))
                except TypeError:
                    return client_cls()
            except Exception:
                return client_cls()
        except Exception as e:
            print(
                "[VMManager] Nebius SDK not available; install with 'pip install nebius'. "
                f"Running in dry scaffold mode: {e}"
            )
            return None

    def _resolve_client_apis(self, client: t.Any) -> tuple[t.Any, t.Any, t.Any, t.Any]:
        compute = self._resolve_service_handle(client, "compute") or self._resolve_service_handle(
            getattr(client, "cloud", None),
            "compute",
        )
        vpc = (
            self._resolve_service_handle(client, "vpc")
            or self._resolve_service_handle(getattr(client, "network", None), "vpc")
            or self._resolve_service_handle(getattr(client, "cloud", None), "vpc")
        )

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

        return instance_api, disk_api, alloc_api, alloc_client

    def _discover_existing_instances(self, client: t.Any, spec: GatewayGroupSpec) -> list[t.Any]:
        existing: list[t.Any] = []
        for i in range(spec.instance_count):
            inst_name = f"{spec.name}-{i}"
            vm_obj = self._get_vm_by_name(client, inst_name)
            if vm_obj:
                existing.append(vm_obj)
        return existing

    @staticmethod
    def _vm_public_ip_from_object(vm_obj: t.Any) -> str | None:
        for owner in (getattr(vm_obj, "status", None), getattr(vm_obj, "spec", None)):
            interfaces = list(getattr(owner, "network_interfaces", []) or []) if owner else []
            if not interfaces:
                continue
            public = getattr(interfaces[0], "public_ip_address", None)
            address = getattr(public, "address", None) if public else None
            if address:
                return str(address).split("/", 1)[0]
        return None

    def _get_vm_by_name_for_vm_ha_preflight(self, client: t.Any, name: str) -> t.Any | None:
        if not self.project_id:
            raise RuntimeError("VM-HA member discovery requires an exact project ID")
        try:
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
            from nebius.api.nebius.compute.v1 import InstanceServiceClient  # type: ignore

            response = (
                InstanceServiceClient(client)
                .get_by_name(GetByNameRequest(parent_id=self.project_id, name=name))
                .wait()
            )
        except Exception as error:
            code = getattr(error, "code", None)
            try:
                code = code() if callable(code) else code
            except Exception:
                code = None
            code_name = str(getattr(code, "name", code) or "").upper()
            if code_name == "NOT_FOUND" or "NOT_FOUND" in str(error).upper():
                return None
            raise RuntimeError(
                f"VM-HA member {name} could not be classified as existing or fresh"
            ) from error
        if response is None:
            raise RuntimeError(f"VM-HA member discovery returned no result for {name}")
        return response

    def _discover_vm_ha_members(
        self, client: t.Any, spec: GatewayGroupSpec
    ) -> dict[str, tuple[t.Any, str]]:
        existing: dict[str, tuple[t.Any, str]] = {}
        for index in range(spec.instance_count):
            name = f"{spec.name}-{index}"
            vm_obj = self._get_vm_by_name_for_vm_ha_preflight(client, name)
            if vm_obj is None:
                continue
            public_ip = self._vm_public_ip_from_object(vm_obj)
            if not public_ip:
                raise RuntimeError(
                    f"Existing VM-HA member {name} has no readable public SSH address"
                )
            existing[name] = (vm_obj, public_ip)
        return existing

    def _require_vm_ha_member_snapshot(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        expected: dict[str, tuple[t.Any, str]],
    ) -> None:
        current = self._discover_vm_ha_members(client, spec)
        if set(current) != set(expected):
            raise RuntimeError("VM-HA member set changed after SSH identity verification")
        for name, (expected_vm, expected_ip) in expected.items():
            current_vm, current_ip = current[name]
            expected_id = self._resource_id(expected_vm)
            current_id = self._resource_id(current_vm)
            if not expected_id or current_id != expected_id or current_ip != expected_ip:
                raise RuntimeError(f"VM-HA member {name} changed identity after SSH verification")

    def discover_vm_ha_members(self, spec: GatewayGroupSpec) -> dict[str, str]:
        """Classify existing members with read-only Compute calls only."""

        if spec.vm_ha is None:
            return {}
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("VM-HA member discovery requires the Nebius SDK")
        return {
            name: public_ip
            for name, (_, public_ip) in self._discover_vm_ha_members(client, spec).items()
        }

    def _classify_former_vm_ha_evidence(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        legacy_identities: t.Mapping[str, LegacyVMHAIdentity | None] | None = None,
        lifecycle_state: VMHALifecycleState | None = None,
    ) -> tuple[dict[str, tuple[t.Any, str]], FormerVMHAEvidence] | None:
        return classify_former_vm_ha_evidence(
            project_id=self.project_id,
            client=client,
            gateway_name=spec.name,
            resource_id=self._resource_id,
            instance_reader=self._get_vm_by_name_for_vm_ha_preflight,
            public_ip_reader=self._vm_public_ip_from_object,
            legacy_identities=legacy_identities,
            lifecycle_state=lifecycle_state,
        )

    def discover_former_vm_ha_candidate_members(
        self,
        spec: GatewayGroupSpec,
        *,
        lifecycle_state: VMHALifecycleState | None = None,
        allow_unmarked_runtime_probe: bool = False,
    ) -> dict[str, str]:
        """Find exact two-member runtime-probe candidates without VPC reads."""

        self._former_vm_ha_candidate_provenance = None
        if spec.vm_ha is not None or not self.project_id:
            return {}
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("Former VM-HA discovery requires the Nebius SDK")
        members = self._discover_vm_ha_members(client, replace(spec, instance_count=2))
        if lifecycle_state is not None:
            expected = lifecycle_member_map(lifecycle_state)
            if set(members) != set(expected):
                raise RuntimeError("Former VM-HA lifecycle member set changed")
            for name, (instance, public_ip) in members.items():
                member = expected[name]
                interfaces = list(
                    getattr(getattr(instance, "spec", None), "network_interfaces", []) or []
                )
                if (
                    self._resource_id(instance) != member.compute_id
                    or len(interfaces) != 1
                    or str(getattr(interfaces[0], "name", "") or "")
                    != member.network_interface_name
                    or public_ip != member.public_ip
                ):
                    raise RuntimeError("Former VM-HA lifecycle member identity changed")
            self._former_vm_ha_candidate_provenance = FormerVMHAProvenance.LIFECYCLE_STATE
            self._former_vm_ha_lifecycle = lifecycle_state
            return {name: public_ip for name, (_, public_ip) in members.items()}
        provenances = {
            compute_provisioning_provenance(instance) for instance, _ in members.values()
        }
        provenances.discard(None)
        if not provenances:
            if allow_unmarked_runtime_probe and len(members) == 2:
                return {name: public_ip for name, (_, public_ip) in members.items()}
            return {}
        if (
            len(members) != 2
            or len(provenances) != 1
            or any(
                compute_provisioning_provenance(instance) is None
                for instance, _ in members.values()
            )
        ):
            raise RuntimeError("Former VM-HA Compute provenance is incomplete or mixed")
        self._former_vm_ha_candidate_provenance = provenances.pop()
        return {name: public_ip for name, (_, public_ip) in members.items()}

    @property
    def former_vm_ha_candidate_provenance(self) -> FormerVMHAProvenance | None:
        return self._former_vm_ha_candidate_provenance

    def discover_former_vm_ha_members(
        self,
        spec: GatewayGroupSpec,
        *,
        legacy_identities: t.Mapping[str, LegacyVMHAIdentity | None] | None = None,
        lifecycle_state: VMHALifecycleState | None = None,
    ) -> dict[str, str]:
        """Discover both former HA members independently of the new member count."""

        self._former_vm_ha_snapshot = None
        self._former_vm_ha_evidence = None
        if spec.vm_ha is not None or not self.project_id:
            return {}
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("Former VM-HA discovery requires the Nebius SDK")
        classified = self._classify_former_vm_ha_evidence(
            client,
            spec,
            legacy_identities=legacy_identities,
            lifecycle_state=lifecycle_state,
        )
        if classified is None:
            if self._former_vm_ha_candidate_provenance is not None:
                raise RuntimeError("Former VM-HA candidate evidence is not authoritative")
            return {}
        members, evidence = classified
        if (
            self._former_vm_ha_candidate_provenance is not None
            and evidence.provenance is not self._former_vm_ha_candidate_provenance
        ):
            raise RuntimeError("Former VM-HA provenance changed during classification")
        if self._former_vm_ha_lifecycle != lifecycle_state:
            raise RuntimeError("Former VM-HA lifecycle state changed during classification")
        self._former_vm_ha_snapshot = members
        self._former_vm_ha_evidence = evidence
        return {name: public_ip for name, (_, public_ip) in members.items()}

    def verify_former_vm_ha_member_snapshot(
        self,
        spec: GatewayGroupSpec,
        expected: t.Mapping[str, str],
        *,
        legacy_identities: t.Mapping[str, LegacyVMHAIdentity | None] | None = None,
        lifecycle_state: VMHALifecycleState | None = None,
    ) -> None:
        """Re-read every former Compute identity immediately before teardown."""

        snapshot = self._former_vm_ha_snapshot
        evidence = self._former_vm_ha_evidence
        if (
            snapshot is None
            or {name: public_ip for name, (_, public_ip) in snapshot.items()} != dict(expected)
            or evidence is None
        ):
            raise RuntimeError("Former VM-HA discovery snapshot is unavailable or stale")
        client = self._build_sdk_client(spec.region)
        if client is None:
            raise RuntimeError("Former VM-HA identity recheck requires the Nebius SDK")
        if evidence.provenance is FormerVMHAProvenance.LEGACY_RUNTIME and legacy_identities is None:
            raise RuntimeError(
                "Former VM-HA legacy runtime evidence must be re-read immediately before teardown"
            )
        if evidence.provenance is FormerVMHAProvenance.LIFECYCLE_STATE and (
            lifecycle_state is None
            or self._former_vm_ha_lifecycle is None
            or not lifecycle_state.has_same_identity(self._former_vm_ha_lifecycle)
            or (
                lifecycle_state.status is not self._former_vm_ha_lifecycle.status
                and not (
                    self._former_vm_ha_lifecycle.status is VMHALifecycleStatus.ACTIVE
                    and lifecycle_state.status is VMHALifecycleStatus.REMOVAL_IN_PROGRESS
                )
            )
        ):
            raise RuntimeError("Former VM-HA lifecycle state is unavailable or stale")
        if evidence.provenance is FormerVMHAProvenance.LIFECYCLE_STATE:
            assert lifecycle_state is not None
            self._former_vm_ha_lifecycle = lifecycle_state
        current = self._classify_former_vm_ha_evidence(
            client,
            spec,
            legacy_identities=legacy_identities,
            lifecycle_state=lifecycle_state,
        )
        if current is None or current[1] != evidence:
            raise RuntimeError("Former VM-HA allocation or member evidence changed")
        if {name: public_ip for name, (_, public_ip) in current[0].items()} != dict(expected):
            raise RuntimeError("Former VM-HA member addresses changed")
        self._require_vm_ha_member_snapshot(client, replace(spec, instance_count=2), snapshot)
        final = self._classify_former_vm_ha_evidence(
            client,
            spec,
            legacy_identities=legacy_identities,
            lifecycle_state=lifecycle_state,
        )
        if final is None or final[1] != evidence:
            raise RuntimeError("Former VM-HA allocation or member evidence changed")

    def former_vm_ha_lifecycle_state(self, spec: GatewayGroupSpec) -> VMHALifecycleState:
        """Adopt the exact classified cloud snapshot into a durable selector."""

        evidence = self._former_vm_ha_evidence
        snapshot = self._former_vm_ha_snapshot
        if not self.project_id or evidence is None or snapshot is None:
            raise RuntimeError("Former VM-HA lifecycle adoption requires classified evidence")
        members: list[VMHALifecycleMember] = []
        for index, identity in enumerate(evidence.members):
            name, compute_id, network_interface_name, node_id, role = identity
            observed = snapshot.get(name)
            if observed is None:
                raise RuntimeError("Former VM-HA lifecycle adoption snapshot is incomplete")
            members.append(
                VMHALifecycleMember(
                    instance_index=index,
                    instance_name=name,
                    node_id=node_id,
                    role=role,
                    compute_id=compute_id,
                    network_interface_name=network_interface_name,
                    public_ip=observed[1],
                )
            )
        return VMHALifecycleState(
            status=VMHALifecycleStatus.ACTIVE,
            project_id=self.project_id,
            gateway_name=spec.name,
            cluster_id=evidence.cluster_id,
            allocation_id=evidence.allocation_id,
            allocation_name=evidence.allocation_name,
            members=t.cast(tuple[VMHALifecycleMember, VMHALifecycleMember], tuple(members)),
        )

    def verify_vm_ha_existing_identities(
        self,
        existing: t.Mapping[str, str],
        *,
        policy: SSHTrustPolicy | None = None,
        username: str = "ubuntu",
    ) -> None:
        """Verify every existing member's exact pin without changing remote state."""

        selected_policy = policy or self._ssh_policy
        if selected_policy is None:
            raise RuntimeError("VM-HA existing-member verification requires an SSH policy")
        for name, public_ip in existing.items():
            ssh_base = build_openssh_base_command(
                key_path=self._management_key_path,
                connect_timeout=5,
                policy=selected_policy,
                hostname=name,
            )
            ssh_base.extend(["-o", "BatchMode=yes"])
            try:
                result = subprocess.run(
                    ssh_base + [f"{username}@{public_ip}", "true"],
                    capture_output=True,
                    timeout=10,
                )
            except Exception as error:
                raise RuntimeError(
                    f"Existing VM-HA member {name} is unreachable before cloud mutation"
                ) from error
            if result.returncode == 0:
                continue
            detail = result.stderr.decode(errors="replace").lower()
            if (
                "host key verification failed" in detail
                or "remote host identification has changed" in detail
            ):
                raise RuntimeError(
                    f"SSH host identity verification failed for existing VM-HA member {name}"
                )
            raise RuntimeError(f"Existing VM-HA member {name} is unreachable before cloud mutation")

    @staticmethod
    def _render_vm_ha_enrollment_cloud_init(
        cloud_init: str,
        identity: t.Any,
        provisioning_marker: str,
    ) -> str:
        write_files_anchor = "write_files:\n"
        sshd_anchor = "            Port 22\n"
        cloud_config_anchor = "#cloud-config\n"
        if cloud_init.count(write_files_anchor) != 1:
            raise RuntimeError("VM-HA cloud-init must contain exactly one write_files anchor")
        if cloud_init.count(sshd_anchor) != 1:
            raise RuntimeError("VM-HA cloud-init must contain exactly one sshd HostKey anchor")
        if cloud_init.count(cloud_config_anchor) != 1 or "\n" in provisioning_marker:
            raise RuntimeError("VM-HA cloud-init must contain one safe provisioning marker anchor")
        return (
            cloud_init.replace(
                cloud_config_anchor,
                cloud_config_anchor + PROVISIONING_MARKER_PREFIX + provisioning_marker + "\n",
                1,
            )
            .replace(
                write_files_anchor,
                write_files_anchor + identity.cloud_init_entries(),
                1,
            )
            .replace(
                sshd_anchor,
                sshd_anchor + "            HostKey /etc/ssh/ssh_host_vpngw_key\n",
                1,
            )
        )

    def _prepare_vm_ha_enrollment_cloud_inits(
        self,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
        existing_names: set[str],
        recreate: bool,
    ) -> dict[str, str]:
        if self._ssh_policy is None:
            raise RuntimeError("VM-HA provisioning requires a validated immutable SSH policy")
        base = self._build_cloud_init(
            ssh_key=spec.vm_spec.get("ssh_public_key"),
            local_prefixes=local_prefixes,
        )
        rendered: dict[str, str] = {}
        for index in range(spec.instance_count):
            name = f"{spec.name}-{index}"
            if name in existing_names and not recreate:
                continue
            rendered[name] = self._render_vm_ha_enrollment_cloud_init(
                base,
                self._ssh_policy.identity_for(name),
                render_provisioning_marker(spec, index),
            )
        return rendered

    def _collect_preserved_allocations(self, existing: list[t.Any]) -> dict[str, list[str]]:
        preserved_allocations: dict[str, list[str]] = {}
        if not existing:
            return preserved_allocations

        print(
            f"[VMManager] Querying allocations from {len(existing)} existing VMs for preservation..."
        )
        for inst in existing:
            vm_name = getattr(getattr(inst, "metadata", None), "name", None) or getattr(
                inst,
                "name",
                None,
            )
            if not vm_name:
                continue
            allocs = self.get_vm_allocations(vm_name)
            if not allocs:
                continue
            alloc_ids = [alloc_id for _, alloc_id in sorted(allocs, key=lambda x: x[0])]
            preserved_allocations[vm_name] = alloc_ids
            print(f"[VMManager] Preserved allocations for {vm_name}: {alloc_ids}")
        return preserved_allocations

    def _delete_existing_instances_and_boot_disks(
        self,
        client: t.Any,
        existing: list[t.Any],
        spec: GatewayGroupSpec,
    ) -> None:
        print(
            f"[VMManager] Recreate requested; deleting {len(existing)} instances and boot disks (preserving subnet and allocations)"
        )
        isc = None
        dsc = None
        try:
            from nebius.api.nebius.compute.v1 import (
                DiskServiceClient,
                InstanceServiceClient,
            )  # type: ignore

            isc = InstanceServiceClient(client)
            dsc = DiskServiceClient(client)
        except Exception as e:
            print(f"[VMManager] Cannot get service clients for deletion: {e}")

        if isc is None:
            print("[VMManager] ERROR: Cannot delete VMs - InstanceServiceClient not available")
            raise RuntimeError("Cannot proceed with --recreate-gw: VM deletion failed")

        for inst in existing:
            inst_id = getattr(inst, "id", None)
            if not inst_id:
                metadata = getattr(inst, "metadata", None)
                if metadata:
                    inst_id = getattr(metadata, "id", None)

            inst_name = getattr(getattr(inst, "metadata", None), "name", None) or getattr(
                inst,
                "name",
                "unknown",
            )
            if not inst_id:
                continue

            try:
                print(f"[VMManager] Deleting VM {inst_name} (id={inst_id})...")
                from nebius.api.nebius.compute.v1 import DeleteInstanceRequest  # type: ignore

                delete_req = DeleteInstanceRequest(id=inst_id)
                op = isc.delete(delete_req)
                if hasattr(op, "wait"):
                    op.wait()
                    print(f"[VMManager] VM {inst_name} deletion initiated")
                else:
                    time.sleep(5)
            except Exception as e:
                print(f"[VMManager] Failed to delete VM {inst_name}: {e}")

        if existing:
            print("[VMManager] Waiting for VM deletions to complete...")
            time.sleep(15)

        if dsc is None:
            return

        from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore

        for i in range(spec.instance_count):
            inst_name = f"{spec.name}-{i}"
            boot_disk_name = f"{inst_name}-boot"
            try:
                if self.project_id and hasattr(dsc, "get_by_name"):
                    disk_obj = dsc.get_by_name(
                        GetByNameRequest(parent_id=self.project_id, name=boot_disk_name)
                    ).wait()
                    disk_id = getattr(disk_obj, "id", None) or getattr(
                        getattr(disk_obj, "metadata", None),
                        "id",
                        None,
                    )
                    if not disk_id:
                        continue

                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            print(
                                f"[VMManager] Deleting boot disk {boot_disk_name} (id={disk_id})..."
                            )
                            from nebius.api.nebius.compute.v1 import (
                                DeleteDiskRequest,  # type: ignore
                            )

                            delete_disk_req = DeleteDiskRequest(id=disk_id)
                            disk_op = dsc.delete(delete_disk_req)
                            if hasattr(disk_op, "wait"):
                                disk_op.wait()
                                print(
                                    f"[VMManager] Boot disk {boot_disk_name} deleted successfully"
                                )
                            break
                        except Exception as disk_err:
                            if "FAILED_PRECONDITION" in str(
                                disk_err
                            ) and "read-write attachments" in str(disk_err):
                                if attempt < max_retries - 1:
                                    wait_time = 10 * (attempt + 1)
                                    print(
                                        f"[VMManager] Disk still attached, waiting {wait_time}s before retry {attempt + 2}/{max_retries}..."
                                    )
                                    time.sleep(wait_time)
                                else:
                                    print(
                                        f"[VMManager] Could not delete boot disk {boot_disk_name} after {max_retries} attempts: {disk_err}"
                                    )
                            else:
                                print(
                                    f"[VMManager] Could not delete boot disk {boot_disk_name}: {disk_err}"
                                )
                                break
            except Exception as e:
                print(
                    f"[VMManager] Could not find or delete boot disk {boot_disk_name} (non-fatal): {e}"
                )

        if existing:
            print(
                "[VMManager] Waiting for allocations to fully detach and disk deletions to complete..."
            )
            time.sleep(15)

    def _instance_exists(self, client: t.Any, instance_api: t.Any, inst_name: str) -> bool:
        try:
            vm_obj = self._get_vm_by_name(client, inst_name)
            if vm_obj is not None:
                return True
            if instance_api is not None and hasattr(instance_api, "get_by_name"):
                try:
                    inst = instance_api.get_by_name(name=inst_name, project_id=self.project_id)
                except TypeError:
                    inst = instance_api.get_by_name(name=inst_name)
                return inst is not None
        except Exception:
            return False
        return False

    @staticmethod
    def _normalize_disk_type(disk_type: t.Any) -> str:
        try:
            dt = str(disk_type).upper()
            if dt in {
                "NETWORK_SSD",
                "NETWORK_HDD",
                "NETWORK_SSD_NON_REPLICATED",
                "NETWORK_SSD_IO_M3",
            }:
                return dt
            if dt in {"SSD", "NVME"}:
                return "NETWORK_SSD"
            if dt == "HDD":
                return "NETWORK_HDD"
        except Exception:
            pass
        return "NETWORK_SSD"

    def _build_vm_provisioning_config(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        local_prefixes: list[str] | None,
    ) -> VMProvisioningConfig:
        num_nics = int(spec.vm_spec.get("num_nics", 1))
        if num_nics > 1:
            print(
                f"[VMManager] WARNING: num_nics={num_nics} but current platform only supports 1 NIC. Using num_nics=1."
            )
            num_nics = 1

        subnet_id = self._ensure_vpngw_subnet(client, spec)
        self._ensure_vpngw_route_table(client, subnet_id)

        ssh_key = spec.vm_spec.get("ssh_public_key")
        cloud_init = self._build_cloud_init(ssh_key=ssh_key, local_prefixes=local_prefixes)

        return VMProvisioningConfig(
            subnet_id=subnet_id,
            num_nics=num_nics,
            platform=spec.vm_spec.get("platform") or "cpu-d3",
            preset=spec.vm_spec.get("preset"),
            boot_image=spec.vm_spec.get("disk_boot_image")
            or spec.vm_spec.get("image_family")
            or "ubuntu24.04-driverless",
            disk_gb=spec.vm_spec.get("disk_gb", 200),
            disk_type=self._normalize_disk_type(spec.vm_spec.get("disk_type", "network_ssd")),
            disk_block_bytes=spec.vm_spec.get("disk_block_bytes", 4096),
            cloud_init=cloud_init,
        )

    @staticmethod
    def _resource_id(resource: t.Any) -> str | None:
        resource_id = getattr(resource, "id", None) or getattr(
            getattr(resource, "metadata", None),
            "id",
            None,
        )
        return str(resource_id) if resource_id else None

    @staticmethod
    def _sync_operation(op: t.Any) -> None:
        try:
            if hasattr(op, "sync_wait"):
                op.sync_wait()
                return
            if hasattr(op, "wait"):
                op.wait()
        except Exception:
            pass

    @staticmethod
    def _sync_vm_ha_operation(operation: t.Any) -> None:
        """Synchronize an HA mutation without swallowing SDK ambiguity."""
        if hasattr(operation, "sync_wait"):
            operation.sync_wait()
            return
        if hasattr(operation, "wait"):
            operation.wait()
            return
        raise RuntimeError("VM-HA allocation create returned no waitable operation")

    @staticmethod
    def _resource_state(resource: t.Any) -> str | None:
        status = getattr(resource, "status", None)
        state = getattr(status, "status", None) if status else None
        return str(state) if state else None

    def _get_disk_by_name_from_client(self, disk_client: t.Any, disk_name: str) -> t.Any | None:
        if not self.project_id or not hasattr(disk_client, "get_by_name"):
            return None
        try:
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore

            return disk_client.get_by_name(
                GetByNameRequest(parent_id=self.project_id, name=disk_name)
            ).wait()
        except Exception:
            return None

    def _lookup_boot_disk_id(self, disk_client: t.Any, disk_name: str) -> str | None:
        disk_obj = self._get_disk_by_name_from_client(disk_client, disk_name)
        disk_id = self._resource_id(disk_obj)
        if disk_id:
            return disk_id
        if not self.project_id:
            return None
        try:
            from nebius.api.nebius.compute.v1 import ListDisksRequest  # type: ignore

            disks = disk_client.list(ListDisksRequest(parent_id=self.project_id)).wait()
            for disk in getattr(disks, "items", []) or []:
                if getattr(getattr(disk, "metadata", None), "name", None) != disk_name:
                    continue
                return self._resource_id(disk)
        except Exception:
            return None
        return None

    def _resolve_existing_boot_disk_id(self, disk_client: t.Any, disk_name: str) -> str | None:
        disk_obj = self._get_disk_by_name_from_client(disk_client, disk_name)
        disk_id = self._resource_id(disk_obj)
        if not disk_id:
            return None

        disk_state = self._resource_state(disk_obj)
        if disk_state and "DELET" in disk_state.upper():
            print(
                f"[VMManager] Disk {disk_name} is in state {disk_state}, waiting for deletion to complete..."
            )
            max_wait = 60
            wait_interval = 5
            for wait_attempt in range(max_wait // wait_interval):
                time.sleep(wait_interval)
                disk_obj = self._get_disk_by_name_from_client(disk_client, disk_name)
                if disk_obj is None:
                    print(
                        f"[VMManager] Disk deletion complete after {(wait_attempt + 1) * wait_interval}s"
                    )
                    return None
                disk_state = self._resource_state(disk_obj)
                if disk_state and "DELET" in disk_state.upper():
                    print(f"[VMManager] Still deleting... ({(wait_attempt + 1) * wait_interval}s)")
                    continue
                print(f"[VMManager] Disk state changed to {disk_state}")
                return self._resource_id(disk_obj)

            print("[VMManager] Timeout waiting for disk deletion, will retry creation")
            return None

        print(f"[VMManager] Found existing disk {disk_name} id={disk_id}")
        return disk_id

    def _resolve_boot_image_id(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
        provisioning: VMProvisioningConfig,
    ) -> str | None:
        image_id = spec.vm_spec.get("image_id") or None
        if image_id or not provisioning.boot_image:
            return image_id

        try:
            from nebius.api.nebius.compute.v1 import (  # type: ignore
                GetImageLatestByFamilyRequest,
                ImageServiceClient,
            )

            image_client = ImageServiceClient(client)  # type: ignore
            routing_code = None
            try:
                if self.project_id and self.project_id.startswith("project-"):
                    routing_code = (self.project_id.split("-")[1] or "")[:3]
            except Exception:
                routing_code = None

            parents_to_try: list[str | None] = []
            if routing_code:
                parents_to_try.append(f"project-{routing_code}public-images")
            parents_to_try.extend([None, "project-u00public-images"])

            for parent in parents_to_try:
                try:
                    request = GetImageLatestByFamilyRequest(
                        image_family=provisioning.boot_image,
                        **({"parent_id": parent} if parent else {}),
                    )
                    image = image_client.get_latest_by_family(request).wait()
                    candidate_id = self._resource_id(image)
                    if not candidate_id:
                        continue
                    if routing_code and not candidate_id.startswith(f"computeimage-{routing_code}"):
                        continue
                    return candidate_id
                except Exception:
                    continue
        except Exception:
            return None

        return None

    def _build_boot_disk_create_request(
        self,
        disk_name: str,
        provisioning: VMProvisioningConfig,
        image_id: str,
    ) -> t.Any:
        from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
        from nebius.api.nebius.compute.v1 import CreateDiskRequest, DiskSpec  # type: ignore

        return CreateDiskRequest(
            metadata=ResourceMetadata(
                name=disk_name,
                parent_id=self.project_id or "",
            ),
            spec=DiskSpec(
                block_size_bytes=provisioning.disk_block_bytes,
                size_gibibytes=provisioning.disk_gb,
                type=t.cast(t.Any, provisioning.disk_type),
                source_image_id=image_id,
            ),
        )

    def _submit_boot_disk_create(
        self,
        disk_client: t.Any,
        create_request: t.Any,
        disk_name: str,
    ) -> str | None:
        operation = disk_client.create(create_request).wait()
        self._sync_operation(operation)
        resource = getattr(getattr(operation, "result", None), "resource", None)
        return self._resource_id(resource) or self._lookup_boot_disk_id(disk_client, disk_name)

    def _wait_for_boot_disk_deletion(self, disk_client: t.Any, disk_name: str) -> bool:
        if not self.project_id or not hasattr(disk_client, "get_by_name"):
            return False

        max_wait = 60
        wait_interval = 5
        for wait_attempt in range(max_wait // wait_interval):
            time.sleep(wait_interval)
            if self._get_disk_by_name_from_client(disk_client, disk_name) is not None:
                print(
                    f"[VMManager] Disk still exists, waiting... ({(wait_attempt + 1) * wait_interval}s)"
                )
                continue
            print("[VMManager] Disk deletion complete, retrying creation...")
            return True

        print("[VMManager] Timeout waiting for disk deletion to complete")
        return False

    def _ensure_boot_disk_legacy(
        self,
        disk_api: t.Any,
        spec: GatewayGroupSpec,
        disk_name: str,
        provisioning: VMProvisioningConfig,
    ) -> str | None:
        if disk_api is None:
            return None

        boot_disk_id = None
        try:
            if hasattr(disk_api, "get_by_name"):
                disk_obj = disk_api.get_by_name(name=disk_name)
                boot_disk_id = self._resource_id(disk_obj)
        except Exception:
            boot_disk_id = None

        if boot_disk_id or not hasattr(disk_api, "create"):
            return boot_disk_id

        disk_request = {
            "name": disk_name,
            "size_gibibytes": provisioning.disk_gb,
            "type": provisioning.disk_type,
            "source_image_family": provisioning.boot_image,
            "block_size_bytes": provisioning.disk_block_bytes,
            **({"project_id": self.project_id} if self.project_id else {}),
            **({"zone": self.zone or spec.region} if (self.zone or spec.region) else {}),
        }
        try:
            print(f"[VMManager] Creating boot disk {disk_name} ...")
            try:
                disk_obj = disk_api.create(**disk_request)  # type: ignore
            except TypeError:
                disk_obj = disk_api.create(disk_request)
            return self._resource_id(disk_obj)
        except Exception as e:
            print(f"[VMManager] boot disk create failed: {e}. Attempted: {disk_request}")
            return None

    def _ensure_boot_disk(
        self,
        client: t.Any,
        disk_api: t.Any,
        spec: GatewayGroupSpec,
        inst_name: str,
        provisioning: VMProvisioningConfig,
        recreate: bool,
    ) -> tuple[str, str | None]:
        boot_disk_name = f"{inst_name}-boot"
        boot_disk_id = None

        try:
            from nebius.api.nebius.compute.v1 import DiskServiceClient  # type: ignore

            disk_client = DiskServiceClient(client)  # type: ignore
            if not recreate:
                boot_disk_id = self._resolve_existing_boot_disk_id(disk_client, boot_disk_name)

            if not boot_disk_id:
                print(
                    f"[VMManager] Creating boot disk {boot_disk_name} (project_id={self.project_id}) ..."
                )
                image_id = self._resolve_boot_image_id(client, spec, provisioning)
                if not image_id:
                    raise RuntimeError(
                        f"[VMManager] Unable to resolve image id for family '{provisioning.boot_image}'. "
                        "Ensure the image family exists or provide vm_spec.image_id."
                    )

                create_request = self._build_boot_disk_create_request(
                    boot_disk_name,
                    provisioning,
                    image_id,
                )
                try:
                    boot_disk_id = self._submit_boot_disk_create(
                        disk_client,
                        create_request,
                        boot_disk_name,
                    )
                except Exception as e:
                    message = str(e)
                    print(f"[VMManager] Disk create exception: {message}")
                    already_exists = "ALREADY_EXISTS" in message or (
                        f'disk with name "{boot_disk_name}" already exists' in message
                    )
                    if already_exists and recreate:
                        print(
                            f"[VMManager] Disk {boot_disk_name} still exists (likely deleting), waiting for deletion to complete..."
                        )
                        if self._wait_for_boot_disk_deletion(disk_client, boot_disk_name):
                            try:
                                boot_disk_id = self._submit_boot_disk_create(
                                    disk_client,
                                    create_request,
                                    boot_disk_name,
                                )
                            except Exception as retry_err:
                                print(f"[VMManager] Disk creation retry failed: {retry_err}")
                    elif already_exists and not recreate:
                        print("[VMManager] Disk already exists, refetching ID...")
                        boot_disk_id = self._lookup_boot_disk_id(disk_client, boot_disk_name)
                    else:
                        print(f"[VMManager] boot disk create failed: {e}")
        except Exception:
            boot_disk_id = self._ensure_boot_disk_legacy(
                disk_api,
                spec,
                boot_disk_name,
                provisioning,
            )

        return boot_disk_name, boot_disk_id

    @staticmethod
    def _desired_public_ips(
        spec: GatewayGroupSpec,
        instance_index: int,
        num_nics: int,
    ) -> list[str]:
        if not spec.external_ips or instance_index >= len(spec.external_ips):
            return []
        instance_ips = spec.external_ips[instance_index] or []
        if not isinstance(instance_ips, list):
            return []
        return [ip for ip in instance_ips[:num_nics] if ip]

    def _get_allocation_by_name(self, alloc_client: t.Any, alloc_name: str) -> t.Any | None:
        if alloc_client is None:
            return None
        try:
            from nebius.api.nebius.vpc.v1 import GetAllocationByNameRequest  # type: ignore

            return alloc_client.get_by_name(
                GetAllocationByNameRequest(
                    parent_id=self.project_id or "",
                    name=alloc_name,
                )
            ).wait()
        except Exception:
            return None

    def _get_allocation_by_id(self, alloc_client: t.Any, allocation_id: str) -> t.Any | None:
        if alloc_client is None:
            return None
        try:
            from nebius.api.nebius.vpc.v1 import GetAllocationRequest  # type: ignore

            return alloc_client.get(GetAllocationRequest(id=allocation_id)).wait()
        except Exception:
            return None

    @staticmethod
    def _allocation_name(alloc_obj: t.Any, fallback: str | None = None) -> str:
        name = getattr(getattr(alloc_obj, "metadata", None), "name", None)
        if name:
            return str(name)
        alloc_id = getattr(alloc_obj, "id", None) or getattr(
            getattr(alloc_obj, "metadata", None),
            "id",
            None,
        )
        if alloc_id:
            return str(alloc_id)
        return fallback or "allocation"

    @staticmethod
    def _allocation_is_transitional(state: str | None) -> bool:
        return bool(
            state and any(token in state.lower() for token in ("delet", "releas", "pending"))
        )

    def _hydrate_allocation(self, alloc_client: t.Any, alloc_obj: t.Any | None) -> t.Any | None:
        if alloc_client is None or alloc_obj is None:
            return alloc_obj
        alloc_id = self._resource_id(alloc_obj)
        if not alloc_id:
            return alloc_obj
        return self._get_allocation_by_id(alloc_client, alloc_id) or alloc_obj

    def _resolve_known_allocation_ip(
        self, alloc_client: t.Any, alloc_obj: t.Any | None
    ) -> str | None:
        if alloc_obj is None:
            return None
        alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
        alloc_ip = self._allocation_ip_from_obj(alloc_obj)
        if alloc_ip:
            return alloc_ip
        alloc_id = self._resource_id(alloc_obj)
        if alloc_id:
            return self._normalize_ip_value(self.get_allocation_ip(alloc_id))
        return None

    def _validate_requested_public_allocation(
        self,
        alloc_client: t.Any,
        alloc_obj: t.Any,
        *,
        desired_ip: str,
        alloc_name: str,
        require_resolved_ip: bool,
    ) -> t.Any:
        alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
        resolved_ip = self._resolve_known_allocation_ip(alloc_client, alloc_obj)
        actual_name = self._allocation_name(alloc_obj, alloc_name)

        if require_resolved_ip and not resolved_ip:
            raise RuntimeError(
                f"Public IP allocation {actual_name} exists, but its IP could not be resolved to "
                f"confirm the requested external_ips value {desired_ip}."
            )
        if resolved_ip and resolved_ip != desired_ip:
            raise RuntimeError(
                f"Public IP allocation {actual_name} has IP {resolved_ip}, but "
                f"external_ips requested {desired_ip}."
            )
        if self._allocation_is_attached(alloc_obj):
            raise RuntimeError(
                f"Requested public IP allocation {desired_ip} ({actual_name}) is already attached "
                "to another resource. Detach it before using it in gateway_group.external_ips."
            )
        return alloc_obj

    def _require_public_allocation_in_gateway_subnet(
        self,
        alloc_client: t.Any,
        alloc_obj: t.Any,
        target_subnet_id: str | None,
        desired_ip: str | None,
    ) -> t.Any:
        if not alloc_obj or not target_subnet_id:
            return alloc_obj

        alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
        alloc_spec = getattr(alloc_obj, "spec", None)
        ipv4_public = getattr(alloc_spec, "ipv4_public", None) if alloc_spec else None
        if not ipv4_public:
            return alloc_obj

        current_subnet_id = getattr(ipv4_public, "subnet_id", None)
        if not current_subnet_id or current_subnet_id == target_subnet_id:
            if current_subnet_id == target_subnet_id:
                print(f"[VMManager] Allocation {desired_ip} already in the gateway subnet")
            return alloc_obj

        alloc_meta = getattr(alloc_obj, "metadata", None)
        alloc_id = getattr(alloc_obj, "id", None) or getattr(alloc_meta, "id", None)
        alloc_name = getattr(alloc_meta, "name", None) if alloc_meta else None
        raise RuntimeError(
            f"Public IP allocation {alloc_name or alloc_id} is bound to subnet {current_subnet_id} "
            f"and cannot be moved to gateway subnet {target_subnet_id}. "
            "Nebius marks public allocation subnet binding as immutable. "
            "Options: (1) deploy the gateway in the original subnet/network so the IP matches, "
            "or (2) remove the IP from external_ips to allow a new allocation, "
            "or (3) release the old allocation and re-request the same IP in the gateway subnet "
            "(best effort only)."
        )

    def _find_requested_public_allocation(
        self,
        alloc_client: t.Any,
        alloc_api: t.Any,
        alloc_name: str,
        desired_ip: str,
        allocations_by_ip: dict[str, t.Any] | None = None,
    ) -> tuple[t.Any | None, dict[str, t.Any]]:
        normalized_ip = self._normalize_ip_value(desired_ip)
        if not normalized_ip:
            return None, allocations_by_ip or {}

        mapping = allocations_by_ip or {}
        alloc_obj = mapping.get(normalized_ip)
        if alloc_obj is None and alloc_client is not None:
            mapping = self._list_allocations_by_ip(alloc_client)
            alloc_obj = mapping.get(normalized_ip)

        if alloc_obj is None and alloc_api is not None:
            get_by_addr = getattr(alloc_api, "get_by_address", None)
            if get_by_addr:
                try:
                    alloc_obj = get_by_addr(address=normalized_ip, project_id=self.project_id)
                except Exception:
                    alloc_obj = None

        if alloc_obj is not None:
            alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
            state = self._allocation_state(alloc_obj)
            if alloc_client is not None and self._allocation_is_transitional(state):
                mapping = self._wait_for_allocation_release(alloc_client, normalized_ip)
                alloc_obj = mapping.get(normalized_ip)
                alloc_obj = self._hydrate_allocation(alloc_client, alloc_obj)
            if alloc_obj is not None:
                alloc_obj = self._validate_requested_public_allocation(
                    alloc_client,
                    alloc_obj,
                    desired_ip=normalized_ip,
                    alloc_name=alloc_name,
                    require_resolved_ip=False,
                )
                return alloc_obj, mapping

        by_name = self._get_allocation_by_name(alloc_client, alloc_name)
        if by_name is None:
            return None, mapping

        by_name = self._validate_requested_public_allocation(
            alloc_client,
            by_name,
            desired_ip=normalized_ip,
            alloc_name=alloc_name,
            require_resolved_ip=True,
        )
        return by_name, mapping

    def _create_public_allocation_via_client(
        self,
        alloc_client: t.Any,
        alloc_name: str,
        subnet_id: str,
        desired_ip: str | None,
    ) -> t.Any | None:
        try:
            from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
            from nebius.api.nebius.vpc.v1 import (  # type: ignore
                AllocationSpec,
                CreateAllocationRequest,
                IPv4PublicAllocationSpec,
            )

            request = CreateAllocationRequest(
                metadata=ResourceMetadata(
                    name=alloc_name,
                    parent_id=self.project_id or "",
                ),
                spec=AllocationSpec(
                    ipv4_public=IPv4PublicAllocationSpec(
                        subnet_id=subnet_id,
                        cidr="/32" if not desired_ip else desired_ip,
                    )
                ),
            )
            operation = alloc_client.create(request).wait()
            self._sync_operation(operation)
            resource = getattr(getattr(operation, "result", None), "resource", None)
            return (
                resource
                if resource is not None
                else self._get_allocation_by_name(
                    alloc_client,
                    alloc_name,
                )
            )
        except Exception as e:
            print(f"[VMManager] allocation create via client failed: {e}")
            return self._get_allocation_by_name(alloc_client, alloc_name)

    def _create_public_allocation_via_api(
        self,
        alloc_api: t.Any,
        alloc_name: str,
        subnet_id: str,
    ) -> t.Any | None:
        create_args = {
            "name": alloc_name,
            "ipv_4_public_subnet_id": subnet_id,
            **({"project_id": self.project_id} if self.project_id else {}),
        }
        try:
            return alloc_api.create(**create_args)  # type: ignore
        except TypeError:
            return alloc_api.create(create_args)

    def _ensure_public_allocation(
        self,
        alloc_api: t.Any,
        alloc_client: t.Any,
        inst_name: str,
        nic_name: str,
        subnet_id: str | None,
        desired_ip: str | None,
        preserved_alloc_id: str | None,
    ) -> tuple[str, t.Any | None]:
        alloc_name = f"{inst_name}-{nic_name}-ip"
        alloc_obj = None

        if desired_ip:
            alloc_obj, _ = self._find_requested_public_allocation(
                alloc_client,
                alloc_api,
                alloc_name,
                desired_ip,
            )
            if alloc_obj is not None:
                print(f"[VMManager] Found existing allocation with IP {desired_ip}")
                alloc_obj = self._require_public_allocation_in_gateway_subnet(
                    alloc_client,
                    alloc_obj,
                    subnet_id,
                    desired_ip,
                )

        if alloc_obj is None and not desired_ip and preserved_alloc_id:
            try:
                alloc_obj = self._get_allocation_by_id(alloc_client, preserved_alloc_id)
                if alloc_obj:
                    preserved_ip = self.get_allocation_ip(preserved_alloc_id)
                    print(
                        f"[VMManager] Reusing preserved allocation {preserved_alloc_id} ({preserved_ip}) for {inst_name} {nic_name}"
                    )
                    alloc_obj = self._require_public_allocation_in_gateway_subnet(
                        alloc_client,
                        alloc_obj,
                        subnet_id,
                        preserved_ip,
                    )
            except Exception as e:
                print(
                    f"[VMManager] Could not retrieve preserved allocation {preserved_alloc_id}: {e}"
                )
                alloc_obj = None

        if alloc_obj is None:
            alloc_obj = self._get_allocation_by_name(alloc_client, alloc_name)
            if alloc_obj:
                print(f"[VMManager] Found existing allocation by name: {alloc_name}")
                by_name_ip = self._resolve_known_allocation_ip(alloc_client, alloc_obj)
                alloc_obj = self._require_public_allocation_in_gateway_subnet(
                    alloc_client,
                    alloc_obj,
                    subnet_id,
                    by_name_ip,
                )

        if alloc_obj is not None:
            return alloc_name, alloc_obj

        if not subnet_id:
            raise RuntimeError(
                "[VMManager] Cannot create public IP allocation: subnet_id is not set. "
                "Resolve subnet creation first or provide a valid network_id."
            )

        if desired_ip:
            print(
                f"[VMManager] Creating public IP allocation {alloc_name} for {nic_name} in the gateway subnet requesting IP {desired_ip} ..."
            )
        else:
            print(
                f"[VMManager] Creating public IP allocation {alloc_name} for {nic_name} in the gateway subnet ..."
            )

        try:
            if alloc_client is not None:
                created_alloc = self._create_public_allocation_via_client(
                    alloc_client,
                    alloc_name,
                    subnet_id,
                    desired_ip,
                )
                if desired_ip and created_alloc is not None:
                    created_alloc = self._validate_requested_public_allocation(
                        alloc_client,
                        created_alloc,
                        desired_ip=desired_ip,
                        alloc_name=alloc_name,
                        require_resolved_ip=False,
                    )
                return alloc_name, created_alloc
            if alloc_api is not None:
                created_alloc = self._create_public_allocation_via_api(
                    alloc_api,
                    alloc_name,
                    subnet_id,
                )
                if desired_ip and created_alloc is not None:
                    created_alloc = self._validate_requested_public_allocation(
                        alloc_client,
                        created_alloc,
                        desired_ip=desired_ip,
                        alloc_name=alloc_name,
                        require_resolved_ip=False,
                    )
                return alloc_name, created_alloc
        except Exception as e:
            print(f"[VMManager] allocation create failed: {e}")

        return alloc_name, None

    def _create_private_allocation_via_client(
        self,
        alloc_client: t.Any,
        alloc_name: str,
        subnet_id: str,
    ) -> t.Any | None:
        try:
            from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
            from nebius.api.nebius.vpc.v1 import (  # type: ignore
                AllocationSpec,
                CreateAllocationRequest,
                IPv4PrivateAllocationSpec,
            )

            request = CreateAllocationRequest(
                metadata=ResourceMetadata(
                    name=alloc_name,
                    parent_id=self.project_id or "",
                ),
                spec=AllocationSpec(ipv4_private=IPv4PrivateAllocationSpec(subnet_id=subnet_id)),
            )
            operation = alloc_client.create(request).wait()
            self._sync_operation(operation)
            resource = getattr(getattr(operation, "result", None), "resource", None)
            return (
                resource
                if resource is not None
                else self._get_allocation_by_name(
                    alloc_client,
                    alloc_name,
                )
            )
        except Exception as e:
            print(f"[VMManager] private allocation create via client failed: {e}")
            return self._get_allocation_by_name(alloc_client, alloc_name)

    def _find_ha_allocation_by_name(self, alloc_client: t.Any, alloc_name: str) -> t.Any | None:
        """List one named HA allocation without translating API failure to absence."""
        if alloc_client is None or not self.project_id:
            raise RuntimeError("VM-HA allocation lookup requires an SDK client and project ID")
        from nebius.api.nebius.vpc.v1 import ListAllocationsRequest  # type: ignore

        response = alloc_client.list(ListAllocationsRequest(parent_id=self.project_id)).wait()
        allocations = list(getattr(response, "items", response))
        matching = [
            allocation
            for allocation in allocations
            if getattr(getattr(allocation, "metadata", None), "name", None) == alloc_name
        ]
        if len(matching) > 1:
            raise RuntimeError(f"VM-HA allocation name {alloc_name} is ambiguous")
        if not matching:
            return None
        allocation_id = self._resource_id(matching[0])
        if not allocation_id:
            raise RuntimeError(f"VM-HA allocation {alloc_name} has no authoritative identity")
        allocation = self.get_ha_allocation(allocation_id)
        if self._resource_id(allocation) != allocation_id:
            raise RuntimeError(f"VM-HA allocation {alloc_name} changed identity during re-read")
        return allocation

    def _ensure_vm_ha_shared_allocation(
        self,
        alloc_client: t.Any,
        spec: GatewayGroupSpec,
        subnet_id: str | None,
    ) -> str:
        """Create or reuse exactly one deterministic shared private allocation."""
        vm_ha = spec.vm_ha
        if vm_ha is None:
            raise RuntimeError("VM-HA shared allocation requested without explicit VM HA")
        if alloc_client is None or not subnet_id:
            raise RuntimeError("VM-HA provisioning requires allocation SDK and resolved subnet")

        allocation_name = f"{spec.name}-{vm_ha.cluster_id}-shared-private-ip"
        allocation = self._find_ha_allocation_by_name(alloc_client, allocation_name)
        if allocation is None:
            try:
                from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
                from nebius.api.nebius.vpc.v1 import (  # type: ignore
                    AllocationSpec,
                    CreateAllocationRequest,
                    IPv4PrivateAllocationSpec,
                )

                operation = alloc_client.create(
                    CreateAllocationRequest(
                        metadata=ResourceMetadata(
                            name=allocation_name,
                            parent_id=self.project_id or "",
                        ),
                        spec=AllocationSpec(
                            ipv4_private=IPv4PrivateAllocationSpec(subnet_id=subnet_id)
                        ),
                    )
                ).wait()
                self._sync_vm_ha_operation(operation)
            except Exception as create_error:
                raise RuntimeError(
                    f"VM-HA shared allocation {allocation_name} could not be created"
                ) from create_error
            allocation = self._find_ha_allocation_by_name(alloc_client, allocation_name)
            if allocation is None:
                raise RuntimeError(
                    f"VM-HA shared allocation {allocation_name} was absent after create"
                )

        allocation_id = self._resource_id(allocation)
        if not allocation_id:
            raise RuntimeError(f"VM-HA shared allocation {allocation_name} has no ID")
        self._vm_ha_shared_allocation_id = allocation_id
        return allocation_id

    def _ensure_private_allocation(
        self,
        alloc_client: t.Any,
        inst_name: str,
        nic_name: str,
        subnet_id: str | None,
    ) -> tuple[str, t.Any | None]:
        alloc_name = f"{inst_name}-{nic_name}-private-ip"
        alloc_obj = self._get_allocation_by_name(alloc_client, alloc_name)
        if alloc_obj is not None:
            print(f"[VMManager] Found existing private allocation by name: {alloc_name}")
            return alloc_name, alloc_obj

        if alloc_client is None:
            return alloc_name, None

        if not subnet_id:
            raise RuntimeError(
                "[VMManager] Cannot create private IP allocation: subnet_id is not set. "
                "Resolve subnet creation first or provide a valid network_id."
            )

        print(
            f"[VMManager] Creating static private IP allocation {alloc_name} for {nic_name} in the gateway subnet ..."
        )
        try:
            return alloc_name, self._create_private_allocation_via_client(
                alloc_client,
                alloc_name,
                subnet_id,
            )
        except Exception as e:
            print(f"[VMManager] private allocation create failed: {e}")
            return alloc_name, None

    def _ensure_instance_allocations(
        self,
        alloc_api: t.Any,
        alloc_client: t.Any,
        spec: GatewayGroupSpec,
        inst_name: str,
        instance_index: int,
        provisioning: VMProvisioningConfig,
        preserved_alloc_ids: list[str],
        vm_ips: dict[str, str],
    ) -> list[str]:
        desired_ips = self._desired_public_ips(spec, instance_index, provisioning.num_nics)
        alloc_ids: list[str] = []
        self._private_alloc_ids[inst_name] = []

        if alloc_api is None and alloc_client is None:
            return alloc_ids

        for nic_index in range(provisioning.num_nics):
            nic_name = f"eth{nic_index}"
            desired_ip = desired_ips[nic_index] if nic_index < len(desired_ips) else None
            preserved_alloc_id = (
                preserved_alloc_ids[nic_index] if nic_index < len(preserved_alloc_ids) else None
            )
            alloc_name, alloc_obj = self._ensure_public_allocation(
                alloc_api,
                alloc_client,
                inst_name,
                nic_name,
                provisioning.subnet_id,
                desired_ip,
                preserved_alloc_id,
            )
            alloc_id = self._resource_id(alloc_obj)
            if alloc_id:
                alloc_ids.append(alloc_id)
                print(f"[VMManager] Public IP allocation {alloc_name} ready: {alloc_id}")
                if nic_index == 0:
                    alloc_ip = self.get_allocation_ip(alloc_id)
                    if alloc_ip:
                        vm_ips[inst_name] = alloc_ip

            if spec.vm_ha is not None:
                if self._vm_ha_shared_allocation_id is None:
                    raise RuntimeError("VM-HA shared allocation identity is unavailable")
                if instance_index == spec.vm_ha.active_instance_index:
                    self._private_alloc_ids[inst_name].append(self._vm_ha_shared_allocation_id)
                continue

            private_alloc_name, private_alloc_obj = self._ensure_private_allocation(
                alloc_client, inst_name, nic_name, provisioning.subnet_id
            )
            private_alloc_id = self._resource_id(private_alloc_obj)
            if private_alloc_id:
                self._private_alloc_ids[inst_name].append(private_alloc_id)
                print(
                    f"[VMManager] Private IP allocation {private_alloc_name} ready: {private_alloc_id}"
                )

        return alloc_ids

    def _resolve_boot_disk_id_before_create(
        self,
        client: t.Any,
        boot_disk_name: str,
    ) -> str | None:
        if not self.project_id:
            return None
        try:
            from nebius.api.nebius.common.v1 import GetByNameRequest  # type: ignore
            from nebius.api.nebius.compute.v1 import DiskServiceClient  # type: ignore

            dsc2 = DiskServiceClient(client)  # type: ignore
            if not hasattr(dsc2, "get_by_name"):
                return None
            disk_obj = dsc2.get_by_name(
                GetByNameRequest(parent_id=self.project_id, name=boot_disk_name)
            ).wait()
            candidate_disk_id = getattr(disk_obj, "id", None) or getattr(
                getattr(disk_obj, "metadata", None),
                "id",
                None,
            )
            if not candidate_disk_id:
                return None

            disk_status = getattr(disk_obj, "status", None)
            disk_state = getattr(disk_status, "status", None) if disk_status else None
            if disk_state and "DELET" in str(disk_state).upper():
                print(
                    f"[VMManager] Final fallback: disk {boot_disk_name} is deleting (state={disk_state}), waiting up to 120s..."
                )
                max_wait = 120
                wait_interval = 5
                for wait_attempt in range(max_wait // wait_interval):
                    time.sleep(wait_interval)
                    try:
                        disk_obj = dsc2.get_by_name(
                            GetByNameRequest(parent_id=self.project_id, name=boot_disk_name)
                        ).wait()
                        disk_status = getattr(disk_obj, "status", None)
                        disk_state = getattr(disk_status, "status", None) if disk_status else None
                        if disk_state and "DELET" in str(disk_state).upper():
                            print(
                                f"[VMManager] Still deleting... ({(wait_attempt + 1) * wait_interval}s / {max_wait}s)"
                            )
                            continue
                        print(f"[VMManager] Disk state changed to {disk_state}, can now use disk")
                        return candidate_disk_id
                    except Exception:
                        print(
                            f"[VMManager] Disk deletion complete after {(wait_attempt + 1) * wait_interval}s"
                        )
                        return None
                print(
                    f"[VMManager] Timeout waiting {max_wait}s for disk deletion. Will proceed without boot disk (may create new disk)."
                )
                return None

            print(f"[VMManager] Final fallback: resolved disk id={candidate_disk_id}")
            return candidate_disk_id
        except Exception as e:
            print(f"[VMManager] Final fallback disk lookup failed: {e}")
            return None

    def _create_instance_with_fallback(
        self,
        client: t.Any,
        instance_api: t.Any,
        inst_name: str,
        provisioning: VMProvisioningConfig,
        boot_disk_id: str | None,
        alloc_ids: list[str],
        vm_ips: dict[str, str],
    ) -> bool:
        inst_req = {
            "metadata": {
                "name": inst_name,
                **({"parent_id": self.project_id} if self.project_id else {}),
            },
            "spec": {
                "resources": {
                    "platform": provisioning.platform,
                    **({"preset": provisioning.preset} if provisioning.preset else {}),
                },
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
                "network_interfaces": [
                    {
                        "name": f"eth{nic_idx}",
                        "ip_address": (
                            {"allocation_id": self._private_alloc_ids[inst_name][nic_idx]}
                            if nic_idx < len(self._private_alloc_ids.get(inst_name, []))
                            else {}
                        ),
                        "public_ip_address": (
                            {"allocation_id": alloc_ids[nic_idx], "static": True}
                            if nic_idx < len(alloc_ids)
                            else {}
                        ),
                        "subnet_id": provisioning.subnet_id,
                    }
                    for nic_idx in range(min(provisioning.num_nics, 1))
                ],
                "cloud_init_user_data": provisioning.cloud_init,
            },
        }

        created = False
        try:
            from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
            from nebius.api.nebius.compute.v1 import (
                AttachedDiskSpec,
                CreateInstanceRequest,
                ExistingDisk,
                InstanceServiceClient,
                InstanceSpec,
                IPAddress,
                NetworkInterfaceSpec,
                PublicIPAddress,
                ResourcesSpec,
            )  # type: ignore

            isc = InstanceServiceClient(client)  # type: ignore
            print(
                f"[VMManager] Creating instance {inst_name} via InstanceServiceClient (project_id={self.project_id}) ..."
            )
            metadata = ResourceMetadata(name=inst_name, parent_id=self.project_id or "")
            if provisioning.preset:
                resources = ResourcesSpec(
                    platform=provisioning.platform,
                    preset=t.cast(t.Any, provisioning.preset),
                )
            else:
                resources = ResourcesSpec(platform=provisioning.platform)
            boot_disk_msg = None
            if boot_disk_id:
                boot_disk_msg = AttachedDiskSpec(
                    attach_mode=t.cast(t.Any, "READ_WRITE"),
                    device_id="boot",
                    existing_disk=ExistingDisk(id=boot_disk_id),
                )
            if not boot_disk_id:
                print(
                    "[VMManager] Warning: boot_disk_id missing; proceeding without boot_disk in spec."
                )

            ni_msgs = []
            for nic_idx in range(provisioning.num_nics):
                nic_name = f"eth{nic_idx}"
                pub = (
                    PublicIPAddress(allocation_id=alloc_ids[nic_idx], static=True)
                    if nic_idx < len(alloc_ids)
                    else None
                )
                priv_alloc_id = None
                priv = None
                if nic_idx < len(self._private_alloc_ids.get(inst_name, [])):
                    priv_alloc_id = self._private_alloc_ids[inst_name][nic_idx]
                    priv = IPAddress(allocation_id=priv_alloc_id)
                if priv is None:
                    priv = IPAddress()
                ni_msgs.append(
                    NetworkInterfaceSpec(
                        name=nic_name,
                        ip_address=priv,
                        public_ip_address=pub if pub is not None else PublicIPAddress(),
                        subnet_id=provisioning.subnet_id,
                    )
                )
                print(
                    f"[VMManager] NIC {nic_name} configured with public={alloc_ids[nic_idx] if nic_idx < len(alloc_ids) else 'auto'}, private={priv_alloc_id or 'auto'}"
                )

            if len(ni_msgs) > 1:
                print(
                    f"[VMManager] WARNING: {len(ni_msgs)} NICs configured but platform only supports 1. Using first NIC only."
                )
                ni_msgs = ni_msgs[:1]

            try:
                print(f"[VMManager] Using boot_disk_id={boot_disk_id}")
            except Exception:
                pass

            spec_kwargs = {
                "resources": resources,
                "network_interfaces": ni_msgs,
                "cloud_init_user_data": provisioning.cloud_init,
            }
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
                print(f"[VMManager] Instance {inst_name} created successfully via SDK")

                print(f"[VMManager] Waiting for {inst_name} to receive public IP...")
                max_ip_wait = 60
                ip_wait_interval = 5
                for attempt in range(max_ip_wait // ip_wait_interval):
                    time.sleep(ip_wait_interval)
                    vm_ip = self.get_vm_public_ip(inst_name)
                    if vm_ip:
                        print(f"[VMManager] {inst_name} ready with IP: {vm_ip}")
                        vm_ips[inst_name] = vm_ip
                        break
                    if attempt < (max_ip_wait // ip_wait_interval) - 1:
                        print(
                            f"[VMManager] Waiting for IP assignment ({(attempt + 1) * ip_wait_interval}s elapsed)..."
                        )
                else:
                    print(
                        f"[VMManager] Warning: {inst_name} did not receive public IP within {max_ip_wait}s"
                    )
            except Exception as e:
                print(f"[VMManager] InstanceServiceClient create failed: {e}")
                import traceback

                traceback.print_exc()
        except Exception as e:
            print(f"[VMManager] InstanceServiceClient initialization failed: {e}")

        if created:
            return True

        if instance_api is not None and hasattr(instance_api, "create"):
            print(f"[VMManager] Creating instance {inst_name} ...")
            try:
                try:
                    instance_api.create(**inst_req)  # type: ignore[arg-type]
                except TypeError:
                    instance_api.create(inst_req)
                return True
            except Exception as e:
                print(f"[VMManager] create failed for {inst_name}: {e}")

        print(f"[VMManager] Would create with payload: {inst_req}")
        return False

    def _provision_instance(
        self,
        client: t.Any,
        instance_api: t.Any,
        disk_api: t.Any,
        alloc_api: t.Any,
        alloc_client: t.Any,
        spec: GatewayGroupSpec,
        instance_index: int,
        recreate: bool,
        provisioning: VMProvisioningConfig | None,
        preserved_allocations: dict[str, list[str]],
        vm_ips: dict[str, str],
        expected_vm_exists: bool | None = None,
    ) -> None:
        inst_name = f"{spec.name}-{instance_index}"
        vm_exists = self._instance_exists(client, instance_api, inst_name)
        if expected_vm_exists is not None and vm_exists != expected_vm_exists:
            raise RuntimeError(
                f"VM-HA member {inst_name} changed existence after identity preflight"
            )
        if vm_exists and not recreate:
            print(f"[VMManager] VM {inst_name} already exists (recreate=False), skipping creation")
            vm_ip = self.get_vm_public_ip(inst_name)
            if vm_ip:
                vm_ips[inst_name] = vm_ip
                print(f"[VMManager] {inst_name} IP: {vm_ip}")
            return
        if vm_exists and recreate:
            print(
                f"[VMManager] WARNING: VM {inst_name} still exists after deletion (race condition?)"
            )
            return

        if provisioning is None:
            raise RuntimeError(
                f"[VMManager] Internal error: provisioning config missing for {inst_name}."
            )

        boot_disk_name, boot_disk_id = self._ensure_boot_disk(
            client,
            disk_api,
            spec,
            inst_name,
            provisioning,
            recreate,
        )
        alloc_ids = self._ensure_instance_allocations(
            alloc_api,
            alloc_client,
            spec,
            inst_name,
            instance_index,
            provisioning,
            preserved_allocations.get(inst_name, []),
            vm_ips,
        )
        if not boot_disk_id:
            boot_disk_id = self._resolve_boot_disk_id_before_create(client, boot_disk_name)
        self._create_instance_with_fallback(
            client,
            instance_api,
            inst_name,
            provisioning,
            boot_disk_id,
            alloc_ids,
            vm_ips,
        )

    def _log_scaffold_mode_instances(self, spec: GatewayGroupSpec) -> None:
        for i in range(spec.instance_count):
            inst_name = f"{spec.name}-{i}"
            inst_ips = spec.external_ips[i] if i < len(spec.external_ips) else []
            pub_ip = inst_ips[0] if inst_ips else None
            print(
                f"[VMManager] ensure instance {inst_name} pub_ip={pub_ip} platform={spec.vm_spec.get('platform')} subnet={self._gateway_subnet_name(spec)}"
            )

    def ensure_group(
        self,
        spec: GatewayGroupSpec,
        recreate: bool = False,
        local_prefixes: list[str] | None = None,
    ) -> dict[str, str]:
        """Ensure gateway VMs exist per spec.

        Pseudocode for Nebius SDK integration:
        - client = InstanceServiceClient(auth=...)
        - existing = client.list(filter=name prefix)
        - if recreate: delete existing, wait; then create all
        - else: create missing, skip existing
        - attach public IPs according to spec.external_ips
        - set network interface subnet to spec.vm_spec.vpn_subnet_id

        Args:
            spec: Gateway group specification
            recreate: Whether to recreate existing VMs
            local_prefixes: Optional list of local VPC prefixes for firewall configuration

        Returns:
            Dict mapping VM names to their public IP addresses
        """
        print(
            f"[VMManager] ensure_group name={spec.name} count={spec.instance_count} region={spec.region} recreate={recreate}"
        )
        vm_ips: dict[str, str] = {}
        try:
            print(f"[VMManager] Using project_id={self.project_id} zone={self.zone or spec.region}")
        except Exception:
            pass
        client = self._build_sdk_client(spec.region)

        try:
            if client is not None:
                instance_api, disk_api, alloc_api, alloc_client = self._resolve_client_apis(client)
                vm_ha_existing: dict[str, tuple[t.Any, str]] = {}
                vm_ha_cloud_inits: dict[str, str] = {}
                if spec.vm_ha is not None:
                    vm_ha_existing = self._discover_vm_ha_members(client, spec)
                    self.verify_vm_ha_existing_identities(
                        {name: public_ip for name, (_, public_ip) in vm_ha_existing.items()},
                        username=(
                            spec.vm_spec.get("ssh_username")
                            or os.environ.get("VPNGW_SSH_USER", "ubuntu")
                        ),
                    )
                    vm_ha_cloud_inits = self._prepare_vm_ha_enrollment_cloud_inits(
                        spec,
                        local_prefixes,
                        set(vm_ha_existing),
                        recreate,
                    )
                    existing = [vm_obj for vm_obj, _ in vm_ha_existing.values()]
                else:
                    existing = self._discover_existing_instances(client, spec)

                if not existing:
                    print("[VMManager] No existing VMs found")
                else:
                    print(f"[VMManager] Found {len(existing)} existing VM(s) for recreation")

                preserved_allocations: dict[str, list[str]] = {}
                if recreate and existing:
                    preserved_allocations = self._collect_preserved_allocations(existing)
                    self._delete_existing_instances_and_boot_disks(client, existing, spec)

                if spec.vm_ha is not None:
                    self._require_vm_ha_member_snapshot(
                        client,
                        spec,
                        {} if recreate else vm_ha_existing,
                    )

                provisioning: VMProvisioningConfig | None = None
                if spec.vm_ha is not None:
                    provisioning = self._build_vm_provisioning_config(
                        client,
                        spec,
                        local_prefixes,
                    )
                    self._ensure_vm_ha_shared_allocation(
                        alloc_client,
                        spec,
                        provisioning.subnet_id,
                    )
                    self._vm_ha_route_targets = self._resolve_vm_ha_route_targets(
                        client, spec, local_prefixes
                    )
                for i in range(spec.instance_count):
                    inst_name = f"{spec.name}-{i}"
                    if spec.vm_ha is not None:
                        expected_vm_exists: bool | None = (
                            False if recreate else inst_name in vm_ha_existing
                        )
                        needs_provisioning = recreate or not expected_vm_exists
                    else:
                        expected_vm_exists = None
                        needs_provisioning = not self._instance_exists(
                            client,
                            instance_api,
                            inst_name,
                        )
                    if needs_provisioning and provisioning is None:
                        provisioning = self._build_vm_provisioning_config(
                            client,
                            spec,
                            local_prefixes,
                        )
                    instance_provisioning = provisioning
                    if needs_provisioning and spec.vm_ha is not None:
                        if provisioning is None or f"{spec.name}-{i}" not in vm_ha_cloud_inits:
                            raise RuntimeError("VM-HA enrollment cloud-init was not prevalidated")
                        instance_provisioning = replace(
                            provisioning,
                            cloud_init=vm_ha_cloud_inits[f"{spec.name}-{i}"],
                        )
                    self._provision_instance(
                        client,
                        instance_api,
                        disk_api,
                        alloc_api,
                        alloc_client,
                        spec,
                        i,
                        recreate,
                        instance_provisioning,
                        preserved_allocations,
                        vm_ips,
                        expected_vm_exists=expected_vm_exists,
                    )
                if spec.vm_ha is not None:
                    active_index = spec.vm_ha.active_instance_index
                    active_name = f"{spec.name}-{active_index}"
                    active = self._get_ha_instance_by_name(client, active_name)
                    active_id = self._resource_id(active)
                    active_interfaces = list(
                        getattr(getattr(active, "spec", None), "network_interfaces", []) or []
                    )
                    if not active_id or len(active_interfaces) != 1:
                        raise RuntimeError("VM-HA active Compute/NIC identity is unavailable")
                    active_nic_name = str(getattr(active_interfaces[0], "name", ""))
                    if not active_nic_name or not self._vm_ha_shared_allocation_id:
                        raise RuntimeError("VM-HA active attachment identity is incomplete")
                    self._attach_vm_ha_shared_allocation_initially(
                        allocation_id=self._vm_ha_shared_allocation_id,
                        active_compute_id=active_id,
                        active_network_interface_name=active_nic_name,
                    )
            else:
                if spec.vm_ha is not None:
                    raise RuntimeError("VM-HA provisioning requires the Nebius SDK")
                self._log_scaffold_mode_instances(spec)
        except Exception as e:
            if spec.vm_ha is not None:
                self._private_alloc_ids.clear()
                self._vm_ha_shared_allocation_id = None
                self._vm_ha_route_targets = None
                raise RuntimeError(f"VM-HA provisioning failed closed: {e}") from e
            print(f"[VMManager] ensure_group failed: {e}. Proceeding in scaffold mode.")

        if spec.vm_ha is not None:
            try:
                binding = self._build_vm_ha_runtime_binding(client, spec)
            except Exception as e:
                self._private_alloc_ids.clear()
                self._vm_ha_shared_allocation_id = None
                self._vm_ha_route_targets = None
                raise RuntimeError(f"VM-HA runtime binding failed closed: {e}") from e
            return VMProvisioningResult(vm_ips, vm_ha_runtime_binding=binding)
        return vm_ips

    @staticmethod
    def _normalize_ip_value(ip_value: str | None) -> str | None:
        if not ip_value:
            return None
        return str(ip_value).strip().split("/")[0]

    def _allocation_ip_from_obj(self, alloc_obj: t.Any) -> str | None:
        try:
            spec_obj = getattr(alloc_obj, "spec", None)
            if spec_obj:
                ipv4_public = getattr(spec_obj, "ipv4_public", None)
                if ipv4_public:
                    address = getattr(ipv4_public, "address", None)
                    if address:
                        normalized_address = self._normalize_ip_value(str(address))
                        if normalized_address:
                            return normalized_address
                    cidr = getattr(ipv4_public, "cidr", None)
                    if cidr:
                        normalized_cidr = self._normalize_ip_value(str(cidr))
                        if normalized_cidr:
                            return normalized_cidr
            status = getattr(alloc_obj, "status", None)
            details = getattr(status, "details", None) if status else None
            cidr = getattr(details, "allocated_cidr", None) if details else None
            if cidr:
                normalized_cidr = self._normalize_ip_value(str(cidr))
                if normalized_cidr:
                    return normalized_cidr
        except Exception:
            return None
        return None

    def _list_allocations_by_ip(self, alloc_client: t.Any) -> dict[str, t.Any]:
        try:
            from nebius.api.nebius.vpc.v1 import ListAllocationsRequest  # type: ignore

            response = alloc_client.list(ListAllocationsRequest(parent_id=self.project_id or ""))
            if hasattr(response, "wait"):
                response = response.wait()
            items = []
            if hasattr(response, "items"):
                items = response.items
            elif hasattr(response, "__iter__"):
                items = list(response)

            mapping: dict[str, t.Any] = {}
            for allocation in items:
                full_allocation = allocation
                ip_value = self._allocation_ip_from_obj(full_allocation)
                if not ip_value:
                    full_allocation = self._hydrate_allocation(alloc_client, allocation)
                    ip_value = self._allocation_ip_from_obj(full_allocation)
                if ip_value:
                    mapping[ip_value] = full_allocation
            return mapping
        except Exception:
            return {}

    @staticmethod
    def _allocation_state(alloc_obj: t.Any) -> str | None:
        for path in (
            ("status", "state"),
            ("status", "state_name"),
            ("status", "lifecycle_state"),
            ("status", "phase"),
            ("status", "details", "state"),
            ("status", "details", "lifecycle_state"),
        ):
            current = alloc_obj
            for key in path:
                current = getattr(current, key, None)
                if current is None:
                    break
            if not current:
                continue
            try:
                if hasattr(current, "value"):
                    current = current.value
            except Exception:
                pass
            return str(current)
        return None

    @staticmethod
    def _allocation_is_attached(alloc_obj: t.Any) -> bool:
        for path in (
            ("status", "details", "attachments"),
            ("status", "attachments"),
            ("status", "assignment", "network_interface", "instance_id"),
            ("status", "assignment", "load_balancer", "id"),
            ("status", "details", "attached_to"),
            ("status", "details", "instance_id"),
            ("status", "details", "resource_id"),
            ("spec", "ipv4_public", "attached_to"),
            ("spec", "ipv4_public", "instance_id"),
        ):
            current = alloc_obj
            for key in path:
                current = getattr(current, key, None)
                if current is None:
                    break
            if not current:
                continue
            if isinstance(current, list):
                return len(current) > 0
            return True
        return False

    def _wait_for_allocation_release(
        self,
        alloc_client: t.Any,
        desired_ip: str,
    ) -> dict[str, t.Any]:
        print(
            f"[VMManager] Allocation {desired_ip} appears to be releasing. Waiting up to 10s before retry..."
        )
        allocations_by_ip: dict[str, t.Any] = {}
        for _ in range(5):
            time.sleep(2)
            allocations_by_ip = self._list_allocations_by_ip(alloc_client)
            alloc_obj = allocations_by_ip.get(desired_ip)
            if alloc_obj is None:
                break
            state = self._allocation_state(alloc_obj)
            if not state or not any(
                token in state.lower() for token in ("delet", "releas", "pending")
            ):
                break
        return allocations_by_ip

    @staticmethod
    def _desired_requested_ip(
        desired_matrix: list[list[str]],
        instance_index: int,
        nic_index: int,
    ) -> str | None:
        if instance_index >= len(desired_matrix):
            return None
        row = desired_matrix[instance_index]
        if not isinstance(row, list) or nic_index >= len(row):
            return None
        return row[nic_index] if row[nic_index] else None

    def _resolve_prepared_public_allocation(
        self,
        alloc_client: t.Any,
        subnet_id: str,
        alloc_name: str,
        desired_ip: str | None,
        allocations_by_ip: dict[str, t.Any],
    ) -> tuple[t.Any | None, dict[str, t.Any]]:
        alloc_obj = None

        if desired_ip:
            alloc_obj, allocations_by_ip = self._find_requested_public_allocation(
                alloc_client,
                None,
                alloc_name,
                desired_ip,
                allocations_by_ip,
            )
            if alloc_obj is not None:
                alloc_obj = self._require_public_allocation_in_gateway_subnet(
                    alloc_client,
                    alloc_obj,
                    subnet_id,
                    desired_ip,
                )
        else:
            by_name = self._get_allocation_by_name(alloc_client, alloc_name)
            if by_name is not None:
                alloc_obj = by_name

        if alloc_obj is None:
            return None, allocations_by_ip

        alloc_ip = self._resolve_known_allocation_ip(alloc_client, alloc_obj)
        alloc_obj = self._require_public_allocation_in_gateway_subnet(
            alloc_client,
            alloc_obj,
            subnet_id,
            alloc_ip,
        )
        if desired_ip and alloc_ip and alloc_ip != desired_ip:
            print(
                f"[VMManager] Note: Allocation {alloc_name} has IP {alloc_ip} which differs from requested {desired_ip}."
            )

        return alloc_obj, allocations_by_ip

    def _create_prepared_public_allocation(
        self,
        alloc_client: t.Any,
        subnet_id: str,
        alloc_name: str,
        desired_ip: str | None,
        allocations_by_ip: dict[str, t.Any],
    ) -> t.Any:
        from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            AllocationSpec,
            CreateAllocationRequest,
            IPv4PublicAllocationSpec,
        )

        request = CreateAllocationRequest(
            metadata=ResourceMetadata(
                name=alloc_name,
                parent_id=self.project_id or "",
            ),
            spec=AllocationSpec(
                ipv4_public=IPv4PublicAllocationSpec(
                    subnet_id=subnet_id,
                    cidr=desired_ip if desired_ip else "/32",
                )
            ),
        )
        try:
            operation = alloc_client.create(request).wait()
            self._sync_operation(operation)
        except Exception as e:
            error_text = str(e).lower()
            if "already exists" not in error_text and "duplicate" not in error_text:
                if desired_ip and ("immutable" in error_text or "subnet" in error_text):
                    print(
                        f"[VMManager] Requested IP {desired_ip} could not be allocated in the gateway subnet: {e}"
                    )
                else:
                    raise RuntimeError(
                        f"Failed to create public IP allocation {alloc_name}: {e}"
                    ) from e

        alloc_obj = self._get_allocation_by_name(alloc_client, alloc_name)
        if alloc_obj is not None:
            if desired_ip:
                alloc_obj = self._validate_requested_public_allocation(
                    alloc_client,
                    alloc_obj,
                    desired_ip=desired_ip,
                    alloc_name=alloc_name,
                    require_resolved_ip=False,
                )
            return alloc_obj
        if desired_ip and desired_ip in allocations_by_ip:
            return self._validate_requested_public_allocation(
                alloc_client,
                allocations_by_ip[desired_ip],
                desired_ip=desired_ip,
                alloc_name=alloc_name,
                require_resolved_ip=False,
            )
        raise RuntimeError(f"Failed to fetch allocation {alloc_name} after creation.")

    def _resolve_prepared_public_ip(
        self,
        alloc_client: t.Any,
        alloc_obj: t.Any,
        alloc_name: str,
        desired_ip: str | None,
    ) -> str:
        alloc_id = self._resource_id(alloc_obj)
        if not alloc_id:
            if desired_ip:
                return desired_ip
            raise RuntimeError(f"Allocation {alloc_name} returned no id.")

        alloc_ip = self._allocation_ip_from_obj(alloc_obj)
        if alloc_ip:
            return alloc_ip

        from nebius.api.nebius.vpc.v1 import GetAllocationRequest  # type: ignore

        for _ in range(3):
            alloc_ip = self.get_allocation_ip(alloc_id)
            if alloc_ip:
                return alloc_ip
            try:
                alloc_obj = alloc_client.get(GetAllocationRequest(id=alloc_id)).wait()
            except Exception:
                alloc_obj = None
            alloc_ip = self._allocation_ip_from_obj(alloc_obj) if alloc_obj else None
            if alloc_ip:
                return alloc_ip
            time.sleep(1)

        if desired_ip:
            return desired_ip
        raise RuntimeError(f"Allocation {alloc_name} returned no IP address (API may be delayed).")

    def prepare_network(
        self,
        spec: GatewayGroupSpec,
        *,
        allocate_ips: bool = True,
        desired_external_ips: list[list[str]] | None = None,
    ) -> list[list[str]]:
        """Ensure the dedicated gateway subnet exists and optionally reserve public IP allocations.

        Returns a list of public IPs per instance (list-of-lists, NIC order).
        """
        client = self._get_client()
        if client is None:
            raise RuntimeError("Nebius SDK client not available; cannot prepare network.")

        subnet_id = self._ensure_vpngw_subnet(client, spec)
        if not subnet_id:
            raise RuntimeError("Failed to resolve or create the dedicated gateway subnet.")

        # Ensure route table exists for the dedicated gateway subnet (idempotent).
        self._ensure_vpngw_route_table(client, subnet_id)

        if not allocate_ips:
            return []

        # CURRENT PLATFORM LIMITATION: num_nics=1 (enforced elsewhere on apply).
        num_nics = int(spec.vm_spec.get("num_nics", 1))
        if num_nics > 1:
            print(
                f"[VMManager] WARNING: num_nics={num_nics} but current platform only supports 1 NIC. Using num_nics=1."
            )
            num_nics = 1

        from nebius.api.nebius.vpc.v1 import AllocationServiceClient  # type: ignore

        alloc_client = AllocationServiceClient(client)

        desired_matrix = desired_external_ips or []
        desired_any = any(
            isinstance(row, list) and any(str(ip or "").strip() for ip in row)
            for row in desired_matrix
        )
        allocations_by_ip = self._list_allocations_by_ip(alloc_client) if desired_any else {}

        allocated_ips: list[list[str]] = []

        for inst_index in range(spec.instance_count):
            inst_name = f"{spec.name}-{inst_index}"
            inst_ips: list[str] = []

            for nic_index in range(num_nics):
                nic_name = f"eth{nic_index}"
                alloc_name = f"{inst_name}-{nic_name}-ip"
                desired_ip = self._normalize_ip_value(
                    self._desired_requested_ip(desired_matrix, inst_index, nic_index)
                )
                alloc_obj, allocations_by_ip = self._resolve_prepared_public_allocation(
                    alloc_client,
                    subnet_id,
                    alloc_name,
                    desired_ip,
                    allocations_by_ip,
                )
                if alloc_obj is None:
                    alloc_obj = self._create_prepared_public_allocation(
                        alloc_client,
                        subnet_id,
                        alloc_name,
                        desired_ip,
                        allocations_by_ip,
                    )

                inst_ips.append(
                    self._resolve_prepared_public_ip(
                        alloc_client,
                        alloc_obj,
                        alloc_name,
                        desired_ip,
                    )
                )

            allocated_ips.append(inst_ips)

        return allocated_ips

    def _resolve_gateway_network(
        self,
        client: t.Any,
        spec: GatewayGroupSpec,
    ) -> tuple[t.Any, str, str, t.Any]:
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            GetNetworkByNameRequest,
            GetNetworkRequest,
            ListNetworksRequest,
            NetworkServiceClient,
            SubnetServiceClient,
        )

        net_client = NetworkServiceClient(client)  # type: ignore
        network_obj = None
        if spec.network_id:
            try:
                network_obj = net_client.get(GetNetworkRequest(id=spec.network_id)).wait()
                print(f"[VMManager] Using network from YAML: {spec.network_id}")
            except Exception as e:
                raise RuntimeError(
                    f"[VMManager] Specified network_id '{spec.network_id}' not found: {e}"
                ) from e
        else:
            print("[VMManager] No gateway_group.network_id in YAML, auto-discovering network...")
            try:
                network_obj = net_client.get_by_name(
                    GetNetworkByNameRequest(
                        parent_id=self.project_id or "",
                        name="default-network",
                    )
                ).wait()
                print("[VMManager] Found default-network, using it")
            except Exception:
                network_obj = None

            if network_obj is None:
                try:
                    networks = net_client.list(
                        ListNetworksRequest(parent_id=self.project_id or "")
                    ).wait()
                    items = getattr(networks, "items", []) or []
                    if not items:
                        raise RuntimeError(
                            "[VMManager] No networks found in project. "
                            "Please create a network or specify gateway_group.network_id in YAML."
                        )
                    if len(items) == 1:
                        network_obj = items[0]
                        net_name = getattr(
                            getattr(network_obj, "metadata", None),
                            "name",
                            "unknown",
                        )
                        print(f"[VMManager] Found single custom network: {net_name}, using it")
                    else:
                        net_names = [
                            getattr(getattr(network, "metadata", None), "name", "unknown")
                            for network in items
                        ]
                        raise RuntimeError(
                            f"[VMManager] Multiple networks found in project: {', '.join(net_names)}. "
                            "Please specify which network to use by setting gateway_group.network_id in your YAML config."
                        )
                except RuntimeError:
                    raise
                except Exception as e:
                    raise RuntimeError(f"[VMManager] Failed to list networks: {e}") from e

        if network_obj is None:
            raise RuntimeError(
                "[VMManager] Could not resolve network. "
                "Please specify gateway_group.network_id in your YAML config."
            )

        network_id = self._resource_id(network_obj)
        if not network_id:
            raise RuntimeError("[VMManager] Resolved network is missing an id.")

        network_name = (
            getattr(getattr(network_obj, "metadata", None), "name", None) or "default-network"
        )
        subnet_client = SubnetServiceClient(client)  # type: ignore
        return network_obj, network_id, network_name, subnet_client

    def _find_gateway_subnet(
        self,
        subnet_client: t.Any,
        network_id: str,
        subnet_name: str,
    ) -> t.Any | None:
        try:
            from nebius.api.nebius.vpc.v1 import (  # type: ignore
                GetSubnetByNameRequest,
                ListSubnetsByNetworkRequest,
            )

            candidate = subnet_client.get_by_name(
                GetSubnetByNameRequest(parent_id=self.project_id or "", name=subnet_name)
            ).wait()
            if candidate is not None:
                candidate_network_id = getattr(getattr(candidate, "spec", None), "network_id", None)
                if candidate_network_id == network_id:
                    return candidate

            subnets = subnet_client.list_by_network(
                ListSubnetsByNetworkRequest(network_id=network_id)
            ).wait()
            for subnet in getattr(subnets, "items", []) or []:
                if getattr(getattr(subnet, "metadata", None), "name", None) == subnet_name:
                    return subnet
        except Exception:
            return None
        return None

    def _validate_existing_gateway_subnet(
        self,
        subnet_obj: t.Any,
        subnet_name: str,
        desired_network: ipaddress.IPv4Network | None,
        desired_prefix_length: int,
    ) -> ipaddress.IPv4Network:
        subnet_networks = self._extract_explicit_subnet_networks(subnet_obj)
        subnet_spec = getattr(subnet_obj, "spec", None)
        subnet_uses_network_pools = bool(
            getattr(getattr(subnet_spec, "ipv4_private_pools", None), "use_network_pools", False)
        )
        if subnet_uses_network_pools:
            raise ValueError(
                f"{subnet_name} exists but uses parent network pools (use_network_pools=true). "
                "Dedicated gateway subnets must use explicit private pools. "
                "Please delete the subnet manually and rerun the command."
            )
        if len(subnet_networks) != 1:
            raise ValueError(
                f"{subnet_name} must have exactly one explicit private CIDR. "
                f"Found {len(subnet_networks)} explicit CIDR(s): "
                + ", ".join(str(network) for network in subnet_networks)
            )

        existing_network = subnet_networks[0]
        if desired_network and existing_network != desired_network:
            raise ValueError(
                f"{subnet_name} exists with CIDR {existing_network}, but the config requires "
                f"{desired_network}. Delete the subnet manually and rerun the command."
            )
        if not desired_network and existing_network.prefixlen != desired_prefix_length:
            raise ValueError(
                f"{subnet_name} exists with prefix /{existing_network.prefixlen}, but the "
                f"config requires /{desired_prefix_length}. Delete the subnet manually and rerun the command."
            )
        return existing_network

    def _list_existing_subnet_networks(
        self,
        subnet_client: t.Any,
        network_id: str,
    ) -> list[ipaddress.IPv4Network]:
        from nebius.api.nebius.vpc.v1 import ListSubnetsByNetworkRequest  # type: ignore

        subnets = subnet_client.list_by_network(
            ListSubnetsByNetworkRequest(network_id=network_id)
        ).wait()
        existing_networks: list[ipaddress.IPv4Network] = []
        for subnet in getattr(subnets, "items", []) or []:
            existing_networks.extend(self._extract_explicit_subnet_networks(subnet))
        return existing_networks

    def _select_gateway_subnet_cidr(
        self,
        client: t.Any,
        network_obj: t.Any,
        subnet_client: t.Any,
        network_id: str,
        desired_network: ipaddress.IPv4Network | None,
        desired_prefix_length: int,
        network_name: str,
    ) -> str:
        existing_subnet_networks = self._list_existing_subnet_networks(subnet_client, network_id)
        if desired_network is not None:
            if any(desired_network.overlaps(existing) for existing in existing_subnet_networks):
                raise RuntimeError(
                    f"Requested gateway subnet CIDR {desired_network} overlaps with an existing explicit subnet in the target network."
                )
            self._ensure_network_pool_contains_cidr(client, network_obj, desired_network)
            return str(desired_network)

        network_pool_cidrs: list[ipaddress.IPv4Network] = []
        for _, pool_obj in self._get_network_private_pools(client, network_obj):
            for pool_cidr_obj in getattr(getattr(pool_obj, "spec", None), "cidrs", []) or []:
                pool_cidr = getattr(pool_cidr_obj, "cidr", None)
                if not pool_cidr:
                    continue
                pool_network = _parse_ipv4_network(str(pool_cidr))
                if pool_network is not None:
                    network_pool_cidrs.append(pool_network)

        cidr_to_use = self._find_first_free_subnet_cidr(
            network_pool_cidrs,
            existing_subnet_networks,
            prefix_length=desired_prefix_length,
        )
        if cidr_to_use:
            return cidr_to_use

        raise RuntimeError(
            f"Failed to calculate a free /{desired_prefix_length} gateway subnet in network '{network_name}'. "
            "Extend the network pool or specify gateway_group.subnet.cidr explicitly."
        )

    def _create_gateway_subnet(
        self,
        client: t.Any,
        subnet_client: t.Any,
        subnet_name: str,
        network_id: str,
        cidr_to_use: str,
    ) -> t.Any | None:
        from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            CreateSubnetRequest,
            IPv4PrivateSubnetPools,
            ListSubnetsByNetworkRequest,
            SubnetCidr,
            SubnetPool,
            SubnetSpec,
        )

        print(f"[VMManager] Creating gateway subnet '{subnet_name}' with CIDR {cidr_to_use}")

        ipv4_private_pools = IPv4PrivateSubnetPools()
        ipv4_private_pools.pools.extend([SubnetPool(cidrs=[SubnetCidr(cidr=cidr_to_use)])])
        ipv4_private_pools.use_network_pools = False

        request = CreateSubnetRequest(
            metadata=ResourceMetadata(
                name=subnet_name,
                parent_id=self.project_id or "",
            ),
            spec=SubnetSpec(
                network_id=network_id,
                ipv4_private_pools=ipv4_private_pools,
            ),
        )
        operation = subnet_client.create(request)
        self._sync_operation(operation)

        time.sleep(5)
        refreshed = subnet_client.list_by_network(
            ListSubnetsByNetworkRequest(network_id=network_id)
        ).wait()
        for subnet in getattr(refreshed, "items", []) or []:
            if getattr(getattr(subnet, "metadata", None), "name", None) != subnet_name:
                continue

            print(f"[VMManager] ✓ Subnet '{subnet_name}' created successfully")
            subnet_spec = getattr(subnet, "spec", None)
            private_pools = (
                getattr(subnet_spec, "ipv4_private_pools", None) if subnet_spec else None
            )
            if private_pools and getattr(private_pools, "use_network_pools", True):
                raise RuntimeError(
                    f"[VMManager] CRITICAL: Subnet '{subnet_name}' was created but use_network_pools=true! "
                    "This means the subnet will inherit the network's private pool instead of using the specified CIDR. "
                    "This is a bug in the Nebius API - it ignored our explicit use_network_pools=False setting. "
                    "The subnet has been created incorrectly and must be deleted manually."
                )

            self._create_vpngw_route_table(client, subnet, network_id)
            return subnet

        return None

    def _ensure_vpngw_subnet(self, client: t.Any, spec: GatewayGroupSpec) -> str | None:
        """Ensure the configured dedicated gateway subnet exists in the chosen network."""
        if client is None:
            return None

        subnet_settings = self._gateway_subnet_settings(spec)
        subnet_name = str(subnet_settings["name"])
        desired_cidr = subnet_settings["cidr"]
        desired_prefix_length = int(subnet_settings["prefix_length"])

        try:
            desired_network = _parse_ipv4_network(desired_cidr) if desired_cidr else None
            if desired_cidr and desired_network is None:
                raise ValueError(
                    f"gateway_group.subnet.cidr must be a valid IPv4 CIDR, got {desired_cidr!r}."
                )
            network_obj, network_id, network_name, subnet_client = self._resolve_gateway_network(
                client,
                spec,
            )
            subnet_obj = self._find_gateway_subnet(subnet_client, network_id, subnet_name)
            if subnet_obj is not None:
                existing_network = self._validate_existing_gateway_subnet(
                    subnet_obj,
                    subnet_name,
                    desired_network,
                    desired_prefix_length,
                )
                print(
                    f"[VMManager] Found existing gateway subnet '{subnet_name}' ({existing_network})"
                )

            if subnet_obj is None:
                try:
                    print(f"[VMManager] Creating gateway subnet '{subnet_name}' ...")
                    cidr_to_use = self._select_gateway_subnet_cidr(
                        client,
                        network_obj,
                        subnet_client,
                        network_id,
                        desired_network,
                        desired_prefix_length,
                        network_name,
                    )
                    subnet_obj = self._create_gateway_subnet(
                        client,
                        subnet_client,
                        subnet_name,
                        network_id,
                        cidr_to_use,
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"[VMManager] Failed to create '{subnet_name}' in {network_name}: {e}. "
                        "Please provide gateway_group.network_id with sufficient IP space or pre-create the subnet."
                    ) from e

            return self._resource_id(subnet_obj)
        except Exception as e:
            print(f"[VMManager] Error in _ensure_vpngw_subnet: {e}")
            return None

    def _ensure_vpngw_route_table(self, client: t.Any, subnet_id: str | None) -> None:
        """Ensure the gateway subnet has a dedicated route table.

        This method is idempotent - it checks if RT exists, creates if missing.
        Called both during subnet creation and when updating existing VMs.

        Args:
            client: Nebius SDK client
            subnet_id: Subnet ID for the dedicated gateway subnet
        """
        if not client or not subnet_id:
            return

        try:
            from nebius.api.nebius.vpc.v1 import (
                GetRouteTableRequest,
                GetSubnetRequest,
                RouteTableServiceClient,
                SubnetServiceClient,
            )  # type: ignore

            subnet_client = SubnetServiceClient(client)  # type: ignore

            # Get subnet to check if it has a route table
            try:
                subnet_obj = subnet_client.get(GetSubnetRequest(id=subnet_id)).wait()
            except Exception as e:
                print(f"[VMManager] Could not get subnet {subnet_id}: {e}")
                return

            # Check if subnet has route table attached
            subnet_spec = getattr(subnet_obj, "spec", None)
            subnet_name = (
                getattr(getattr(subnet_obj, "metadata", None), "name", None) or "gateway subnet"
            )
            rt_id = getattr(subnet_spec, "route_table_id", None) if subnet_spec else None

            if rt_id:
                # Route table exists, verify it's valid
                try:
                    rt_client = RouteTableServiceClient(client)  # type: ignore
                    rt_obj = rt_client.get(GetRouteTableRequest(id=rt_id)).wait()
                    rt_name = getattr(getattr(rt_obj, "metadata", None), "name", None) or "unknown"
                    print(f"[VMManager] Route table '{rt_name}' already attached to {subnet_name}")
                    return
                except Exception:
                    # RT ID exists but not found - will recreate
                    print(
                        "[VMManager] Route table ID found but not accessible, creating new one..."
                    )

            # No route table or invalid RT - create one
            network_id = subnet_spec.network_id if subnet_spec else None
            if not network_id:
                print("[VMManager] Cannot create route table: network_id not found")
                return

            self._create_vpngw_route_table(client, subnet_obj, network_id)

        except Exception as e:
            print(f"[VMManager] Error ensuring route table for gateway subnet: {e}")

    def _create_vpngw_route_table(self, client: t.Any, subnet_obj: t.Any, network_id: str) -> None:
        """Create a dedicated route table for the gateway subnet with default egress route.

        Args:
            client: Nebius SDK client
            subnet_obj: Subnet object for the dedicated gateway subnet
            network_id: Network ID containing the subnet
        """
        try:
            from nebius.api.nebius.common.v1 import ResourceMetadata  # type: ignore
            from nebius.api.nebius.vpc.v1 import (  # type: ignore
                CreateRouteRequest,
                CreateRouteTableRequest,
                RouteServiceClient,
                RouteTableServiceClient,
                RouteTableSpec,
                SubnetServiceClient,
                SubnetSpec,
                UpdateSubnetRequest,
                route_pb2,  # type: ignore
            )  # type: ignore

            subnet_name = (
                getattr(getattr(subnet_obj, "metadata", None), "name", None) or "vpngw-subnet"
            )
            rt_name = self._gateway_route_table_name(subnet_name)
            rt_client = RouteTableServiceClient(client)  # type: ignore
            route_client = RouteServiceClient(client)  # type: ignore
            subnet_client = SubnetServiceClient(client)  # type: ignore

            # Get subnet ID
            subnet_id = getattr(subnet_obj, "id", None)
            if not subnet_id:
                subnet_id = getattr(getattr(subnet_obj, "metadata", None), "id", None)

            if not subnet_id:
                print("[VMManager] Cannot create route table: subnet_id not found")
                return

            print(f"[VMManager] Creating dedicated route table '{rt_name}' for {subnet_name}...")

            # Create route table (handle ALREADY_EXISTS)
            rt_id = None
            try:
                rt_req = CreateRouteTableRequest(
                    metadata=ResourceMetadata(
                        name=rt_name,
                        parent_id=self.project_id or "",
                    ),
                    spec=RouteTableSpec(network_id=network_id),
                )
                rt_op = rt_client.create(rt_req).wait()
                try:
                    rt_op.sync_wait()
                except Exception:
                    pass

                rt_id = rt_op.resource_id or ""
            except Exception as e:
                if "ALREADY_EXISTS" in str(e) or "already exists" in str(e):
                    print(f"[VMManager] Route table '{rt_name}' already exists, reusing...")
                    # Get existing route table by name
                    try:
                        from nebius.api.nebius.vpc.v1 import GetRouteTableByNameRequest

                        rt_obj = rt_client.get_by_name(
                            GetRouteTableByNameRequest(
                                parent_id=self.project_id or "", name=rt_name
                            )
                        ).wait()
                        rt_id = getattr(rt_obj, "id", None) or getattr(
                            getattr(rt_obj, "metadata", None), "id", None
                        )
                    except Exception as get_err:
                        print(f"[VMManager] Could not retrieve existing route table: {get_err}")
                        return
                else:
                    print(f"[VMManager] Error creating route table: {e}")
                    return

            if not rt_id:
                print("[VMManager] Route table creation returned no resource_id")
                return

            print(f"[VMManager] Created route table: {rt_name} (ID: {rt_id})")

            # Add default egress route (0.0.0.0/0 -> default-egress)
            try:
                route_req = CreateRouteRequest(
                    metadata=ResourceMetadata(
                        name="default-egress",
                        parent_id=rt_id,
                    ),
                    spec=route_pb2.RouteSpec(
                        destination=route_pb2.DestinationMatch(cidr="0.0.0.0/0"),
                        next_hop=route_pb2.NextHop(default_egress_gateway=True),
                    ),
                )
                route_client.create(route_req).wait()
                print("[VMManager] Added route: 0.0.0.0/0 -> default-egress")
            except Exception as e:
                err_str = str(e)
                if "ALREADY_EXISTS" in err_str or "already exists" in err_str:
                    print("[VMManager] Default egress route already exists, skipping")
                else:
                    print(f"[VMManager] Warning: Could not create default egress route: {e}")

            # Attach route table to subnet
            try:
                subnet_meta = getattr(subnet_obj, "metadata", None)
                subnet_spec = getattr(subnet_obj, "spec", None)
                if subnet_meta and subnet_spec:
                    # Get subnet's network_id - required by API
                    subnet_network_id = getattr(subnet_spec, "network_id", None)
                    if not subnet_network_id:
                        print(
                            "[VMManager] Warning: Subnet has no network_id, cannot attach route table"
                        )
                        return

                    # CRITICAL: Preserve existing ipv4_private_pools when updating
                    # If we don't include it, the API resets use_network_pools=true!
                    existing_ipv4_private_pools = getattr(subnet_spec, "ipv4_private_pools", None)
                    existing_ipv4_public_pools = getattr(subnet_spec, "ipv4_public_pools", None)

                    # ResourceMetadata requires: id, parent_id (required), and name (validated)
                    # SubnetSpec requires: network_id (required) and route_table_id (what we're updating)
                    update_req = UpdateSubnetRequest(
                        metadata=ResourceMetadata(
                            id=getattr(subnet_meta, "id", subnet_id),
                            parent_id=getattr(subnet_meta, "parent_id", ""),
                            name=getattr(subnet_meta, "name", ""),
                        ),
                        spec=SubnetSpec(
                            network_id=subnet_network_id,
                            route_table_id=rt_id,
                            ipv4_private_pools=existing_ipv4_private_pools,
                            ipv4_public_pools=existing_ipv4_public_pools,
                        ),
                    )
                    subnet_client.update(update_req).wait()
                    print(f"[VMManager] ✓ Attached route table '{rt_name}' to {subnet_name}")
                else:
                    print("[VMManager] Warning: Could not get subnet metadata/spec for update")
            except Exception as e:
                print(f"[VMManager] Warning: Could not attach route table to subnet: {e}")

        except Exception as e:
            print(f"[VMManager] Error creating route table for gateway subnet: {e}")

    def get_instance_ssh_target(self, instance_index: int) -> str:
        # Placeholder fallback if external IP wasn't available in plan
        return f"{instance_index}"

    def _build_cloud_init(
        self,
        ssh_key: str | None = None,
        local_prefixes: list[str] | None = None,
    ) -> str:
        """Return a hardened cloud-init to install deps, configure security, and setup the gateway.

        This prepares a production-hardened VPN gateway with:
        - SSH hardening (key-only, no root, limited retries)
        - Fail2ban for SSH brute-force protection
        - Unattended security upgrades
        - Auditd for exec logging
        - UFW firewall (allows IPsec, SSH from management, blocks rest)
        - System hardening (sysctl, minimal packages)

        Args:
            ssh_key: Optional SSH public key to add to ubuntu user's authorized_keys
            local_prefixes: Optional list of local VPC prefixes for firewall configuration
        """
        try:
            with resources.as_file(
                resources.files("nebius_vpngw").joinpath("systemd/nebius-vpngw-agent.service")
            ) as p:
                unit_text = p.read_text(encoding="utf-8")
        except Exception:
            unit_text = textwrap.dedent(
                """
                [Unit]
                Description=Nebius VPNGW Agent
                After=network.target

                [Service]
                Type=simple
                Environment="PYTHONUNBUFFERED=1"
                ExecStart=/usr/bin/python3 -m nebius_vpngw.agent.main
                ExecReload=/bin/kill -HUP $MAINPID
                Restart=always
                RestartSec=3
                StandardOutput=journal
                StandardError=journal

                [Install]
                WantedBy=multi-user.target
                """
            ).strip()

        indented_unit = textwrap.indent(unit_text, " " * 12)

        # Build users section with SSH key if provided
        users_section = ""
        if ssh_key:
            users_section = (
                f"users:\n  - name: ubuntu\n    ssh_authorized_keys:\n      - {ssh_key}\n"
            )

        cloud = (
            "#cloud-config\n"
            f"{users_section}"
            "package_update: true\n"
            "package_upgrade: true\n"
            "packages:\n"
            "  - strongswan\n"
            "  - strongswan-swanctl\n"
            "  - strongswan-pki\n"
            "  - libcharon-extra-plugins\n"
            "  - python3\n"
            "  - python3-pip\n"
            "  - python3-yaml\n"
            "  - ufw\n"
            "  - fail2ban\n"
            "  - unattended-upgrades\n"
            "  - auditd\n"
            "  - iproute2\n"
            "  - curl\n"
            "  - vim\n"
            "write_files:\n"
            "  - path: /etc/systemd/system/nebius-vpngw-agent.service\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            f"{indented_unit}\n"
            "  - path: /etc/ssh/sshd_config.d/50-vpngw.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            Port 22\n"
            "            AddressFamily any\n"
            "            ListenAddress 0.0.0.0\n"
            "            PermitRootLogin no\n"
            "            PasswordAuthentication no\n"
            "            ChallengeResponseAuthentication no\n"
            "            UsePAM yes\n"
            "            X11Forwarding no\n"
            "            AllowTcpForwarding yes\n"
            "            GatewayPorts no\n"
            "            PermitTunnel no\n"
            "            MaxAuthTries 3\n"
            "            LogLevel VERBOSE\n"
            "            AcceptEnv LANG LC_*\n"
            "            Subsystem sftp /usr/lib/openssh/sftp-server\n"
            "  - path: /etc/fail2ban/jail.d/sshd.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [sshd]\n"
            "            enabled = true\n"
            "            port = ssh\n"
            "            logpath = /var/log/auth.log\n"
            "            maxretry = 3\n"
            "            bantime = 3600\n"
            "            findtime = 600\n"
            "  - path: /etc/apt/apt.conf.d/50unattended-upgrades\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            Unattended-Upgrade::Allowed-Origins {\n"
            '              "${distro_id}:${distro_codename}-security";\n'
            "            };\n"
            '            Unattended-Upgrade::AutoFixInterruptedDpkg "true";\n'
            '            Unattended-Upgrade::MinimalSteps "true";\n'
            '            Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";\n'
            '            Unattended-Upgrade::Remove-Unused-Dependencies "true";\n'
            '            Unattended-Upgrade::Automatic-Reboot "false";\n'
            '            Unattended-Upgrade::Automatic-Reboot-Time "03:00";\n'
            "  - path: /etc/audit/rules.d/50-vpngw.rules\n"
            '    permissions: "0640"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            # Audit execve for security monitoring\n"
            "            -a always,exit -F arch=b64 -S execve -k exec\n"
            "            -a always,exit -F arch=b32 -S execve -k exec\n"
            "            # Audit file modifications in sensitive directories\n"
            "            -w /etc/ipsec.conf -p wa -k ipsec_config\n"
            "            -w /etc/ipsec.secrets -p wa -k ipsec_secrets\n"
            "            -w /etc/frr/frr.conf -p wa -k frr_config\n"
            "            -w /etc/nebius-vpngw/ -p wa -k vpngw_config\n"
            "  - path: /usr/local/bin/log-restart-required.sh\n"
            '    permissions: "0755"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            #!/bin/bash\n"
            "            if [ -f /var/run/reboot-required ]; then\n"
            '              logger -t vpngw "Restart required after updates on $(hostname)"\n'
            "            fi\n"
            "  - path: /etc/systemd/system/log-restart-required.service\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            Description=Log when a restart is required after upgrades\n"
            "            [Service]\n"
            "            Type=oneshot\n"
            "            ExecStart=/usr/local/bin/log-restart-required.sh\n"
            "  - path: /etc/systemd/system/log-restart-required.timer\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            Description=Run restart-required logger hourly\n"
            "            [Timer]\n"
            "            OnBootSec=5min\n"
            "            OnUnitActiveSec=1h\n"
            "            Persistent=true\n"
            "            [Install]\n"
            "            WantedBy=timers.target\n"
            "  - path: /etc/vpngw_mgmt_cidrs\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            # Management CIDRs allowed for SSH access (one per line)\n"
            "            # Examples:\n"
            "            # 203.0.113.4/32\n"
            "            # 198.51.100.0/24\n"
            "            # This file will be populated by the agent with allowed management IPs\n"
            "  - path: /etc/vpngw_peer_ips\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            # VPN peer public IPs (one per line)\n"
            "            # This file will be populated by the agent from connection configs\n"
            "  - path: /etc/vpngw_local_prefixes\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            # Local VPC prefixes to allow through firewall (one per line)\n"
            "            # This file will be populated by the agent from gateway.local_prefixes\n"
        )

        # Add local prefixes if provided
        if local_prefixes:
            for prefix in local_prefixes:
                cloud += f"            {prefix}\n"

        firewall_script = _read_firewall_setup_script()
        esp4_preflight_script = _read_esp4_preflight_script()
        cloud += (
            "  - path: /usr/local/bin/setup-vpngw-firewall.sh\n"
            '    permissions: "0755"\n'
            "    owner: root:root\n"
            "    content: |\n"
            + textwrap.indent(firewall_script.rstrip() + "\n", "            ")
            + "  - path: /usr/local/bin/nebius-vpngw-esp4-preflight.sh\n"
            '    permissions: "0755"\n'
            "    owner: root:root\n"
            "    content: |\n"
            + textwrap.indent(esp4_preflight_script.rstrip() + "\n", "            ")
        )

        cloud += (
            "  - path: /etc/systemd/system/nebius-vpngw-esp4-preflight.service\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            Description=Nebius VPNGW ESP4 kernel module readiness preflight\n"
            "            After=local-fs.target systemd-modules-load.service\n"
            "            Before=strongswan-starter.service strongswan.service frr.service nebius-vpngw-agent.service\n"
            "            \n"
            "            [Service]\n"
            "            Type=oneshot\n"
            "            ExecStart=/usr/local/bin/nebius-vpngw-esp4-preflight.sh --verify\n"
            "            RemainAfterExit=yes\n"
            "            \n"
            "            [Install]\n"
            "            WantedBy=multi-user.target\n"
            "  - path: /etc/frr/daemons\n"
            '    permissions: "0644"\n'
            "    owner: frr:frr\n"
            "    content: |\n"
            "            # FRR daemons configuration - enable bgpd\n"
            "            bgpd=yes\n"
            '            bgpd_options="   -A 0.0.0.0"\n'
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
            "  - path: /etc/sysctl.d/99-zzz-vpngw.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            ########################################\n"
            "            # Nebius VPN Gateway – XFRM Routing Stack\n"
            "            ########################################\n"
            "            \n"
            "            # 1. Global forwarding – the box is a router\n"
            "            net.ipv4.ip_forward = 1\n"
            "            \n"
            "            # 1.1 TCP MTU probing – recover when PMTUD is blocked\n"
            "            net.ipv4.tcp_mtu_probing = 1\n"
            "            \n"
            "            # 2. Reverse path filtering\n"
            "            # Route-based IPsec with xfrm-interfaces often looks asymmetric to the kernel.\n"
            "            # Strict or even loose rp_filter can drop valid tunnel traffic. Disable it.\n"
            "            net.ipv4.conf.all.rp_filter = 0\n"
            "            net.ipv4.conf.default.rp_filter = 0\n"
            "            \n"
            "            # Interface-specific – belt and suspenders\n"
            "            net.ipv4.conf.eth0.rp_filter = 0\n"
            "            net.ipv4.conf.lo.rp_filter = 0\n"
            "            \n"
            "            # 3. Disable ICMP redirects – the gateway must not rely on redirects, and\n"
            "            # cloud fabric should never try to optimize routes via redirects.\n"
            "            net.ipv4.conf.all.accept_redirects = 0\n"
            "            net.ipv4.conf.default.accept_redirects = 0\n"
            "            net.ipv4.conf.all.send_redirects = 0\n"
            "            net.ipv4.conf.default.send_redirects = 0\n"
            "            \n"
            "            # 4. Source routing – off for security and predictability\n"
            "            net.ipv4.conf.all.accept_source_route = 0\n"
            "            net.ipv4.conf.default.accept_source_route = 0\n"
            "            \n"
            "            # 5. Martian logging (optional, useful for debugging weird traffic)\n"
            "            net.ipv4.conf.all.log_martians = 1\n"
            "            net.ipv4.conf.default.log_martians = 1\n"
            "            \n"
            "            # 6. Basic IPv6 hygiene (if you are not using IPv6 in the tunnels yet)\n"
            "            net.ipv6.conf.all.accept_redirects = 0\n"
            "            net.ipv6.conf.default.accept_redirects = 0\n"
            "            net.ipv6.conf.all.accept_ra = 0\n"
            "            net.ipv6.conf.default.accept_ra = 0\n"
            "  - path: /etc/systemd/system/ufw.service.d/override.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            After=network-online.target cloud-init.service\n"
            "            Wants=network-online.target\n"
            "  - path: /etc/systemd/system/strongswan-starter.service.d/override.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            After=nebius-vpngw-esp4-preflight.service ufw.service network-online.target\n"
            "            Wants=ufw.service\n"
            "            Requires=nebius-vpngw-esp4-preflight.service\n"
            "  - path: /etc/systemd/system/frr.service.d/override.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            After=strongswan-starter.service\n"
            "            Wants=strongswan-starter.service\n"
            "  - path: /etc/systemd/system/nebius-vpngw-agent.service.d/override.conf\n"
            '    permissions: "0644"\n'
            "    owner: root:root\n"
            "    content: |\n"
            "            [Unit]\n"
            "            After=strongswan-starter.service frr.service\n"
            "            Wants=strongswan-starter.service frr.service\n"
        )

        # Add runcmd section
        cloud += (
            "runcmd:\n"
            '  - [ bash, -lc, "mkdir -p /etc/nebius-vpngw" ]\n'
            '  - [ bash, -lc, "mkdir -p /var/lib/nebius-vpngw" ]\n'
            '  - [ bash, -lc, "mkdir -p /etc/ipsec.d" ]\n'
            "  # Install FRR 10.x from official repository (fixes route installation bug in 8.4.4)\n"
            '  - [ bash, -c, "curl -s https://deb.frrouting.org/frr/keys.asc | tee /usr/share/keyrings/frrouting.asc > /dev/null" ]\n'
            '  - [ bash, -c, "UBUNTU_CODENAME=$(lsb_release -cs); echo \\"deb [signed-by=/usr/share/keyrings/frrouting.asc] https://deb.frrouting.org/frr $UBUNTU_CODENAME frr-stable\\" > /etc/apt/sources.list.d/frr.list" ]\n'
            "  - [ apt-get, update ]\n"
            '  - [ bash, -c, "DEBIAN_FRONTEND=noninteractive apt-get install -y frr frr-pythontools" ]\n'
            "  # Prepare ESP4 after Ubuntu package upgrades; defer VPN services if a reboot is required\n"
            '  - [ bash, -lc, "/usr/local/bin/nebius-vpngw-esp4-preflight.sh --prepare; rc=$?; if [ $rc -eq 75 ]; then exit 0; fi; exit $rc" ]\n'
            "  # Comment out conflicting sysctl settings in /etc/sysctl.conf (prevents our 99-zzz-vpngw.conf from being overridden)\n"
            "  - [ bash, -c, \"sed -i 's/^net.ipv4.ip_forward=.*/#&  # Overridden by 99-zzz-vpngw.conf/' /etc/sysctl.conf\" ]\n"
            "  - [ bash, -c, \"sed -i 's/^net.ipv4.conf.all.rp_filter=.*/#&  # Overridden by 99-zzz-vpngw.conf/' /etc/sysctl.conf\" ]\n"
            "  - [ bash, -c, \"sed -i 's/^net.ipv4.conf.default.rp_filter=.*/#&  # Overridden by 99-zzz-vpngw.conf/' /etc/sysctl.conf\" ]\n"
            "  # Apply all sysctl settings (99-zzz-vpngw.conf loads after 99-sysctl.conf symlink)\n"
            "  - [ sysctl, --system ]\n"
            "  # CRITICAL: Enable UFW firewall for security hardening\n"
            "  # UFW MUST be active for proper packet forwarding through XFRM tunnels\n"
            "  # Without UFW active, netfilter is not properly initialized and VPC traffic\n"
            "  # may not be correctly routed through the VPN gateway\n"
            '  - [ bash, -lc, "/usr/local/bin/setup-vpngw-firewall.sh > /var/log/vpngw-firewall-setup.log 2>&1 || true" ]\n'
            "  # Load auditd rules\n"
            '  - [ bash, -lc, "augenrules --load || true" ]\n'
            "  # Enable and start services\n"
            "  - [ systemctl, daemon-reload ]\n"
            "  - [ systemctl, enable, auditd ]\n"
            "  - [ systemctl, enable, fail2ban ]\n"
            "  - [ systemctl, enable, log-restart-required.timer ]\n"
            "  - [ systemctl, enable, nebius-vpngw-esp4-preflight ]\n"
            "  - [ systemctl, enable, strongswan-starter ]\n"
            "  - [ systemctl, enable, frr ]\n"
            "  - [ systemctl, enable, nebius-vpngw-agent ]\n"
            "  - [ systemctl, start, auditd ]\n"
            "  - [ systemctl, start, fail2ban ]\n"
            "  - [ systemctl, start, log-restart-required.timer ]\n"
            '  - [ bash, -lc, "if [ ! -f /var/lib/nebius-vpngw/esp4-reboot-pending ]; then systemctl start nebius-vpngw-esp4-preflight strongswan-starter frr; fi" ]\n'
            "  # Reload SSH to apply hardening config\n"
            "  - [ systemctl, reload, ssh ]\n"
            "  # Reboot only after cloud-init has written files and enabled services\n"
            '  - [ bash, -lc, "if [ -f /var/lib/nebius-vpngw/esp4-reboot-pending ]; then logger -t vpngw \\"Rebooting to activate ESP4/kernel update before VPN services start\\"; shutdown -r +1 \\"Nebius VPN Gateway rebooting to activate ESP4/kernel update\\"; fi" ]\n'
            "  # Log completion\n"
            "  - [ bash, -c, \"logger -t vpngw 'Cloud-init hardening complete for VPN gateway'\" ]\n"
        )
        return cloud
