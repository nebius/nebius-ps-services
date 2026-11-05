"""Read-only customer Security Group evidence for creation and replacement.

Compute 0.3.40 exposes explicit NIC references. Effective default membership is
resolved through the VPC network's unique default group; an empty specification
never means an empty firewall. No method here changes groups or their rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..nebius_pagination import collect_nebius_pages, nebius_resource_id


@dataclass(frozen=True)
class NICSecurityGroups:
    name: str
    subnet_id: str
    network_id: str
    explicit_ids: tuple[str, ...]
    effective_ids: tuple[str, ...]

    @property
    def create_ids(self) -> tuple[str, ...]:
        # Freeze even implicit default membership as explicit references on the
        # candidate. A network default change cannot silently change its policy.
        return self.effective_ids


def _ids(values: Any) -> tuple[str, ...]:
    identifiers = [str(getattr(value, "id", "") or "") for value in values]
    if any(not identifier for identifier in identifiers) or len(set(identifiers)) != len(
        identifiers
    ):
        raise ValueError("Security Group membership contains missing or duplicate IDs")
    return tuple(sorted(identifiers))


def validate_groups(client: Any, network_id: str, identifiers: tuple[str, ...]) -> None:
    from nebius.api.nebius.vpc.v1 import GetSecurityGroupRequest, SecurityGroupServiceClient

    service = SecurityGroupServiceClient(client)
    for identifier in identifiers:
        group = service.get(GetSecurityGroupRequest(id=identifier)).wait()
        if nebius_resource_id(group) != identifier or group.spec.network_id != network_id:
            raise ValueError("Customer Security Group is missing or belongs to a different VPC")
        if getattr(group.status.state, "name", str(group.status.state)) != "READY":
            raise ValueError("Customer Security Group is not ready")


def capture(client: Any, instance: Any) -> tuple[NICSecurityGroups, ...]:
    """Capture the complete live per-NIC membership before replacement."""
    from nebius.api.nebius.vpc.v1 import (
        GetSubnetRequest,
        ListSecurityGroupsByNetworkRequest,
        SecurityGroupServiceClient,
        SubnetServiceClient,
    )

    interfaces = list(instance.spec.network_interfaces)
    if not interfaces or len({nic.name for nic in interfaces}) != len(interfaces):
        raise ValueError("Cannot establish unique live NIC identities for Security Groups")
    subnets = SubnetServiceClient(client)
    groups = SecurityGroupServiceClient(client)
    result = []
    for nic in interfaces:
        if not nic.name or not nic.subnet_id:
            raise ValueError("Cannot establish live NIC subnet identity")
        subnet = subnets.get(GetSubnetRequest(id=nic.subnet_id)).wait()
        if nebius_resource_id(subnet) != nic.subnet_id or not subnet.spec.network_id:
            raise ValueError("Cannot establish live NIC VPC identity")
        network_id = subnet.spec.network_id
        explicit = _ids(nic.security_groups)
        effective = explicit
        if not effective:

            def collect_groups(network: str) -> Any:
                return collect_nebius_pages(
                    lambda token: groups.list_by_network(
                        ListSecurityGroupsByNetworkRequest(
                            network_id=network, page_token=token, page_size=100
                        )
                    ),
                    context="Default Security Group",
                    item_identity=nebius_resource_id,
                )

            inventory = collect_groups(network_id)
            defaults = [group for group in inventory if group.status.default]
            if len(defaults) != 1:
                raise ValueError("Cannot establish the unique default Security Group")
            default_id = nebius_resource_id(defaults[0])
            if not default_id:
                raise ValueError("Default Security Group has no resource identity")
            effective = (default_id,)
        validate_groups(client, network_id, effective)
        result.append(NICSecurityGroups(nic.name, nic.subnet_id, network_id, explicit, effective))
    return tuple(sorted(result, key=lambda item: item.name))


def require_unchanged(client: Any, instance: Any, expected: tuple[NICSecurityGroups, ...]) -> None:
    if capture(client, instance) != expected:
        raise RuntimeError("Customer Security Group membership changed; replacement is blocked")


def verify_candidate(client: Any, instance: Any, expected: tuple[NICSecurityGroups, ...]) -> None:
    observed = capture(client, instance)
    wanted = tuple(
        NICSecurityGroups(
            item.name, item.subnet_id, item.network_id, item.create_ids, item.effective_ids
        )
        for item in expected
    )
    if observed != wanted:
        raise RuntimeError("Replacement Security Groups differ from the captured customer policy")
