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
        # Best-effort SDK wiring; continue to log-only if not present
        route_api = None
        try:
            import nebius.sdk as sdk  # type: ignore

            client = sdk.SDK()
            vpc = getattr(client, "vpc", None)
            if vpc:
                vpc_client = vpc()
                route_api = getattr(vpc_client, "route", None) or getattr(vpc_client, "routes", None)
        except Exception as e:
            print(f"[RouteManager] SDK not available; logging only: {e}")
            route_api = None

        import yaml
        for inst in plan.iter_instance_configs():
            try:
                cfg = yaml.safe_load(inst.config_yaml) or {}
            except Exception:
                cfg = {}
            for conn in cfg.get("connections", []):
                mode = conn.get("routing_mode") or (cfg.get("defaults", {}).get("routing", {}).get("mode") or "bgp")
                if mode != "static":
                    continue
                for tun in conn.get("tunnels", []):
                    if tun.get("ha_role", "active") != "active":
                        continue
                    prefixes = ((tun.get("static_routes") or {}).get("remote_prefixes") or [])
                    for pfx in prefixes:
                        # Use external_ip if available, otherwise show hostname
                        next_hop = inst.external_ip or f"gateway:{inst.hostname}"
                        if route_api and hasattr(route_api, "ensure"):
                            try:
                                # Placeholder ensure signature; adapt to real SDK
                                route_api.ensure(destination=pfx, next_hop=next_hop, project_id=self.project_id)
                                print(f"[RouteManager] Ensured route {pfx} -> {next_hop}")
                            except Exception as e:
                                print(f"[RouteManager] ensure failed for {pfx}: {e}")
                        else:
                            print(f"[RouteManager] Would ensure route {pfx} -> {next_hop}")
