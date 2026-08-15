"""Durable secret-free selector for the local VM-HA lifecycle.

This record never replaces cloud or SSH authority.  It only records that an
exact two-member deployment must be treated as possibly active until both
members have been independently verified terminally non-HA.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

LIFECYCLE_SCHEMA = "nebius-vpngw/vm-ha-lifecycle-v2"


class VMHALifecycleStatus(str, Enum):
    """Persisted lifecycle states that select ordinary or removal handling."""

    ACTIVE = "active"
    REMOVAL_IN_PROGRESS = "removal-in-progress"
    REMOVED = "removed"


@dataclass(frozen=True)
class VMHALifecycleMember:
    """One exact member identity retained without credential material."""

    instance_index: int
    instance_name: str
    node_id: str
    role: str
    compute_id: str
    network_interface_name: str
    public_ip: str

    def __post_init__(self) -> None:
        values = (
            self.instance_name,
            self.node_id,
            self.compute_id,
            self.network_interface_name,
            self.public_ip,
        )
        if self.instance_index not in {0, 1} or any(not value for value in values):
            raise ValueError("VM-HA lifecycle member identity is incomplete")
        if self.role not in {"active", "passive"}:
            raise ValueError("VM-HA lifecycle member role is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "compute_id": self.compute_id,
            "instance_index": self.instance_index,
            "instance_name": self.instance_name,
            "network_interface_name": self.network_interface_name,
            "node_id": self.node_id,
            "public_ip": self.public_ip,
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, value: Any) -> VMHALifecycleMember:
        expected = {
            "compute_id",
            "instance_index",
            "instance_name",
            "network_interface_name",
            "node_id",
            "public_ip",
            "role",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("VM-HA lifecycle member record is malformed")
        index = value.get("instance_index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("VM-HA lifecycle member index is malformed")
        string_fields = expected - {"instance_index"}
        if any(not isinstance(value.get(field), str) for field in string_fields):
            raise ValueError("VM-HA lifecycle member record is malformed")
        return cls(
            instance_index=index,
            instance_name=value["instance_name"],
            node_id=value["node_id"],
            role=value["role"],
            compute_id=value["compute_id"],
            network_interface_name=value["network_interface_name"],
            public_ip=value["public_ip"],
        )


@dataclass(frozen=True)
class VMHALifecycleState:
    """One versioned lifecycle selector bound to exact cloud identities."""

    status: VMHALifecycleStatus
    project_id: str
    gateway_name: str
    cluster_id: str
    allocation_id: str
    allocation_name: str
    members: tuple[VMHALifecycleMember, VMHALifecycleMember]

    def __post_init__(self) -> None:
        if any(
            not value
            for value in (
                self.project_id,
                self.gateway_name,
                self.cluster_id,
                self.allocation_id,
                self.allocation_name,
            )
        ):
            raise ValueError("VM-HA lifecycle identity is incomplete")
        ordered = tuple(sorted(self.members, key=lambda member: member.instance_index))
        if self.members != ordered or [member.instance_index for member in ordered] != [0, 1]:
            raise ValueError("VM-HA lifecycle requires two canonically ordered members")
        if len({member.instance_name for member in ordered}) != 2:
            raise ValueError("VM-HA lifecycle member names are not unique")
        if len({member.node_id for member in ordered}) != 2:
            raise ValueError("VM-HA lifecycle node identities are not unique")
        if len({member.compute_id for member in ordered}) != 2:
            raise ValueError("VM-HA lifecycle Compute identities are not unique")
        if {member.role for member in ordered} != {"active", "passive"}:
            raise ValueError("VM-HA lifecycle requires one active and one passive member")
        for member in ordered:
            if member.instance_name != f"{self.gateway_name}-{member.instance_index}":
                raise ValueError("VM-HA lifecycle member name does not match the gateway")
        expected_allocation_name = f"{self.gateway_name}-{self.cluster_id}-shared-private-ip"
        if self.allocation_name != expected_allocation_name:
            raise ValueError("VM-HA lifecycle allocation name is not canonical")

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "allocation_id": self.allocation_id,
            "allocation_name": self.allocation_name,
            "cluster_id": self.cluster_id,
            "gateway_name": self.gateway_name,
            "members": [member.to_dict() for member in self.members],
            "project_id": self.project_id,
        }

    @property
    def record_sha256(self) -> str:
        """Bind the schema, lifecycle status, and exact identity in one digest."""

        encoded = json.dumps(
            {
                "identity": self._identity_payload(),
                "schema": LIFECYCLE_SCHEMA,
                "status": self.status.value,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def has_same_identity(self, other: VMHALifecycleState) -> bool:
        """Compare exact identity while lifecycle writers advance the status."""

        return self._identity_payload() == other._identity_payload()

    def with_status(self, status: VMHALifecycleStatus) -> VMHALifecycleState:
        return VMHALifecycleState(
            status=status,
            project_id=self.project_id,
            gateway_name=self.gateway_name,
            cluster_id=self.cluster_id,
            allocation_id=self.allocation_id,
            allocation_name=self.allocation_name,
            members=self.members,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self._identity_payload(),
            "record_sha256": self.record_sha256,
            "schema": LIFECYCLE_SCHEMA,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, value: Any) -> VMHALifecycleState:
        if not isinstance(value, dict) or set(value) != {
            "identity",
            "record_sha256",
            "schema",
            "status",
        }:
            raise ValueError("VM-HA lifecycle state is malformed")
        if value.get("schema") != LIFECYCLE_SCHEMA:
            raise ValueError("VM-HA lifecycle schema is unsupported")
        identity = value.get("identity")
        if not isinstance(identity, dict) or set(identity) != {
            "allocation_id",
            "allocation_name",
            "cluster_id",
            "gateway_name",
            "members",
            "project_id",
        }:
            raise ValueError("VM-HA lifecycle identity is malformed")
        string_fields = {
            "allocation_id",
            "allocation_name",
            "cluster_id",
            "gateway_name",
            "project_id",
        }
        if any(not isinstance(identity.get(field), str) for field in string_fields):
            raise ValueError("VM-HA lifecycle identity is malformed")
        raw_members = identity.get("members")
        if not isinstance(raw_members, list) or len(raw_members) != 2:
            raise ValueError("VM-HA lifecycle member set is malformed")
        try:
            status = VMHALifecycleStatus(value.get("status"))
        except ValueError as error:
            raise ValueError("VM-HA lifecycle status is unsupported") from error
        state = cls(
            status=status,
            project_id=identity["project_id"],
            gateway_name=identity["gateway_name"],
            cluster_id=identity["cluster_id"],
            allocation_id=identity["allocation_id"],
            allocation_name=identity["allocation_name"],
            members=tuple(VMHALifecycleMember.from_dict(member) for member in raw_members),  # type: ignore[arg-type]
        )
        digest = value.get("record_sha256")
        if not isinstance(digest, str) or digest != state.record_sha256:
            raise ValueError("VM-HA lifecycle record digest does not match")
        return state


class VMHALifecycleStore:
    """Strict atomic store scoped to one selected local configuration file."""

    def __init__(self, config_path: Path) -> None:
        self.path = config_path.with_name(f"{config_path.name}.vm-ha-lifecycle.json")

    def read(
        self,
        *,
        expected_project_id: str | None,
        expected_gateway_name: str,
    ) -> VMHALifecycleState | None:
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ValueError("VM-HA lifecycle path must be a regular file")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("VM-HA lifecycle state is unreadable or malformed") from error
        state = VMHALifecycleState.from_dict(payload)
        if not expected_project_id or state.project_id != expected_project_id:
            raise ValueError("VM-HA lifecycle project identity does not match the config")
        if state.gateway_name != expected_gateway_name:
            raise ValueError("VM-HA lifecycle gateway identity does not match the config")
        return state

    def write_verified(self, state: VMHALifecycleState) -> None:
        payload = (
            json.dumps(
                state.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        observed = self.read(
            expected_project_id=state.project_id,
            expected_gateway_name=state.gateway_name,
        )
        if observed != state:
            raise RuntimeError("VM-HA lifecycle state did not verify after atomic write")


def lifecycle_member_map(
    state: VMHALifecycleState,
) -> Mapping[str, VMHALifecycleMember]:
    """Return the canonical exact-name view used by cloud classification."""

    return {member.instance_name: member for member in state.members}
