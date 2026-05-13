# Public Cloud Patterns

## Goal

Use these patterns when recommending Nebius VPC console or API behavior.

## Address Space Modeling

### AWS Address Space

- A VPC can have multiple CIDR blocks.
- Each subnet has its own explicit subnet CIDR from the VPC address space.
- VPC address space and subnet address space are represented separately.

### Azure Address Space

- A VNet has an address space.
- Each subnet has explicit prefixes inside that VNet address space.
- Azure does not present the whole VNet space as if it were subnet-owned.

### GCP Address Space

- The VPC is the container.
- Subnets own explicit primary and optional secondary ranges.
- Network/container scope and subnet-owned ranges are represented separately.

## Gateway Placement Pattern

### Azure Gateway Placement

- Managed VPN gateway uses a dedicated `GatewaySubnet`.
- It is explicitly separate from workload subnets.

### AWS Gateway Placement

- Site-to-Site VPN terminates on edge resources such as VGW or TGW.
- It is not placed inside workload subnets.

### GCP Gateway Placement

- HA VPN is a VPC-attached regional resource, not a workload subnet VM.

## Recommendation For Nebius

Nebius should represent three different concepts separately:

- network address space
- subnet-owned address space
- inherited allocation mode

Do not flatten those concepts into one subnet CIDR presentation.
