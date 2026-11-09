"""Customer Security Group evidence and desired attachment policy.

Compute 0.3.40 exposes explicit NIC references. Effective default membership is
resolved through the VPC network's unique default group; an empty specification
never means an empty firewall. No method here changes groups or their rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from ..nebius_pagination import collect_nebius_pages, nebius_resource_id
from .vm_ha_cloud import vm_ha_request_kwargs


@dataclass(frozen=True)
class NICSecurityGroups:
    name: str
    subnet_id: str
    network_id: str
    explicit_ids: tuple[str, ...]
    effective_ids: tuple[str, ...]

    @property
    def create_ids(self) -> tuple[str, ...]:
        return self.explicit_ids


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
        group = service.get(GetSecurityGroupRequest(id=identifier), **vm_ha_request_kwargs()).wait()
        if nebius_resource_id(group) != identifier or group.spec.network_id != network_id:
            raise ValueError("Customer Security Group is missing or belongs to a different VPC")
        if getattr(group.status.state, "name", str(group.status.state)) != "READY":
            raise ValueError("Customer Security Group is not ready")


def effective_ids(client: Any, network_id: str, identifiers: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve one explicit or default policy, including before subnet creation."""
    from nebius.api.nebius.vpc.v1 import (
        ListSecurityGroupsByNetworkRequest,
        SecurityGroupServiceClient,
    )

    effective = identifiers
    if not effective:
        groups = SecurityGroupServiceClient(client)
        inventory = collect_nebius_pages(
            lambda token: groups.list_by_network(
                ListSecurityGroupsByNetworkRequest(
                    network_id=network_id, page_token=token, page_size=100
                ),
                **vm_ha_request_kwargs(),
            ),
            context="Default Security Group",
            item_identity=nebius_resource_id,
        )
        defaults = [group for group in inventory if cast(Any, group).status.default]
        if len(defaults) != 1:
            raise ValueError("Cannot establish the unique default Security Group")
        default_id = nebius_resource_id(defaults[0])
        if not default_id:
            raise ValueError("Default Security Group has no resource identity")
        effective = (default_id,)
    validate_groups(client, network_id, effective)
    return effective


def capture(client: Any, instance: Any) -> tuple[NICSecurityGroups, ...]:
    """Capture the complete live per-NIC membership before replacement."""
    from nebius.api.nebius.vpc.v1 import GetSubnetRequest, SubnetServiceClient

    interfaces = list(instance.spec.network_interfaces)
    if not interfaces or len({nic.name for nic in interfaces}) != len(interfaces):
        raise ValueError("Cannot establish unique live NIC identities for Security Groups")
    subnets = SubnetServiceClient(client)
    result = []
    for nic in interfaces:
        if not nic.name or not nic.subnet_id:
            raise ValueError("Cannot establish live NIC subnet identity")
        subnet = subnets.get(GetSubnetRequest(id=nic.subnet_id), **vm_ha_request_kwargs()).wait()
        if nebius_resource_id(subnet) != nic.subnet_id or not subnet.spec.network_id:
            raise ValueError("Cannot establish live NIC VPC identity")
        network_id = subnet.spec.network_id
        explicit = _ids(nic.security_groups)
        effective = effective_ids(client, network_id, explicit)
        result.append(NICSecurityGroups(nic.name, nic.subnet_id, network_id, explicit, effective))
    return tuple(sorted(result, key=lambda item: item.name))


def desired(
    client: Any, instance: Any, identifiers: tuple[str, ...]
) -> tuple[NICSecurityGroups, ...]:
    """Resolve desired explicit/default policy without changing any SDK object."""
    from types import SimpleNamespace

    if len(set(identifiers)) != len(identifiers) or len(identifiers) > 8:
        raise ValueError("Desired Security Groups must contain at most eight unique IDs")
    nics = [
        SimpleNamespace(
            name=nic.name,
            subnet_id=nic.subnet_id,
            security_groups=[SimpleNamespace(id=value) for value in identifiers],
        )
        for nic in instance.spec.network_interfaces
    ]
    return capture(client, SimpleNamespace(spec=SimpleNamespace(network_interfaces=nics)))


def require_unchanged(client: Any, instance: Any, expected: tuple[NICSecurityGroups, ...]) -> None:
    if capture(client, instance) != expected:
        raise RuntimeError("Customer Security Group membership changed; replacement is blocked")


def verify_desired(client: Any, instance: Any, identifiers: tuple[str, ...]) -> None:
    verify_candidate(client, instance, desired(client, instance, identifiers))


def verify_candidate(client: Any, instance: Any, expected: tuple[NICSecurityGroups, ...]) -> None:
    observed = capture(client, instance)
    wanted = tuple(
        NICSecurityGroups(
            item.name, item.subnet_id, item.network_id, item.create_ids, item.effective_ids
        )
        for item in expected
    )
    if observed != wanted:
        raise RuntimeError("Gateway Security Groups differ from the desired customer policy")
