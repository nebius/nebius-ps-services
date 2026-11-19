"""Complete, secret-free HA identities for composed rendering tests."""

from nebius_vpngw.schema import VMHARouteTarget, VMHARuntimeBinding


def runtime_ha(cluster_id="cluster"):
    targets = (
        VMHARouteTarget(
            project_id="project",
            network_id="network",
            workload_subnet_id="subnet",
            route_table_id="routes",
        ),
    )
    binding = VMHARuntimeBinding(
        cluster_id=cluster_id,
        shared_allocation_id="allocation",
        route_targets=targets,
        route_runtime_id=VMHARuntimeBinding.derive_route_runtime_id(
            cluster_id, "allocation", targets
        ),
        generation_id="a" * 64,
        configuration_digest="a" * 64,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
        nodes=tuple(
            {
                "node_id": f"node-{name}",
                "role": role,
                "compute_id": f"compute-{name}",
                "network_interface_name": "eth0",
                "peer_endpoint": f"192.0.2.{index + 20}:9443",
            }
            for index, (name, role) in enumerate((("a", "active"), ("b", "passive")))
        ),
    )
    return {
        "cluster_id": cluster_id,
        "node": {"node_id": "node-a"},
        "runtime_binding": binding.model_dump(mode="json"),
    }
