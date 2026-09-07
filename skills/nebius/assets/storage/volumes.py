"""Standalone durable volumes. Deletion protection is explicit and enabled."""


def disk_request(*, project_id: str, name: str, size_gib: int, image_id: str):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import CreateDiskRequest, DiskSpec

    if size_gib < 1 or not all([project_id, name, image_id]):
        raise ValueError("positive size, project/name and resolved image ID required")
    return CreateDiskRequest(
        metadata=ResourceMetadata(parent_id=project_id, name=name),
        spec=DiskSpec(
            size_gibibytes=size_gib,
            type=DiskSpec.DiskType.NETWORK_SSD,
            source_image_id=image_id,
            forbid_deletion=True,
        ),
    )


def filesystem_request(*, project_id: str, name: str, size_gib: int):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import CreateFilesystemRequest, FilesystemSpec

    if size_gib < 1 or not project_id or not name:
        raise ValueError("positive size and project/name required")
    return CreateFilesystemRequest(
        metadata=ResourceMetadata(parent_id=project_id, name=name),
        spec=FilesystemSpec(
            size_gibibytes=size_gib,
            type=FilesystemSpec.FilesystemType.NETWORK_SSD,
            forbid_deletion=True,
        ),
    )
