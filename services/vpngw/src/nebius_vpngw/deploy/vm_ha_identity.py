"""Read-only evidence for classifying a previous VM-HA deployment.

Allocation names are only candidate selectors.  Classification requires a
private allocation, its exact Compute/NIC attachment, and matching
product-owned member records persisted in both Compute specifications.
"""

from __future__ import annotations

import json
import re
import typing as t
from dataclasses import dataclass

from ..config_loader import GatewayGroupSpec
from ..schema import VMHARole
from .vm_ha_cloud import HACloudError, allocation_observation

PROVISIONING_MARKER_PREFIX = "# nebius-vpngw-vm-ha-provisioning-v1: "
PROVISIONING_MARKER_SCHEMA = "nebius-vpngw/vm-ha-provisioning-v1"

_IdentifierRecord = tuple[int, str, str, str]
_Topology = tuple[_IdentifierRecord, _IdentifierRecord]
_MemberIdentity = tuple[str, str, str, str, str]


@dataclass(frozen=True)
class FormerVMHAEvidence:
    """One coherent, secret-free classification snapshot."""

    allocation_id: str
    allocation_name: str
    cluster_id: str
    owner_instance_id: str
    owner_network_interface_name: str
    members: tuple[_MemberIdentity, _MemberIdentity]


def render_provisioning_marker(spec: GatewayGroupSpec, instance_index: int) -> str:
    """Render the secret-free Compute-persisted identity for one HA member."""

    vm_ha = spec.vm_ha
    if vm_ha is None:
        raise RuntimeError("VM-HA provisioning marker requires explicit VM HA")
    members = [
        {
            "instance_index": member.instance_index,
            "instance_name": f"{spec.name}-{member.instance_index}",
            "node_id": member.node_id,
            "role": member.role.value,
        }
        for member in sorted(vm_ha.members, key=lambda item: item.instance_index)
    ]
    own_members = [member for member in members if member["instance_index"] == instance_index]
    if len(members) != 2 or len(own_members) != 1:
        raise RuntimeError("VM-HA provisioning marker requires two exact member records")
    payload = {
        "schema": PROVISIONING_MARKER_SCHEMA,
        "cluster_id": vm_ha.cluster_id,
        "shared_allocation_name": f"{spec.name}-{vm_ha.cluster_id}-shared-private-ip",
        "member": own_members[0],
        "members": members,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def parse_provisioning_marker(instance: t.Any) -> dict[str, t.Any] | None:
    """Read one exact marker from the Compute-persisted cloud-init value."""

    cloud_init = str(
        getattr(getattr(instance, "spec", None), "cloud_init_user_data", "") or ""
    )
    encoded = [
        line.removeprefix(PROVISIONING_MARKER_PREFIX)
        for line in cloud_init.splitlines()
        if line.startswith(PROVISIONING_MARKER_PREFIX)
    ]
    if len(encoded) != 1:
        return None
    try:
        marker = json.loads(encoded[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return marker if isinstance(marker, dict) else None


def validate_provisioning_marker(
    marker: dict[str, t.Any],
    *,
    expected_instance_index: int,
    gateway_name: str,
    allocation_name: str,
    cluster_id: str,
) -> _Topology | None:
    """Require one canonical two-member topology and the marker's own record."""

    if set(marker) != {
        "schema",
        "cluster_id",
        "shared_allocation_name",
        "member",
        "members",
    } or marker.get("schema") != PROVISIONING_MARKER_SCHEMA:
        return None
    if (
        marker.get("cluster_id") != cluster_id
        or marker.get("shared_allocation_name") != allocation_name
    ):
        return None
    raw_members = marker.get("members")
    own_member = marker.get("member")
    if not isinstance(raw_members, list) or len(raw_members) != 2:
        return None
    records: list[_IdentifierRecord] = []
    for raw_member in raw_members:
        if not isinstance(raw_member, dict) or set(raw_member) != {
            "instance_index",
            "instance_name",
            "node_id",
            "role",
        }:
            return None
        index = raw_member.get("instance_index")
        node_id = raw_member.get("node_id")
        role = raw_member.get("role")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index not in {0, 1}
            or raw_member.get("instance_name") != f"{gateway_name}-{index}"
            or not isinstance(node_id, str)
            or len(node_id) > 64
            or not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", node_id)
            or role not in {VMHARole.ACTIVE.value, VMHARole.PASSIVE.value}
        ):
            return None
        records.append((index, f"{gateway_name}-{index}", node_id, str(role)))
    records.sort()
    if (
        [record[0] for record in records] != [0, 1]
        or len({record[2] for record in records}) != 2
        or {record[3] for record in records}
        != {VMHARole.ACTIVE.value, VMHARole.PASSIVE.value}
    ):
        return None
    expected_own = next(
        (
            raw_member
            for raw_member in raw_members
            if raw_member.get("instance_index") == expected_instance_index
        ),
        None,
    )
    if own_member != expected_own:
        return None
    return t.cast(_Topology, tuple(records))


def _read_former_allocation(
    *,
    project_id: str,
    client: t.Any,
    gateway_name: str,
    resource_id: t.Callable[[t.Any], str | None],
) -> tuple[t.Any, str, str] | None:
    """List with the generated SDK contract, then re-read one exact private candidate."""

    try:
        from nebius.api.nebius.vpc.v1 import (  # type: ignore
            AllocationServiceClient,
            GetAllocationRequest,
            ListAllocationsRequest,
        )

        service = AllocationServiceClient(client)
        items: list[t.Any] = []
        page_token = ""
        seen_tokens: set[str] = set()
        for _ in range(100):
            request = ListAllocationsRequest(
                parent_id=project_id,
                page_size=1000,
                page_token=page_token,
            )
            response = service.list(request)
            if hasattr(response, "wait"):
                response = response.wait()
            items.extend(list(getattr(response, "items", response) or []))
            next_page_token = str(getattr(response, "next_page_token", "") or "")
            if not next_page_token:
                break
            if next_page_token in seen_tokens:
                raise RuntimeError("Former VM-HA allocation pagination repeated a token")
            seen_tokens.add(next_page_token)
            page_token = next_page_token
        else:
            raise RuntimeError("Former VM-HA allocation pagination exceeded its bound")
    except Exception as error:
        raise RuntimeError("Former VM-HA allocation evidence could not be read") from error

    prefix = f"{gateway_name}-"
    suffix = "-shared-private-ip"
    matches = [
        allocation
        for allocation in items
        if (
            str(getattr(getattr(allocation, "metadata", None), "name", "")).startswith(prefix)
            and str(getattr(getattr(allocation, "metadata", None), "name", "")).endswith(suffix)
        )
    ]
    if len(matches) != 1:
        return None
    listed = matches[0]
    allocation_name = str(getattr(getattr(listed, "metadata", None), "name", ""))
    cluster_id = allocation_name[len(prefix) : -len(suffix)]
    allocation_id = resource_id(listed)
    if (
        not allocation_id
        or len(cluster_id) > 64
        or not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", cluster_id)
    ):
        return None
    try:
        current = service.get(GetAllocationRequest(id=allocation_id))
        if hasattr(current, "wait"):
            current = current.wait()
    except Exception as error:
        raise RuntimeError("Former VM-HA allocation evidence could not be re-read") from error
    if (
        resource_id(current) != allocation_id
        or str(getattr(getattr(current, "metadata", None), "name", "")) != allocation_name
    ):
        return None
    allocation_spec = getattr(current, "spec", None)
    private_spec = getattr(allocation_spec, "ipv4_private", None)
    public_spec = getattr(allocation_spec, "ipv4_public", None)
    if (
        private_spec is None
        or public_spec is not None
        or not str(getattr(private_spec, "subnet_id", "") or "")
    ):
        return None
    return current, allocation_name, cluster_id


def classify_former_vm_ha_evidence(
    *,
    project_id: str | None,
    client: t.Any,
    gateway_name: str,
    resource_id: t.Callable[[t.Any], str | None],
    instance_reader: t.Callable[[t.Any, str], t.Any | None],
    public_ip_reader: t.Callable[[t.Any], str | None],
) -> tuple[dict[str, tuple[t.Any, str]], FormerVMHAEvidence] | None:
    """Classify only an independently coherent allocation and two-member record."""

    if not project_id:
        return None
    allocation_result = _read_former_allocation(
        project_id=project_id,
        client=client,
        gateway_name=gateway_name,
        resource_id=resource_id,
    )
    if allocation_result is None:
        return None
    allocation, allocation_name, cluster_id = allocation_result
    allocation_id = resource_id(allocation)
    assert allocation_id is not None

    members: dict[str, tuple[t.Any, str]] = {}
    topologies: list[_Topology] = []
    identities: list[_MemberIdentity] = []
    attachments: list[tuple[str, str]] = []
    for index in range(2):
        name = f"{gateway_name}-{index}"
        instance = instance_reader(client, name)
        if instance is None:
            return None
        compute_id = resource_id(instance)
        metadata_name = str(getattr(getattr(instance, "metadata", None), "name", "") or "")
        public_ip = public_ip_reader(instance)
        interfaces = list(
            getattr(getattr(instance, "spec", None), "network_interfaces", []) or []
        )
        if not compute_id or metadata_name != name or not public_ip or len(interfaces) != 1:
            return None
        network_interface_name = str(getattr(interfaces[0], "name", "") or "")
        if not network_interface_name:
            return None
        marker = parse_provisioning_marker(instance)
        if marker is None:
            return None
        topology = validate_provisioning_marker(
            marker,
            expected_instance_index=index,
            gateway_name=gateway_name,
            allocation_name=allocation_name,
            cluster_id=cluster_id,
        )
        if topology is None:
            return None
        record = topology[index]
        topologies.append(topology)
        identities.append((name, compute_id, network_interface_name, record[2], record[3]))
        attached_allocation_id = str(
            getattr(getattr(interfaces[0], "ip_address", None), "allocation_id", "") or ""
        )
        if attached_allocation_id == allocation_id:
            attachments.append((compute_id, network_interface_name))
        members[name] = (instance, public_ip)
    if len(topologies) != 2 or topologies[0] != topologies[1]:
        return None

    try:
        owner = allocation_observation(allocation_id, allocation).owner
    except HACloudError:
        return None
    if owner is None or attachments != [(owner.instance_id, owner.network_interface_name)]:
        return None
    if (owner.instance_id, owner.network_interface_name) not in {
        (identity[1], identity[2]) for identity in identities
    }:
        return None
    evidence = FormerVMHAEvidence(
        allocation_id=allocation_id,
        allocation_name=allocation_name,
        cluster_id=cluster_id,
        owner_instance_id=owner.instance_id,
        owner_network_interface_name=owner.network_interface_name,
        members=t.cast(tuple[_MemberIdentity, _MemberIdentity], tuple(identities)),
    )
    return members, evidence
