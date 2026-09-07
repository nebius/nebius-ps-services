"""Registry and private PostgreSQL builders; PostgreSQL credentials stay caller-owned."""


def registry_request(*, project_id: str, name: str):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.registry.v1 import CreateRegistryRequest, RegistrySpec

    return CreateRegistryRequest(
        metadata=ResourceMetadata(parent_id=project_id, name=name),
        spec=RegistrySpec(description="Application images"),
    )


def postgresql_request(
    *, project_id: str, name: str, network_id: str, config, bootstrap, backup
):
    """Pass researched ConfigSpec/BootstrapSpec/BackupSpec; never print the resulting request."""
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.msp.postgresql.v1alpha1 import (
        ClusterSpec,
        CreateClusterRequest,
    )

    if config.public_access:
        raise ValueError("this example requires private database access")
    if (
        not config.version
        or not backup.retention_policy
        or not backup.backup_window_start
    ):
        raise ValueError(
            "explicit database version and immutable backup settings required"
        )
    return CreateClusterRequest(
        metadata=ResourceMetadata(parent_id=project_id, name=name),
        spec=ClusterSpec(
            network_id=network_id, config=config, bootstrap=bootstrap, backup=backup
        ),
    )
