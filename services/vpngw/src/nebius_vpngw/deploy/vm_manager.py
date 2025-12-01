from __future__ import annotations

import typing as t

from ..config_loader import GatewayGroupSpec, InstanceResolvedConfig


class VMManager:
    """Manage Nebius gateway VM lifecycle.

    This is a scaffold placeholder. Integrate with Nebius Python SDK when available.
    """

    def __init__(self, project_id: t.Optional[str], zone: t.Optional[str]) -> None:
        self.project_id = project_id
        self.zone = zone

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
        # Example placeholder create
        for i in range(spec.instance_count):
            inst_name = f"{spec.name}-{i}"
            pub_ip = spec.external_ips[i] if i < len(spec.external_ips) else None
            print(f"[VMManager] ensure instance {inst_name} pub_ip={pub_ip} platform={spec.vm_spec.get('platform')}")

    def get_instance_ssh_target(self, instance_index: int) -> str:
        # TODO: Return reachable SSH hostname/IP. For now, use name placeholder.
        return f"{instance_index}"
