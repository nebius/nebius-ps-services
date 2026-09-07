"""Private VM request builder. Resolve exact shape, disk, subnet and security group first."""


def instance_request(
    *,
    project_id: str,
    name: str,
    platform: str,
    preset: str,
    disk_id: str,
    subnet_id: str,
    security_group_id: str,
    cloud_init: str,
):
    """cloud_init contains public SSH keys/configuration only, never credentials."""
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.compute.v1 import (
        AttachedDiskSpec,
        CreateInstanceRequest,
        ExistingDisk,
        InstanceSpec,
        IPAddress,
        NetworkInterfaceSpec,
        ResourcesSpec,
        SecurityGroup,
    )

    if not all(
        [project_id, name, platform, preset, disk_id, subnet_id, security_group_id]
    ):
        raise ValueError("exact project, shape and attachment identifiers required")
    return CreateInstanceRequest(
        metadata=ResourceMetadata(parent_id=project_id, name=name),
        spec=InstanceSpec(
            resources=ResourcesSpec(platform=platform, preset=preset),
            boot_disk=AttachedDiskSpec(
                existing_disk=ExistingDisk(id=disk_id),
                attach_mode=AttachedDiskSpec.AttachMode.READ_WRITE,
                device_id="boot",
            ),
            network_interfaces=[
                NetworkInterfaceSpec(
                    name="eth0",
                    subnet_id=subnet_id,
                    ip_address=IPAddress(),
                    security_groups=[SecurityGroup(id=security_group_id)],
                )
            ],
            cloud_init_user_data=cloud_init,
        ),
    )
