"""VPC builders; live pool containment, overlaps, allocation and ownership checks come first."""

import ipaddress


def network_request(*, project_id: str, name: str, private_pool_id: str):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import (
        CreateNetworkRequest,
        IPv4PrivateNetworkPools,
        NetworkPool,
        NetworkSpec,
    )

    if not all([project_id, name, private_pool_id]):
        raise ValueError("project/name and verified unassigned private pool required")
    return CreateNetworkRequest(
        metadata=ResourceMetadata(parent_id=project_id, name=name),
        spec=NetworkSpec(
            ipv4_private_pools=IPv4PrivateNetworkPools(
                pools=[NetworkPool(id=private_pool_id)]
            )
        ),
    )


def subnet_request(*, project_id: str, name: str, network_id: str, cidr: str):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import (
        CreateSubnetRequest,
        IPv4PrivateSubnetPools,
        SubnetCidr,
        SubnetPool,
        SubnetSpec,
    )

    if ipaddress.ip_network(cidr, strict=True).version != 4:
        raise ValueError("an explicit IPv4 child CIDR is required")
    return CreateSubnetRequest(
        metadata=ResourceMetadata(parent_id=project_id, name=name),
        spec=SubnetSpec(
            network_id=network_id,
            ipv4_private_pools=IPv4PrivateSubnetPools(
                use_network_pools=False,
                pools=[SubnetPool(cidrs=[SubnetCidr(cidr=cidr)])],
            ),
        ),
    )


def security_group_request(*, project_id: str, name: str, network_id: str):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import CreateSecurityGroupRequest, SecurityGroupSpec

    return CreateSecurityGroupRequest(
        metadata=ResourceMetadata(parent_id=project_id, name=name),
        spec=SecurityGroupSpec(network_id=network_id),
    )


def ingress_rule_request(*, group_id: str, name: str, source_cidr: str, port: int):
    """Create a scoped TCP allow rule; a rule does not attach its group to a VM."""
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc.v1 import (
        CreateSecurityRuleRequest,
        RuleAccessAction,
        RuleIngress,
        RuleProtocol,
        RuleType,
        SecurityRuleSpec,
    )

    network = ipaddress.ip_network(source_cidr, strict=True)
    if network.version != 4 or network.prefixlen == 0 or not 1 <= port <= 65535:
        raise ValueError("scoped IPv4 source and valid TCP port required")
    return CreateSecurityRuleRequest(
        metadata=ResourceMetadata(parent_id=group_id, name=name),
        spec=SecurityRuleSpec(
            access=RuleAccessAction.ALLOW,
            protocol=RuleProtocol.TCP,
            type=RuleType.STATEFUL,
            priority=500,
            ingress=RuleIngress(source_cidrs=[source_cidr], destination_ports=[port]),
        ),
    )
