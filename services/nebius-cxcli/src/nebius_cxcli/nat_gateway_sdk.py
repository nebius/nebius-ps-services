"""Scoped SDK reads and delegated subnet attachment updates; no resource creation."""

from __future__ import annotations

import ipaddress
import math
import time
from collections.abc import Callable, Mapping
from typing import Any

from .nat_gateway import canonical_routes, fingerprint, policy_snapshot
from .nebius_api_helpers import bounded_nebius_request_kwargs, wait_nebius_operation
from .sdk_auth import init_nebius_sdk


class NatOperationFailed(RuntimeError):
    """The provider confirmed a terminal operation failure, rather than an unknown outcome."""


class NatGatewaySDK:
    def __init__(self, project_id: str, *, sdk: Any = None) -> None:
        self.project_id = project_id
        self.owned = sdk is None
        self.sdk = (
            sdk
            if sdk is not None
            else init_nebius_sdk(
                parent_id=project_id, context="NAT gateway", prefer_operator_auth=True
            )
        )

    def __enter__(self) -> NatGatewaySDK:
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.owned:
            self.sdk.sync_close()

    def call(self, method: Callable[..., Any], request: Any) -> Any:
        try:
            return method(request, **bounded_nebius_request_kwargs()).wait()
        except Exception as exc:
            raise RuntimeError(
                f"NAT provider request failed ({type(exc).__name__}); no empty-inventory fallback"
            ) from None

    def pages(
        self,
        method: Callable[..., Any],
        factory: Callable[[str], Any],
        *,
        deadline: float | None = None,
    ) -> list[Any]:
        result, token, seen, identities = [], "", set(), set()
        deadline = (
            min(deadline, time.monotonic() + 120)
            if deadline is not None
            else time.monotonic() + 120
        )
        for _ in range(1000):
            if time.monotonic() > deadline:
                raise RuntimeError("NAT inventory deadline exceeded")
            response = self.call(method, factory(token))
            for item in response.items:
                identity = item.metadata.id
                if not identity or identity in identities:
                    raise RuntimeError("NAT inventory has missing or duplicate identities")
                identities.add(identity)
                result.append(item)
                if len(result) > 100000:
                    raise RuntimeError("NAT inventory limit exceeded")
            token = response.next_page_token
            if not token:
                return result
            if token in seen:
                raise RuntimeError("Repeated NAT inventory continuation token")
            seen.add(token)
        raise RuntimeError("NAT inventory page limit exceeded")

    def network(self, network_id: str) -> Any:
        from nebius.api.nebius.vpc.v1 import GetNetworkRequest, NetworkServiceClient

        obj = self.call(NetworkServiceClient(self.sdk).get, GetNetworkRequest(id=network_id))
        if obj.metadata.id != network_id or obj.metadata.parent_id != self.project_id:
            raise ValueError("NAT network belongs to a different project")
        return obj

    def network_cidrs(self, network_id: str) -> list[str]:
        network = self.network(network_id)
        result = []
        for reference in network.spec.ipv4_private_pools.pools:
            result.extend(self.pool_cidrs(reference.id))
        if not result:
            raise ValueError("NAT network has no verified private address range")
        return sorted(set(result))

    def pool_cidrs(self, pool_id: str) -> list[str]:
        from nebius.api.nebius.vpc.v1 import GetPoolRequest, PoolServiceClient

        pool = self.call(PoolServiceClient(self.sdk).get, GetPoolRequest(id=pool_id))
        if pool.spec.visibility.name != "PRIVATE" or pool.spec.version.name != "IPV4":
            raise ValueError("NAT network has an invalid private pool")
        if pool.status.state.name != "READY" or not pool.status.cidrs:
            raise RuntimeError("NAT private pool is not ready or its ranges are unresolved")
        return [str(ipaddress.IPv4Network(cidr, strict=True)) for cidr in pool.status.cidrs]

    def subnet(self, subnet_id: str, network_id: str) -> Any:
        from nebius.api.nebius.vpc.v1 import GetSubnetRequest, SubnetServiceClient

        obj = self.call(SubnetServiceClient(self.sdk).get, GetSubnetRequest(id=subnet_id))
        if (
            obj.metadata.id != subnet_id
            or obj.metadata.parent_id != self.project_id
            or obj.spec.network_id != network_id
        ):
            raise ValueError("NAT subnet identity, project or network differs")
        if not obj.status.route_table.id:
            raise RuntimeError("NAT subnet effective route table is unresolved")
        return obj

    def subnets(self, network_id: str) -> list[Any]:
        from nebius.api.nebius.vpc.v1 import ListSubnetsByNetworkRequest, SubnetServiceClient

        self.network(network_id)
        return self.pages(
            SubnetServiceClient(self.sdk).list_by_network,
            lambda token: ListSubnetsByNetworkRequest(
                network_id=network_id, page_size=500, page_token=token
            ),
        )

    def subnet_info(self, subnet_id: str, network_id: str) -> dict[str, Any]:
        """Describe public allocation capability separately from effective routing."""
        subnet = self.subnet(subnet_id, network_id)
        policy = self.route_policy(subnet.status.route_table.id, network_id)
        direct = any(
            route["destination_cidr"] == "0.0.0.0/0" and route["default_egress_gateway"]
            for route in policy["routes"]
        )
        private = [cidr for pool in subnet.status.ipv4_private_pools for cidr in pool.cidrs]
        public = any(pool.cidrs for pool in subnet.status.ipv4_public_pools)
        reasons = []
        if subnet.status.state.name != "READY":
            reasons.append("subnet is not ready")
        if not private:
            reasons.append("private address ranges are unresolved")
        if not public:
            reasons.append("public IP allocation is unavailable")
        if not direct:
            reasons.append("no direct default Internet route")
        return {
            "id": subnet.metadata.id,
            "name": subnet.metadata.name,
            "private_cidrs": private,
            "public_ip_capable": public,
            "direct_egress": direct,
            "policy": policy,
            "reason": "; ".join(reasons),
        }

    def reusable_subnet(self, subnet_id: str, network_id: str) -> dict[str, Any]:
        info = self.subnet_info(subnet_id, network_id)
        if info["reason"]:
            raise ValueError("Gateway subnet cannot be reused: " + info["reason"])
        return info

    def route_policy(self, table_id: str, network_id: str) -> dict[str, Any]:
        from nebius.api.nebius.vpc.v1 import (
            GetRouteTableRequest,
            ListRoutesRequest,
            RouteServiceClient,
            RouteTableServiceClient,
        )

        table = self.call(RouteTableServiceClient(self.sdk).get, GetRouteTableRequest(id=table_id))
        if (
            table.metadata.id != table_id
            or table.metadata.parent_id != self.project_id
            or table.spec.network_id != network_id
        ):
            raise ValueError("NAT route table is outside the selected project/network")
        routes = self.pages(
            RouteServiceClient(self.sdk).list,
            lambda token: ListRoutesRequest(parent_id=table_id, page_size=500, page_token=token),
        )
        projection = []
        for route in routes:
            if route.metadata.parent_id != table_id:
                raise ValueError("Route listing escaped the selected table")
            hop = route.spec.next_hop
            projection.append(
                {
                    "destination_cidr": route.spec.destination.cidr,
                    "allocation_id": hop.allocation.id if hop.allocation is not None else "",
                    "default_egress_gateway": bool(hop.default_egress_gateway),
                    "type": route.status.type.name,
                }
            )
        return policy_snapshot(table_id, projection)

    def check_source_policy(self, policy: Mapping[str, Any], network_id: str) -> None:
        table_id = (
            policy["source_table_id"] or self.network(network_id).status.default_route_table_id
        )
        current = self.route_policy(table_id, network_id)
        if current["source_fingerprint"] != policy["source_fingerprint"]:
            raise RuntimeError("NAT source routing changed; explicitly refresh and review routes")

    def workload_policy(
        self, table_id: str, network_id: str, policy: Mapping[str, Any], allocation_id: str
    ) -> None:
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient, GetAllocationRequest

        expected = [route for route in policy["routes"] if route["destination_cidr"] != "0.0.0.0/0"]
        expected.append({"destination_cidr": "0.0.0.0/0", "allocation_id": allocation_id})
        if self.route_policy(table_id, network_id)["source_fingerprint"] != fingerprint(
            canonical_routes(expected)
        ):
            raise RuntimeError("NAT workload table differs from its reviewed routing policy")
        for route in expected:
            allocation = self.call(
                AllocationServiceClient(self.sdk).get,
                GetAllocationRequest(id=route["allocation_id"]),
            )
            assignment = allocation.status.assignment.network_interface
            if (
                allocation.metadata.parent_id != self.project_id
                or not allocation.spec.check_presence("ipv4_private")
                or not allocation.status.details.allocated_cidr
                or ipaddress.IPv4Network(allocation.status.details.allocated_cidr).prefixlen != 32
                or assignment is None
                or not assignment.instance_id
            ):
                raise RuntimeError(
                    "NAT route next hop is not an assigned private IPv4 host allocation"
                )
            self.subnet(allocation.status.details.subnet_id, network_id)

    def attach(
        self,
        subnet_id: str,
        network_id: str,
        *,
        expected: str,
        desired: str,
        assert_held: Callable[[], None],
        record_operation: Callable[[str], None],
    ) -> None:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.vpc.v1 import SubnetServiceClient, SubnetSpec, UpdateSubnetRequest

        current = self.subnet(subnet_id, network_id)
        if current.spec.route_table_id == desired:
            return
        if current.spec.route_table_id != expected:
            raise RuntimeError("NAT subnet attachment changed outside this operation")
        spec = SubnetSpec(current.spec)
        spec.route_table_id = desired
        assert_held()
        operation = self.call(
            SubnetServiceClient(self.sdk).update,
            UpdateSubnetRequest(metadata=ResourceMetadata(current.metadata), spec=spec),
        )
        record_operation(operation.id)
        wait_nebius_operation(operation, timeout_seconds=300, action="NAT route attachment")
        observed = self.subnet(subnet_id, network_id)
        if observed.spec.route_table_id != desired or (
            desired and observed.status.route_table.id != desired
        ):
            raise RuntimeError("NAT route attachment has not converged")

    def wait_operation(self, operation_id: str) -> None:
        from nebius.api.nebius.common.v1 import GetOperationRequest, OperationServiceClient

        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            operation = self.call(
                OperationServiceClient(self.sdk).get, GetOperationRequest(id=operation_id)
            )
            if operation.check_presence("finished_at"):
                if operation.status.code.name != "OK":
                    raise NatOperationFailed(
                        "NAT attachment operation failed; inspect its terminal outcome"
                    )
                return
            time.sleep(2)
        raise RuntimeError("NAT attachment operation is still pending")

    def address_pool(self, pool_id: str, *, deadline: float | None = None) -> Any:
        from .nat_gateway_address_inventory import read_address_pool

        return read_address_pool(self, pool_id, deadline=deadline)

    def require_private_capacity(self, subnet_id: str, network_id: str) -> None:
        from .nat_gateway_address_space import AddressDemand, assess_address_space

        subnet = self.subnet(subnet_id, network_id)
        pools = tuple(pool.pool_id for pool in subnet.status.ipv4_private_pools)
        if not pools:
            raise RuntimeError("Subnet private allocation capacity is unresolved")
        assess_address_space(
            tuple(self.address_pool(pool) for pool in pools),
            (AddressDemand("temporary-private-allocation", pools, "/32"),),
        ).require_feasible()

    def preset_resources(self, platform: str, preset: str) -> dict[str, int]:
        from nebius.api.nebius.common.v1 import GetByNameRequest
        from nebius.api.nebius.compute.v1 import PlatformServiceClient

        obj = self.call(
            PlatformServiceClient(self.sdk).get_by_name,
            GetByNameRequest(parent_id=self.project_id, name=platform),
        )
        rows = [p.resources for p in obj.spec.presets if p.name == preset]
        if len(rows) != 1 or rows[0].vcpu_count <= 0:
            raise ValueError("A currently available compute platform/preset is required")
        return {
            "vcpu_count": rows[0].vcpu_count,
            "memory_gibibytes": rows[0].memory_gibibytes,
            "gpu_count": rows[0].gpu_count,
        }

    def cpu_preset(self, platform: str, preset: str) -> dict[str, int]:
        resources = self.preset_resources(platform, preset)
        if resources["gpu_count"] != 0:
            raise ValueError("NAT requires a currently available CPU-only platform/preset")
        return resources

    def image(self, image_id: str, *, platform: str, preset: str) -> Any:
        from nebius.api.nebius.compute.v1 import GetImageRequest, ImageServiceClient

        image = self.call(ImageServiceClient(self.sdk).get, GetImageRequest(id=image_id))
        if (
            image.metadata.id != image_id
            or image.status.state.name != "READY"
            or platform in image.spec.unsupported_platforms
        ):
            raise ValueError("NAT image is not ready or is incompatible with the platform")
        if any(
            p.platform == platform and p.preset == preset for p in image.spec.unsupported_presets
        ):
            raise ValueError("NAT image is incompatible with the selected preset")
        if image.spec.image_family != "ubuntu24.04-driverless":
            raise ValueError(
                "NAT guest bootstrap requires the Ubuntu 24.04 driverless image family"
            )
        return image

    def image_choices(self, region: str, platform: str, preset: str) -> list[dict[str, Any]]:
        from nebius.api.nebius.compute.v1 import ImageServiceClient, ListPublicRequest

        images = self.pages(
            ImageServiceClient(self.sdk).list_public,
            lambda token: ListPublicRequest(region=region, page_size=500, page_token=token),
        )
        return sorted(
            [
                {
                    "id": image.metadata.id,
                    "name": image.metadata.name,
                    "minimum_gib": math.ceil(image.status.min_disk_size_bytes / 1024**3),
                }
                for image in images
                if image.spec.image_family == "ubuntu24.04-driverless"
                and image.status.state.name == "READY"
                and platform not in image.spec.unsupported_platforms
                and not any(
                    item.platform == platform and item.preset == preset
                    for item in image.spec.unsupported_presets
                )
            ],
            key=lambda row: row["name"],
            reverse=True,
        )

    def gateway(self, outputs: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
        from nebius.api.nebius.compute.v1 import GetInstanceRequest, InstanceServiceClient
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient, GetAllocationRequest

        instance = self.call(
            InstanceServiceClient(self.sdk).get, GetInstanceRequest(id=outputs["instance_id"])
        )
        if (
            instance.metadata.parent_id != self.project_id
            or instance.status.state.name != "RUNNING"
        ):
            raise RuntimeError("NAT gateway VM is not running in the selected project")
        if (
            instance.spec.resources.platform != inputs["platform"]
            or instance.spec.resources.preset != inputs["preset"]
        ):
            raise RuntimeError("NAT gateway live compute shape differs from intent")
        if len(instance.spec.network_interfaces) != 1:
            raise RuntimeError("NAT gateway must have one interface")
        nic = instance.spec.network_interfaces[0]
        if (
            nic.subnet_id != outputs["subnet_id"]
            or nic.ip_address.allocation_id != outputs["private_allocation_id"]
            or nic.public_ip_address.allocation_id != outputs["public_allocation_id"]
        ):
            raise RuntimeError("NAT gateway allocation bindings differ from Terraform outputs")
        private_allocation = None
        for key in ("private_allocation_id", "public_allocation_id"):
            allocation = self.call(
                AllocationServiceClient(self.sdk).get, GetAllocationRequest(id=outputs[key])
            )
            if key == "private_allocation_id":
                private_allocation = allocation
            assignment = allocation.status.assignment.network_interface
            if (
                allocation.metadata.id != outputs[key]
                or allocation.metadata.parent_id != self.project_id
                or assignment is None
                or assignment.instance_id != outputs["instance_id"]
            ):
                raise RuntimeError("NAT allocation is not attached to the expected gateway")
        subnet = self.subnet(outputs["subnet_id"], inputs["network_id"])
        reuse = inputs.get("gateway_subnet_mode", "create") == "reuse"
        if subnet.status.route_table.id != outputs["gateway_route_table_id"] or (
            not reuse
            and (
                not subnet.spec.check_presence("ipv4_private_pools")
                or subnet.spec.ipv4_private_pools.use_network_pools
            )
        ):
            raise RuntimeError("Gateway subnet isolation or direct egress differs from intent")
        if reuse:
            if subnet.metadata.id != inputs.get("gateway_subnet_id"):
                raise RuntimeError("Reused gateway subnet differs from intent")
            info = self.reusable_subnet(subnet.metadata.id, inputs["network_id"])
            if any(
                route["allocation_id"] == outputs["private_allocation_id"]
                for route in info["policy"]["routes"]
            ):
                raise RuntimeError("Gateway subnet contains a route to its own NAT allocation")
        from .nat_gateway_address_inventory import verify_gateway_address

        verify_gateway_address(self, subnet, inputs, private_allocation)
        expected = canonical_routes(
            [{"destination_cidr": "0.0.0.0/0", "default_egress_gateway": True}]
        )
        if not reuse and self.route_policy(outputs["gateway_route_table_id"], inputs["network_id"])[
            "source_fingerprint"
        ] != fingerprint(expected):
            raise RuntimeError("Gateway direct egress route differs from intent")
        return {"instance_id": instance.metadata.id, "state": "RUNNING", "subnet_id": nic.subnet_id}
