"""Private control plane and fixed node-group request builders; no version/shape guesses."""


def cluster_request(
    *, project_id: str, name: str, version: str, subnet_id: str, service_cidr: str
):
    import ipaddress

    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.mk8s.v1 import (
        ClusterSpec,
        ControlPlaneSpec,
        CreateClusterRequest,
        KubeNetworkSpec,
    )

    ipaddress.ip_network(service_cidr, strict=True)
    if not all([project_id, name, version, subnet_id]):
        raise ValueError("project, name, version and subnet required")
    return CreateClusterRequest(
        metadata=ResourceMetadata(parent_id=project_id, name=name),
        spec=ClusterSpec(
            control_plane=ControlPlaneSpec(version=version, subnet_id=subnet_id),
            kube_network=KubeNetworkSpec(service_cidrs=[service_cidr]),
        ),
    )


def node_group_request(
    *, cluster_id: str, name: str, version: str, count: int, template
):
    """Pass a NodeTemplate validated against the live compatibility matrix and subnet inventory."""
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.mk8s.v1 import CreateNodeGroupRequest, NodeGroupSpec

    if count < 1 or not all([cluster_id, name, version]):
        raise ValueError("positive count and exact cluster/name/version required")
    if (
        not template.resources.platform
        or not template.resources.preset
        or not template.network_interfaces
    ):
        raise ValueError("template needs an explicit shape and network interfaces")
    return CreateNodeGroupRequest(
        metadata=ResourceMetadata(parent_id=cluster_id, name=name),
        spec=NodeGroupSpec(version=version, fixed_node_count=count, template=template),
    )
