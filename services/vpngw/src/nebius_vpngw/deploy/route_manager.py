from __future__ import annotations

from ..config_loader import ResolvedDeploymentPlan


class RouteManager:
    def __init__(self, project_id: str | None) -> None:
        self.project_id = project_id

    def reconcile(self, plan: ResolvedDeploymentPlan) -> None:
        """Reconcile VPC routes for remote prefixes to point at active gateway VM.

        Pseudocode for Nebius SDK:
        - rt_client = RouteTableClient(auth=...)
        - for each connection/tunnel in plan where routing_mode == static:
            - for each remote_prefix in static_routes.remote_prefixes:
                - find route in relevant table, ensure next_hop.allocation_id -> active gateway
        - for bgp mode, skip (dynamic on FRR and cloud side)
        """
        print("[RouteManager] Reconcile routes")
        for inst in plan.iter_instance_configs():
            # The per-instance YAML is serialized; parse to inspect static routes if needed
            # For scaffold, just log
            print(f"[RouteManager] Instance {inst.instance_index} routes: idempotent ensure")
