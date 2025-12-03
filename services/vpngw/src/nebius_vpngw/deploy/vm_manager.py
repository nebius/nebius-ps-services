from __future__ import annotations

import typing as t
import textwrap
import importlib.resources as resources

from ..config_loader import GatewayGroupSpec, InstanceResolvedConfig


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

    def ensure_group(self, spec: GatewayGroupSpec, recreate: bool = False) -> None:
        """Ensure gateway VMs exist per spec.

        Pseudocode for Nebius SDK integration:
        - client = InstanceServiceClient(auth=...)
        - existing = client.list(filter=name prefix)
        - if recreate: delete existing, wait; then create all
        - else: create missing, skip existing
        - attach public IPs according to spec.external_ips
        - set network interface subnet to spec.vm_spec.vpn_subnet_id
        """
        print(
            f"[VMManager] ensure_group name={spec.name} count={spec.instance_count} region={spec.region} recreate={recreate}"
        )
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

                # Discover existing
                existing = []
                if instance_api is not None and hasattr(instance_api, "list"):
                    try:
                        existing = list(instance_api.list(filter=f"name starts_with '{spec.name}-'", project_id=self.project_id))
                    except TypeError:
                        try:
                            existing = list(instance_api.list(filter=f"name starts_with '{spec.name}-'"))
                        except Exception:
                            existing = []
                    except Exception:
                        existing = []

                # Optionally delete existing
                if recreate and existing and instance_api is not None and hasattr(instance_api, "delete"):
                    print(f"[VMManager] Recreate requested; deleting {len(existing)} instances")
                    for inst in existing:
                        try:
                            instance_api.delete(inst.id)
                        except Exception as e:
                            print(f"[VMManager] delete failed: {e}")

                # Ensure each instance
                for i in range(spec.instance_count):
                    inst_name = f"{spec.name}-{i}"
                    # Check if exists
                    ok = False
                    try:
                        if instance_api is not None and hasattr(instance_api, "get_by_name"):
                            try:
                                inst = instance_api.get_by_name(name=inst_name, project_id=self.project_id)
                            except TypeError:
                                inst = instance_api.get_by_name(name=inst_name)
                            ok = inst is not None
                    except Exception:
                        ok = False
                    if ok:
                        print(f"[VMManager] Exists: {inst_name}")
                        continue

                    # Step 1: Ensure boot disk exists (following CLI pattern)
                    # Determine/ensure gateway subnet (vpngw-subnet) in desired network
                    subnet_id = self._ensure_vpngw_subnet(client, spec)
                    nic = {"subnet_id": subnet_id}
                    # Preset-based CPU/mem; fall back to cores/memory_gb if preset missing
                    platform = spec.vm_spec.get("platform") or "cpu-d3"
                    preset = spec.vm_spec.get("preset")
                    cores = spec.vm_spec.get("cores")
                    memory_gb = spec.vm_spec.get("memory_gb")
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
                    cloud_init = self._build_cloud_init()
                    boot_disk_name = f"{inst_name}-boot"
                    boot_disk_id = None
                    # Prefer explicit DiskServiceClient with CreateDiskRequest per schema
                    try:
                        from nebius.api.nebius.compute.v1 import (
                            DiskServiceClient,
                            CreateDiskRequest,
                            DiskSpec,
                            SourceImageFamily,
                            ImageServiceClient,
                            GetImageLatestByFamilyRequest,
                        )  # type: ignore
                        from nebius.api.nebius.common.v1 import ResourceMetadata, GetByNameRequest  # type: ignore
                        dsc = DiskServiceClient(client)  # type: ignore
                        # Try get_by_name first
                        boot_disk_id = None
                        print(f"[VMManager] DEBUG: self.project_id={self.project_id!r} type={type(self.project_id)}")
                        if self.project_id:
                            try:
                                # Prefer get_by_name if available on client
                                if hasattr(dsc, "get_by_name"):
                                    disk_obj = dsc.get_by_name(GetByNameRequest(parent_id=self.project_id, name=boot_disk_name)).wait()
                                    boot_disk_id = getattr(disk_obj, "id", None) or getattr(getattr(disk_obj, "metadata", None), "id", None)
                                    if boot_disk_id:
                                        print(f"[VMManager] Found existing disk {boot_disk_name} id={boot_disk_id}")
                            except Exception as e:
                                # Disk doesn't exist yet or lookup failed
                                print(f"[VMManager] Disk lookup failed (will create): {e}")
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
                                # If disk already exists, refetch id by name and proceed
                                msg = str(e)
                                print(f"[VMManager] Disk create exception: {msg}")
                                if "ALREADY_EXISTS" in msg or f"disk with name \"{boot_disk_name}\" already exists" in msg:
                                    print(f"[VMManager] Disk already exists, refetching ID...")
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
                                        print(f"[VMManager] Cannot refetch: project_id is None")
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

                    # Step 2: Ensure public IP allocation
                    # Current: 1 IP per VM (platform limitation: 1 NIC per instance, all tunnels share IP)
                    # Future: configurable for multi-VM HA (1 IP per VM, 1 tunnel per VM)
                    desired_ips = []
                    if spec.external_ips:
                        desired_ips = [ip for ip in spec.external_ips[:1] if ip]  # Use first IP only
                    alloc_ids: list[str] = []
                    num_allocations = 1  # TODO: Make configurable for multi-VM deployment
                    if alloc_api is not None or alloc_client is not None:
                        for ni in range(num_allocations):
                            desired_ip = desired_ips[ni] if ni < len(desired_ips) else None
                            alloc_obj = None
                            # Match existing allocation by IP if provided
                            if desired_ip:
                                try:
                                    get_by_addr = getattr(alloc_api, "get_by_address", None)
                                    if get_by_addr:
                                        alloc_obj = get_by_addr(address=desired_ip, project_id=self.project_id)
                                except Exception:
                                    alloc_obj = None
                            # Also try by-name before creating to avoid ALREADY_EXISTS
                            if alloc_obj is None and alloc_client is not None:
                                try:
                                    from nebius.api.nebius.vpc.v1 import GetAllocationByNameRequest  # type: ignore
                                    pre_name = f"{inst_name}-ip" if num_allocations == 1 else f"{inst_name}-alloc-{ni}"
                                    alloc_obj = alloc_client.get_by_name(
                                        GetAllocationByNameRequest(parent_id=self.project_id or "", name=pre_name)
                                    ).wait()
                                except Exception:
                                    alloc_obj = None
                            # Create allocation in vpngw-subnet when not found
                            if alloc_obj is None:
                                # Ensure subnet_id is present before attempting allocation creation
                                if not nic.get("subnet_id"):
                                    raise RuntimeError(
                                        "[VMManager] Cannot create public IP allocation: subnet_id is not set. "
                                        "Resolve subnet creation first or provide a valid network_id."
                                    )
                                try:
                                    alloc_name = f"{inst_name}-ip" if num_allocations == 1 else f"{inst_name}-alloc-{ni}"
                                    print(f"[VMManager] Creating public IP allocation {alloc_name} in vpngw-subnet ...")
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
                            # Extract allocation id robustly
                            alloc_id = None
                            if alloc_obj is not None:
                                alloc_id = getattr(alloc_obj, "id", None)
                                if not alloc_id:
                                    alloc_id = getattr(getattr(alloc_obj, "metadata", None), "id", None)
                            if alloc_id:
                                alloc_ids.append(alloc_id)

                    # NOTE: Legacy code created 2 NICs; current platform supports only 1 NIC per instance.
                    # The single NIC setup is handled in the InstanceServiceClient block below.

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
                            # Network - single NIC with one public IP (current platform limitation)
                            # All VPN tunnels share this IP; peer differentiates by IKE/IPsec identifiers
                            "network_interfaces": [
                                {
                                    "name": "eth0",
                                    "ip_address": {},
                                    "public_ip_address": (
                                        {"allocation_id": alloc_ids[0], "static": True}
                                        if len(alloc_ids) > 0
                                        else {}
                                    ),
                                    "subnet_id": nic["subnet_id"],
                                }
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
                            IPAlias,
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

                        # Network interfaces - current platform limitation: 1 NIC with 1 public IP
                        # This supports multiple VPN tunnels on the same IP (differentiated by IKE identifiers)
                        # Future: multi-VM deployment with 1 IP per VM for tunnel-level redundancy
                        ni_msgs = []
                        pub = None
                        if len(alloc_ids) > 0:
                            pub = PublicIPAddress(allocation_id=alloc_ids[0], static=True)
                        ni_msgs.append(
                            NetworkInterfaceSpec(
                                name="eth0",
                                ip_address=IPAddress(),
                                public_ip_address=pub if pub is not None else PublicIPAddress(),
                                subnet_id=nic["subnet_id"],
                            )
                        )
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
                    ipv4_private_pools = None
                    # Build a minimal-compatible shape without mutating SDK message objects
                    if cidr_to_use:
                        ipv4_private_pools = {
                            "pools": [
                                {
                                    "cidrs": [
                                        {
                                            "cidr": cidr_to_use,
                                        }
                                    ]
                                }
                            ],
                            "use_network_pools": False,
                        }
                    else:
                        # Inherit network pools if computation failed; user can pre-create subnet
                        ipv4_private_pools = {"use_network_pools": True}
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

    def _build_cloud_init(self) -> str:
        """Return a basic cloud-init to install deps and unit file.

        This does not install the agent binary itself; it prepares the unit and
        directories so the orchestrator/ops can deploy the binary later. The unit
        is enabled (but not forcibly started) to avoid failing if the binary is
        not yet present.
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
        cloud = (
            "#cloud-config\n"
            "packages:\n"
            "  - strongswan\n"
            "  - frr\n"
            "write_files:\n"
            "  - path: /etc/systemd/system/nebius-vpngw-agent.service\n"
            "    permissions: \"0644\"\n"
            "    owner: root:root\n"
            "    content: |\n"
            f"{indented_unit}\n"
            "runcmd:\n"
            "  - [ bash, -lc, \"mkdir -p /etc/nebius-vpngw\" ]\n"
            "  - [ systemctl, daemon-reload ]\n"
            "  - [ systemctl, enable, nebius-vpngw-agent ]\n"
        )
        return cloud
