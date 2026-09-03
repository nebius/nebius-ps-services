"""Durable, secret-free authority for VM-HA migration and removal.

The v4 record is the only local transaction authority for effects, exact cloud
identity, activation, and removal.  It never replaces authoritative cloud or
SSH observations.  Version-2 and version-3 records remain readable byte-for-byte
and are upgraded only by an explicitly approved mutating successor.
"""

from __future__ import annotations

import fcntl
import fnmatch
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, cast

LIFECYCLE_SCHEMA_V2 = "nebius-vpngw/vm-ha-lifecycle-v2"
LIFECYCLE_SCHEMA_V3 = "nebius-vpngw/vm-ha-lifecycle-v3"
LIFECYCLE_SCHEMA = "nebius-vpngw/vm-ha-lifecycle-v4"
_APPROVAL_KINDS = frozenset({"destruction", "migration", "recovery"})
_DIGEST = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")

_PASSIVE_REPLACEMENT_AUDIT_PREFIXES = (
    "passive-replacement-approval:",
    "passive-replacement-",
    "standby-replacement-",
    "retired-compute:",
    "retired-disk:",
    "retired-",
    "replacement-compute:",
    "replacement-disk:",
    "replacement-",
)

_MAX_NEBIUS_RESOURCE_NAME_LENGTH = 63


def vm_ha_passive_replacement_binding_key(
    kind: str,
    instance_name: str,
    cycle: int,
) -> str:
    """Return one backward-compatible, cycle-qualified replacement binding key."""

    if cycle < 1 or kind not in {"approval", "retired-compute", "retired-disk", "compute", "disk"}:
        raise ValueError("VM-HA passive replacement binding identity is invalid")
    if cycle == 1:
        prefixes = {
            "approval": "passive-replacement-approval",
            "retired-compute": "retired-compute",
            "retired-disk": "retired-disk",
            "compute": "replacement-compute",
            "disk": "replacement-disk",
        }
    else:
        prefixes = {
            "approval": f"passive-replacement-{cycle}-approval",
            "retired-compute": f"retired-{cycle}-compute",
            "retired-disk": f"retired-{cycle}-disk",
            "compute": f"replacement-{cycle}-compute",
            "disk": f"replacement-{cycle}-disk",
        }
    return f"{prefixes[kind]}:{instance_name}"


def vm_ha_passive_replacement_effect(
    instance_name: str,
    cycle: int,
    action: str,
) -> str:
    """Return one cycle-qualified effect while preserving first-cycle records."""

    if cycle < 1 or action not in {
        "intent",
        "delete-compute",
        "delete-boot-disk",
        "create-boot-disk",
        "create-compute",
    }:
        raise ValueError("VM-HA passive replacement effect identity is invalid")
    cycle_part = "" if cycle == 1 else f"{cycle}-"
    return f"replace-failed-{cycle_part}{instance_name}-{action}"


def vm_ha_missing_standby_replacement_effect(
    instance_name: str,
    cycle: int,
    action: str,
) -> str:
    """Return one replay-stable creation-only standby replacement effect."""

    if cycle < 1 or action not in {
        "intent",
        "stage-ssh-identity",
        "publish-ssh-trust",
        "create-boot-disk",
        "create-compute",
    }:
        raise ValueError("VM-HA missing standby replacement effect identity is invalid")
    cycle_part = "" if cycle == 1 else f"{cycle}-"
    return f"replace-missing-{cycle_part}{instance_name}-{action}"


def vm_ha_missing_standby_ssh_binding_key(
    kind: str,
    instance_name: str,
    cycle: int,
) -> str:
    """Return one cycle-qualified, secret-free SSH replacement audit binding."""

    if cycle < 1 or kind not in {
        "old-fingerprint",
        "trust-scope",
        "predecessor-receipt",
        "predecessor-projection",
        "storage-owner",
        "stage-token",
        "new-fingerprint",
        "successor-receipt",
        "successor-projection",
    }:
        raise ValueError("VM-HA missing standby SSH binding identity is invalid")
    cycle_part = "" if cycle == 1 else f"{cycle}-"
    return f"standby-replacement-{cycle_part}ssh-{kind}:{instance_name}"


def vm_ha_missing_standby_disk_name_binding_key(
    instance_name: str,
    cycle: int,
) -> str:
    """Return the audit-only binding for one fresh replacement disk name."""

    if cycle < 1:
        raise ValueError("VM-HA missing standby replacement cycle is invalid")
    cycle_part = "" if cycle == 1 else f"{cycle}-"
    return f"standby-replacement-{cycle_part}disk-name:{instance_name}"


def vm_ha_missing_standby_owner_binding_key(kind: str, sequence: int) -> str:
    """Return one append-only binding for the cloud-proven serving owner."""

    if sequence < 1 or kind not in {"instance", "compute", "nic"}:
        raise ValueError("VM-HA missing standby owner binding identity is invalid")
    return f"standby-replacement-{sequence}-owner-{kind}"


def vm_ha_missing_standby_owner_sequences(bindings: Mapping[str, str]) -> tuple[int, ...]:
    """Return complete append-only serving-owner audit sequences."""

    sequences: set[int] = set()
    pattern = re.compile(r"standby-replacement-([1-9][0-9]*)-owner-(instance|compute|nic)")
    kinds: dict[int, set[str]] = {}
    for key in bindings:
        matched = pattern.fullmatch(key)
        if matched is not None:
            kinds.setdefault(int(matched.group(1)), set()).add(matched.group(2))
    for sequence, observed_kinds in kinds.items():
        if observed_kinds == {"instance", "compute", "nic"}:
            sequences.add(sequence)
    return tuple(sorted(sequences))


def vm_ha_missing_standby_disk_name(
    *,
    gateway_name: str,
    instance_name: str,
    predecessor_sha256: str,
    cycle: int,
) -> str:
    """Derive a provider-valid fresh disk name without depending on approval output."""

    if (
        cycle < 1
        or not gateway_name
        or not instance_name
        or not _DIGEST.fullmatch(predecessor_sha256)
    ):
        raise ValueError("VM-HA missing standby disk-name identity is invalid")
    seed = hashlib.sha256(
        f"{gateway_name}\0{instance_name}\0{predecessor_sha256}\0{cycle}".encode()
    ).hexdigest()[:12]
    suffix = f"-boot-r{cycle}-{seed}"
    prefix_length = _MAX_NEBIUS_RESOURCE_NAME_LENGTH - len(suffix)
    prefix = instance_name[:prefix_length].rstrip("-._")
    if not prefix:
        raise ValueError("VM-HA missing standby disk-name prefix is invalid")
    return f"{prefix}{suffix}"


def vm_ha_passive_replacement_cycles(
    bindings: Mapping[str, str],
    instance_name: str,
) -> tuple[int, ...]:
    """Return every complete approval cycle recorded for one passive."""

    cycles: set[int] = set()
    if vm_ha_passive_replacement_binding_key("approval", instance_name, 1) in bindings:
        cycles.add(1)
    pattern = re.compile(rf"passive-replacement-([2-9][0-9]*)-approval:{re.escape(instance_name)}")
    for key in bindings:
        matched = pattern.fullmatch(key)
        if matched is not None:
            cycles.add(int(matched.group(1)))
    return tuple(sorted(cycles))


def vm_ha_passive_replacement_cycle_for_approval(
    bindings: Mapping[str, str],
    instance_name: str,
    approval_digest: str,
) -> int | None:
    """Resolve an existing approval without treating a digest as mutable state."""

    matches = [
        cycle
        for cycle in vm_ha_passive_replacement_cycles(bindings, instance_name)
        if bindings.get(vm_ha_passive_replacement_binding_key("approval", instance_name, cycle))
        == approval_digest
    ]
    if len(matches) > 1:
        raise ValueError("VM-HA passive replacement approval digest is ambiguous")
    return matches[0] if matches else None


def vm_ha_effective_resource_bindings(
    bindings: Mapping[str, str],
) -> dict[str, str]:
    """Return live bindings while retaining replacement history in the record."""

    effective = {
        key: value
        for key, value in bindings.items()
        if not key.startswith(_PASSIVE_REPLACEMENT_AUDIT_PREFIXES)
    }
    member_cycles: dict[str, set[int]] = {}
    for key in bindings:
        if key.startswith("retired-compute:"):
            member_cycles.setdefault(key.removeprefix("retired-compute:"), set()).add(1)
        matched = re.fullmatch(r"retired-([2-9][0-9]*)-compute:(.+)", key)
        if matched is not None:
            member_cycles.setdefault(matched.group(2), set()).add(int(matched.group(1)))
    for name, cycles in member_cycles.items():
        effective.pop(f"compute:{name}", None)
        effective.pop(f"disk:{name}", None)
        for cycle in sorted(cycles, reverse=True):
            compute = bindings.get(vm_ha_passive_replacement_binding_key("compute", name, cycle))
            disk = bindings.get(vm_ha_passive_replacement_binding_key("disk", name, cycle))
            if compute:
                effective[f"compute:{name}"] = compute
                effective[f"disk:{name}"] = disk or ""
                if not effective[f"disk:{name}"]:
                    effective.pop(f"disk:{name}")
                break
            if disk:
                effective[f"disk:{name}"] = disk
                break
    owner_sequences = vm_ha_missing_standby_owner_sequences(bindings)
    if owner_sequences:
        latest = owner_sequences[-1]
        effective["shared-allocation-owner-compute"] = bindings[
            vm_ha_missing_standby_owner_binding_key("compute", latest)
        ]
        effective["shared-allocation-owner-nic"] = bindings[
            vm_ha_missing_standby_owner_binding_key("nic", latest)
        ]
    return effective


def vm_ha_destroyed_retained_public_bindings(
    state: VMHALifecycleState,
) -> dict[str, str]:
    """Return exact retained public identities from a terminal destruction."""

    if (
        state.record_version != 4
        or state.status is not VMHALifecycleStatus.DESTROYED
        or state.transaction is None
    ):
        raise ValueError("VM-HA retained public bindings require a destroyed lifecycle")
    effective = vm_ha_effective_resource_bindings(dict(state.transaction.resource_bindings))
    members = {member.instance_name: member for member in state.members}
    retained: dict[str, str] = {}
    for key, value in effective.items():
        if not key.startswith("public-allocation:"):
            continue
        identity = key.removeprefix("public-allocation:")
        if ":" not in identity:
            raise ValueError("VM-HA retained public allocation binding is malformed")
        instance_name, nic_name = identity.rsplit(":", 1)
        member = members.get(instance_name)
        if (
            member is None
            or member.network_interface_name not in {"", "eth0"}
            or nic_name != "eth0"
        ):
            raise ValueError("VM-HA retained public allocation binding is foreign")
        retained[key] = value
    for member in state.members:
        if not member.public_allocation_id:
            continue
        if not member.network_interface_name:
            raise ValueError("VM-HA retained public allocation has no network interface")
        key = f"public-allocation:{member.instance_name}:{member.network_interface_name}"
        if effective.get(key) != member.public_allocation_id:
            raise ValueError("VM-HA retained public allocation binding changed")
    return retained


def vm_ha_resource_binding_matches_observation(
    key: str,
    value: str,
    *,
    observed: Mapping[str, str],
    expected: Mapping[str, str],
) -> bool:
    """Match a binding, allowing only pre-Compute standalone member resources."""

    if key == "route-runtime-id" or observed.get(key) == value:
        return True
    member_name = ""
    if key.startswith("disk:"):
        member_name = key.removeprefix("disk:")
    elif key.startswith("primary-allocation:"):
        member_name = key.removeprefix("primary-allocation:").rsplit(":", 1)[0]
    elif key.startswith("public-allocation:"):
        member_name = key.removeprefix("public-allocation:").rsplit(":", 1)[0]
    if not member_name:
        return False
    compute_key = f"compute:{member_name}"
    return compute_key not in expected and compute_key not in observed


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def normalize_vm_ha_observation(value: object) -> tuple[tuple[str, str], ...]:
    """Flatten one secret-free cloud observation into canonical JSON-pointer leaves."""

    leaves: list[tuple[str, str]] = []

    def visit(current: object, path: str) -> None:
        if isinstance(current, Mapping):
            if not current:
                leaves.append((path or "/", "{}"))
                return
            for key in sorted(current):
                if not isinstance(key, str):
                    raise ValueError("VM-HA observation keys must be strings")
                visit(current[key], f"{path}/{_json_pointer_token(key)}")
            return
        if isinstance(current, (list, tuple)):
            if not current:
                leaves.append((path or "/", "[]"))
                return
            for index, item in enumerate(current):
                visit(item, f"{path}/{index}")
            return
        try:
            encoded = _canonical_json(current).decode("ascii")
        except (TypeError, UnicodeError) as error:
            raise ValueError("VM-HA observation is not canonical JSON") from error
        leaves.append((path or "/", encoded))

    visit(value, "")
    result = tuple(leaves)
    if result != tuple(sorted(result)) or len({path for path, _value in result}) != len(result):
        raise ValueError("VM-HA observation paths are not canonical")
    return result


def vm_ha_observation_changed_paths(
    before: tuple[tuple[str, str], ...],
    after: tuple[tuple[str, str], ...],
) -> frozenset[str]:
    """Return every leaf path that was added, removed, or changed."""

    before_map = dict(before)
    after_map = dict(after)
    return frozenset(
        path
        for path in before_map.keys() | after_map.keys()
        if before_map.get(path) != after_map.get(path)
    )


@dataclass(frozen=True)
class VMHAEffectObservationGuard:
    """Exact pre-effect cloud state and the only paths one effect may change."""

    effect: str
    permitted_paths: tuple[str, ...]
    pre_observation: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.effect):
            raise ValueError("VM-HA lifecycle observation guard effect is invalid")
        if self.permitted_paths != tuple(sorted(set(self.permitted_paths))) or any(
            not path.startswith("/") or "//" in path for path in self.permitted_paths
        ):
            raise ValueError("VM-HA lifecycle permitted observation paths are invalid")
        if self.pre_observation != tuple(sorted(self.pre_observation)) or len(
            {path for path, _value in self.pre_observation}
        ) != len(self.pre_observation):
            raise ValueError("VM-HA lifecycle pre-effect observation is not canonical")

    def permits(self, changed_paths: frozenset[str]) -> bool:
        return not self.unpermitted(changed_paths)

    def unpermitted(self, changed_paths: frozenset[str]) -> tuple[str, ...]:
        """Return only secret-free observation path names outside the effect contract."""

        patterns = self.permitted_paths
        if self.effect == "provision-shared-allocation":
            # Records written before the owner-null observation fix omitted
            # this scalar leaf. This admits only the unattached ``null`` leaf;
            # an attached owner normalizes to child paths and remains blocked.
            patterns = (*patterns, "/shared_allocation/owner")
        if self.effect.endswith("-create-compute"):
            matching_indices: list[str] = []
            for path, encoded in self.pre_observation:
                matched = re.fullmatch(r"/members/([0-9]+)/instance_name", path)
                if matched is None:
                    continue
                try:
                    instance_name = json.loads(encoded)
                except json.JSONDecodeError:
                    continue
                if not isinstance(instance_name, str) or not instance_name:
                    continue
                if self.effect == f"provision-{instance_name}-compute" or re.fullmatch(
                    rf"replace-(?:failed|missing)-(?:[2-9][0-9]*-)?"
                    rf"{re.escape(instance_name)}-create-compute",
                    self.effect,
                ):
                    matching_indices.append(matched.group(1))
            if len(matching_indices) == 1:
                # A pre-adoption create guard may predate provider-state tracking.
                # Admit only the exact target member's transient state leaf; all
                # identity, attachment, and unrelated-member paths remain closed.
                patterns = (*patterns, f"/members/{matching_indices[0]}/state")
        return tuple(
            sorted(
                path
                for path in changed_paths
                if not any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect,
            "permitted_paths": list(self.permitted_paths),
            "pre_observation": [
                {"path": path, "value": value} for path, value in self.pre_observation
            ],
        }

    @classmethod
    def from_dict(cls, value: Any) -> VMHAEffectObservationGuard:
        if not isinstance(value, dict) or set(value) != {
            "effect",
            "permitted_paths",
            "pre_observation",
        }:
            raise ValueError("VM-HA lifecycle observation guard is malformed")
        paths = value.get("permitted_paths")
        observation = value.get("pre_observation")
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ValueError("VM-HA lifecycle permitted observation paths are malformed")
        if not isinstance(observation, list):
            raise ValueError("VM-HA lifecycle pre-effect observation is malformed")
        leaves: list[tuple[str, str]] = []
        for item in observation:
            if (
                not isinstance(item, dict)
                or set(item) != {"path", "value"}
                or not isinstance(item.get("path"), str)
                or not isinstance(item.get("value"), str)
            ):
                raise ValueError("VM-HA lifecycle observation leaf is malformed")
            leaves.append((item["path"], item["value"]))
        effect = value.get("effect")
        if not isinstance(effect, str):
            raise ValueError("VM-HA lifecycle observation guard effect is malformed")
        return cls(effect, tuple(paths), tuple(leaves))


class VMHALifecycleStatus(str, Enum):
    """Persisted lifecycle states for one migration/removal transaction."""

    PROVISIONING = "provisioning"
    ACTIVATING = "activating"
    ACTIVE = "active"
    REMOVAL_IN_PROGRESS = "removal-in-progress"
    REMOVED = "removed"
    DESTROYED = "destroyed"


def vm_ha_activation_effect_is_host_only(effect: str | None) -> bool:
    """Return whether an interrupted effect is confined to host activation."""

    if effect is None:
        return True
    return effect in {
        "verify-active-forwarding-and-routes",
        "verify-passive-unlocked-non-forwarding",
    } or effect.startswith(
        (
            "stage-",
            "install-apply-lock-",
            "install-owner-adoption-",
            "reconcile-replacement-policy-",
            "activate-",
            "release-standby-replacement-inhibition-",
        )
    )


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
    compute_revision: str = ""
    disk_id: str = ""
    network_interface_subnet_id: str = ""
    primary_allocation_id: str = ""
    public_allocation_id: str = ""
    alias_allocation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.instance_index not in {0, 1}:
            raise ValueError("VM-HA lifecycle member index is invalid")
        if self.role not in {"active", "passive"}:
            raise ValueError("VM-HA lifecycle member role is invalid")
        for value in (self.instance_name, self.node_id):
            if not value:
                raise ValueError("VM-HA lifecycle member identity is incomplete")
        if self.alias_allocation_ids != tuple(sorted(set(self.alias_allocation_ids))):
            raise ValueError("VM-HA lifecycle aliases must be unique and ordered")

    @property
    def binding_complete(self) -> bool:
        """Return whether every authoritative member binding field is filled."""

        required = (
            self.compute_id,
            self.compute_revision,
            self.disk_id,
            self.network_interface_name,
            self.network_interface_subnet_id,
            self.primary_allocation_id,
            self.public_ip,
        )
        return all(required)

    def to_dict(self, *, legacy: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "compute_id": self.compute_id,
            "instance_index": self.instance_index,
            "instance_name": self.instance_name,
            "network_interface_name": self.network_interface_name,
            "node_id": self.node_id,
            "public_ip": self.public_ip,
            "role": self.role,
        }
        if legacy:
            return value
        value.update(
            {
                "alias_allocation_ids": list(self.alias_allocation_ids),
                "compute_revision": self.compute_revision,
                "disk_id": self.disk_id,
                "network_interface_subnet_id": self.network_interface_subnet_id,
                "primary_allocation_id": self.primary_allocation_id,
                "public_allocation_id": self.public_allocation_id,
            }
        )
        return value

    @classmethod
    def from_dict(cls, value: Any, *, legacy: bool = False) -> VMHALifecycleMember:
        legacy_fields = {
            "compute_id",
            "instance_index",
            "instance_name",
            "network_interface_name",
            "node_id",
            "public_ip",
            "role",
        }
        current_fields = legacy_fields | {
            "alias_allocation_ids",
            "compute_revision",
            "disk_id",
            "network_interface_subnet_id",
            "primary_allocation_id",
            "public_allocation_id",
        }
        expected = legacy_fields if legacy else current_fields
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("VM-HA lifecycle member record is malformed")
        index = value.get("instance_index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("VM-HA lifecycle member index is malformed")
        aliases: tuple[str, ...] = ()
        if not legacy:
            raw_aliases = value.get("alias_allocation_ids")
            if not isinstance(raw_aliases, list) or any(
                not isinstance(item, str) or not item for item in raw_aliases
            ):
                raise ValueError("VM-HA lifecycle aliases are malformed")
            aliases = tuple(raw_aliases)
        string_fields = expected - {"instance_index", "alias_allocation_ids"}
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
            compute_revision="" if legacy else value["compute_revision"],
            disk_id="" if legacy else value["disk_id"],
            network_interface_subnet_id=("" if legacy else value["network_interface_subnet_id"]),
            primary_allocation_id="" if legacy else value["primary_allocation_id"],
            public_allocation_id="" if legacy else value["public_allocation_id"],
            alias_allocation_ids=aliases,
        )


@dataclass(frozen=True)
class VMHAMigrationTransaction:
    """Approval, replay, and compare-and-swap identity for one operation."""

    operation_id: str
    approval_kind: str
    approval_digest: str
    desired_state_digest: str
    current_state_digest: str
    checkpoint: str
    pending_effect: str | None
    completed_effects: tuple[str, ...]
    resource_bindings: tuple[tuple[str, str], ...]
    revision: int
    predecessor_sha256: str | None
    v2_predecessor_sha256: str | None = None
    observation: tuple[tuple[str, str], ...] = ()
    observation_guard: VMHAEffectObservationGuard | None = None
    accepted_cloud_operation_effect: str | None = None
    accepted_cloud_operation_id: str | None = None

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.operation_id):
            raise ValueError("VM-HA lifecycle operation identity is invalid")
        if self.approval_kind not in _APPROVAL_KINDS:
            raise ValueError("VM-HA lifecycle approval kind is invalid")
        for value in (
            self.approval_digest,
            self.desired_state_digest,
            self.current_state_digest,
        ):
            if not _DIGEST.fullmatch(value):
                raise ValueError("VM-HA lifecycle transaction digest is invalid")
        if not _IDENTIFIER.fullmatch(self.checkpoint):
            raise ValueError("VM-HA lifecycle checkpoint is invalid")
        if self.pending_effect is not None and not _IDENTIFIER.fullmatch(self.pending_effect):
            raise ValueError("VM-HA lifecycle pending effect is invalid")
        if self.completed_effects != tuple(sorted(set(self.completed_effects))):
            raise ValueError("VM-HA lifecycle completed effects are not canonical")
        if any(not _IDENTIFIER.fullmatch(item) for item in self.completed_effects):
            raise ValueError("VM-HA lifecycle completed effect is invalid")
        if self.resource_bindings != tuple(sorted(self.resource_bindings)):
            raise ValueError("VM-HA lifecycle resource bindings are not ordered")
        if len({key for key, _value in self.resource_bindings}) != len(
            self.resource_bindings
        ) or any(
            not _IDENTIFIER.fullmatch(key) or not value for key, value in self.resource_bindings
        ):
            raise ValueError("VM-HA lifecycle resource bindings are invalid")
        if self.revision < 1:
            raise ValueError("VM-HA lifecycle revision must be positive")
        if self.predecessor_sha256 is not None and not _DIGEST.fullmatch(self.predecessor_sha256):
            raise ValueError("VM-HA lifecycle predecessor digest is invalid")
        if self.v2_predecessor_sha256 is not None and not _DIGEST.fullmatch(
            self.v2_predecessor_sha256
        ):
            raise ValueError("VM-HA lifecycle v2 predecessor digest is invalid")
        if self.observation != tuple(sorted(self.observation)) or len(
            {path for path, _value in self.observation}
        ) != len(self.observation):
            raise ValueError("VM-HA lifecycle trusted observation is not canonical")
        if self.observation_guard is not None:
            if self.pending_effect != self.observation_guard.effect:
                raise ValueError("VM-HA lifecycle observation guard does not match pending effect")
            if self.observation_guard.pre_observation != self.observation:
                raise ValueError("VM-HA lifecycle observation guard lost its trusted prestate")
        operation_fields = (
            self.accepted_cloud_operation_effect,
            self.accepted_cloud_operation_id,
        )
        if (operation_fields[0] is None) != (operation_fields[1] is None):
            raise ValueError("VM-HA lifecycle accepted cloud operation is incomplete")
        if self.accepted_cloud_operation_effect is not None:
            if (
                self.pending_effect != self.accepted_cloud_operation_effect
                or not _IDENTIFIER.fullmatch(self.accepted_cloud_operation_effect)
                or not _IDENTIFIER.fullmatch(self.accepted_cloud_operation_id or "")
            ):
                raise ValueError("VM-HA lifecycle accepted cloud operation is invalid")

    def to_dict(self, *, record_version: int = 4) -> dict[str, Any]:
        value: dict[str, Any] = {
            "approval_digest": self.approval_digest,
            "approval_kind": self.approval_kind,
            "checkpoint": self.checkpoint,
            "completed_effects": list(self.completed_effects),
            "current_state_digest": self.current_state_digest,
            "desired_state_digest": self.desired_state_digest,
            "operation_id": self.operation_id,
            "pending_effect": self.pending_effect,
            "predecessor_sha256": self.predecessor_sha256,
            "resource_bindings": [
                {"key": key, "value": value} for key, value in self.resource_bindings
            ],
            "revision": self.revision,
            "v2_predecessor_sha256": self.v2_predecessor_sha256,
        }
        if record_version >= 4:
            value.update(
                {
                    "accepted_cloud_operation_effect": self.accepted_cloud_operation_effect,
                    "accepted_cloud_operation_id": self.accepted_cloud_operation_id,
                    "observation": [
                        {"path": path, "value": item} for path, item in self.observation
                    ],
                    "observation_guard": (
                        None if self.observation_guard is None else self.observation_guard.to_dict()
                    ),
                }
            )
        return value

    @classmethod
    def from_dict(cls, value: Any, *, record_version: int = 4) -> VMHAMigrationTransaction:
        expected = {
            "approval_digest",
            "approval_kind",
            "checkpoint",
            "completed_effects",
            "current_state_digest",
            "desired_state_digest",
            "operation_id",
            "pending_effect",
            "predecessor_sha256",
            "resource_bindings",
            "revision",
            "v2_predecessor_sha256",
        }
        if record_version >= 4:
            expected |= {
                "accepted_cloud_operation_effect",
                "accepted_cloud_operation_id",
                "observation",
                "observation_guard",
            }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("VM-HA lifecycle transaction is malformed")
        raw_completed = value.get("completed_effects")
        raw_bindings = value.get("resource_bindings")
        if not isinstance(raw_completed, list) or any(
            not isinstance(item, str) for item in raw_completed
        ):
            raise ValueError("VM-HA lifecycle completed effects are malformed")
        if not isinstance(raw_bindings, list):
            raise ValueError("VM-HA lifecycle resource bindings are malformed")
        bindings: list[tuple[str, str]] = []
        for item in raw_bindings:
            if (
                not isinstance(item, dict)
                or set(item) != {"key", "value"}
                or not isinstance(item.get("key"), str)
                or not isinstance(item.get("value"), str)
            ):
                raise ValueError("VM-HA lifecycle resource binding is malformed")
            bindings.append((item["key"], item["value"]))
        revision = value.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise ValueError("VM-HA lifecycle revision is malformed")
        string_fields = {
            "approval_digest",
            "approval_kind",
            "checkpoint",
            "current_state_digest",
            "desired_state_digest",
            "operation_id",
        }
        if any(not isinstance(value.get(field), str) for field in string_fields):
            raise ValueError("VM-HA lifecycle transaction is malformed")
        for field in (
            "pending_effect",
            "predecessor_sha256",
            "v2_predecessor_sha256",
            "accepted_cloud_operation_effect",
            "accepted_cloud_operation_id",
        ):
            if (
                field in value
                and value.get(field) is not None
                and not isinstance(value.get(field), str)
            ):
                raise ValueError("VM-HA lifecycle transaction is malformed")
        observation: list[tuple[str, str]] = []
        guard: VMHAEffectObservationGuard | None = None
        if record_version >= 4:
            raw_observation = value.get("observation")
            if not isinstance(raw_observation, list):
                raise ValueError("VM-HA lifecycle trusted observation is malformed")
            for item in raw_observation:
                if (
                    not isinstance(item, dict)
                    or set(item) != {"path", "value"}
                    or not isinstance(item.get("path"), str)
                    or not isinstance(item.get("value"), str)
                ):
                    raise ValueError("VM-HA lifecycle trusted observation leaf is malformed")
                observation.append((item["path"], item["value"]))
            if value.get("observation_guard") is not None:
                guard = VMHAEffectObservationGuard.from_dict(value["observation_guard"])
        return cls(
            operation_id=value["operation_id"],
            approval_kind=value["approval_kind"],
            approval_digest=value["approval_digest"],
            desired_state_digest=value["desired_state_digest"],
            current_state_digest=value["current_state_digest"],
            checkpoint=value["checkpoint"],
            pending_effect=value["pending_effect"],
            completed_effects=tuple(raw_completed),
            resource_bindings=tuple(bindings),
            revision=revision,
            predecessor_sha256=value["predecessor_sha256"],
            v2_predecessor_sha256=value["v2_predecessor_sha256"],
            observation=tuple(observation),
            observation_guard=guard,
            accepted_cloud_operation_effect=value.get("accepted_cloud_operation_effect"),
            accepted_cloud_operation_id=value.get("accepted_cloud_operation_id"),
        )

    def advance(
        self,
        *,
        predecessor_sha256: str,
        checkpoint: str | None = None,
        pending_effect: str | None | object = ...,
        completed_effect: str | None = None,
        resource_updates: Mapping[str, str] | None = None,
        observation: tuple[tuple[str, str], ...] | None = None,
        observation_guard: VMHAEffectObservationGuard | None | object = ...,
        accepted_cloud_operation_effect: str | None | object = ...,
        accepted_cloud_operation_id: str | None | object = ...,
    ) -> VMHAMigrationTransaction:
        """Return one monotonic CAS successor with fill-once bindings."""

        completed = set(self.completed_effects)
        if completed_effect is not None:
            completed.add(completed_effect)
        bindings = dict(self.resource_bindings)
        for key, value in (resource_updates or {}).items():
            if key in bindings and bindings[key] != value:
                raise ValueError(f"VM-HA lifecycle resource binding {key} cannot change")
            if not value:
                raise ValueError("VM-HA lifecycle resource binding cannot be empty")
            bindings[key] = value
        next_pending_effect = (
            self.pending_effect if pending_effect is ... else cast(str | None, pending_effect)
        )
        return replace(
            self,
            checkpoint=checkpoint or self.checkpoint,
            pending_effect=next_pending_effect,
            completed_effects=tuple(sorted(completed)),
            resource_bindings=tuple(sorted(bindings.items())),
            revision=self.revision + 1,
            predecessor_sha256=predecessor_sha256,
            observation=self.observation if observation is None else observation,
            observation_guard=(
                self.observation_guard
                if observation_guard is ...
                else cast(VMHAEffectObservationGuard | None, observation_guard)
            ),
            accepted_cloud_operation_effect=(
                self.accepted_cloud_operation_effect
                if accepted_cloud_operation_effect is ...
                else cast(str | None, accepted_cloud_operation_effect)
            ),
            accepted_cloud_operation_id=(
                self.accepted_cloud_operation_id
                if accepted_cloud_operation_id is ...
                else cast(str | None, accepted_cloud_operation_id)
            ),
        )


@dataclass(frozen=True)
class VMHALifecycleState:
    """One versioned lifecycle record bound to exact cloud identities."""

    status: VMHALifecycleStatus
    project_id: str
    gateway_name: str
    cluster_id: str
    allocation_id: str
    allocation_name: str
    members: tuple[VMHALifecycleMember, VMHALifecycleMember]
    route_runtime_id: str = ""
    route_targets: tuple[str, ...] = ()
    transaction: VMHAMigrationTransaction | None = None
    record_version: int = 4

    def __post_init__(self) -> None:
        if self.record_version not in {2, 3, 4}:
            raise ValueError("VM-HA lifecycle record version is invalid")
        required = (self.project_id, self.gateway_name, self.cluster_id)
        if any(not value for value in required):
            raise ValueError("VM-HA lifecycle identity is incomplete")
        if self.record_version in {3, 4} and self.transaction is None:
            raise ValueError("VM-HA lifecycle transaction is missing")
        if self.record_version == 2 and self.transaction is not None:
            raise ValueError("VM-HA lifecycle v2 cannot contain a transaction")
        ordered = tuple(sorted(self.members, key=lambda member: member.instance_index))
        if self.members != ordered or [member.instance_index for member in ordered] != [0, 1]:
            raise ValueError("VM-HA lifecycle requires two canonically ordered members")
        if len({member.instance_name for member in ordered}) != 2:
            raise ValueError("VM-HA lifecycle member names are not unique")
        if len({member.node_id for member in ordered}) != 2:
            raise ValueError("VM-HA lifecycle node identities are not unique")
        compute_ids = {member.compute_id for member in ordered if member.compute_id}
        if len(compute_ids) not in {0, 1, 2} or (
            len(compute_ids) == 1 and sum(bool(member.compute_id) for member in ordered) == 2
        ):
            raise ValueError("VM-HA lifecycle Compute identities are not unique")
        if {member.role for member in ordered} != {"active", "passive"}:
            raise ValueError("VM-HA lifecycle requires one active and one passive member")
        for member in ordered:
            if member.instance_name != f"{self.gateway_name}-{member.instance_index}":
                raise ValueError("VM-HA lifecycle member name does not match the gateway")
        expected_allocation_name = f"{self.gateway_name}-{self.cluster_id}-shared-private-ip"
        if self.allocation_name != expected_allocation_name:
            raise ValueError("VM-HA lifecycle allocation name is not canonical")
        if self.route_targets != tuple(sorted(set(self.route_targets))):
            raise ValueError("VM-HA lifecycle route targets are not canonical")
        if self.record_version in {3, 4} and self.status in {
            VMHALifecycleStatus.ACTIVATING,
            VMHALifecycleStatus.ACTIVE,
        }:
            if (
                not self.allocation_id
                or not self.route_runtime_id
                or not self.route_targets
                or any(not member.binding_complete for member in self.members)
            ):
                raise ValueError("VM-HA lifecycle authoritative binding is incomplete")

    def _identity_payload(self, *, legacy: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "allocation_id": self.allocation_id,
            "allocation_name": self.allocation_name,
            "cluster_id": self.cluster_id,
            "gateway_name": self.gateway_name,
            "members": [member.to_dict(legacy=legacy) for member in self.members],
            "project_id": self.project_id,
        }
        if not legacy:
            payload.update(
                {
                    "route_runtime_id": self.route_runtime_id,
                    "route_targets": list(self.route_targets),
                }
            )
        return payload

    @property
    def record_sha256(self) -> str:
        """Bind the schema, state, transaction, and exact identity."""

        if self.record_version == 2:
            return _digest(
                {
                    "identity": self._identity_payload(legacy=True),
                    "schema": LIFECYCLE_SCHEMA_V2,
                    "status": self.status.value,
                }
            )
        assert self.transaction is not None
        return _digest(
            {
                "identity": self._identity_payload(),
                "schema": LIFECYCLE_SCHEMA_V3 if self.record_version == 3 else LIFECYCLE_SCHEMA,
                "status": self.status.value,
                "transaction": self.transaction.to_dict(record_version=self.record_version),
            }
        )

    @property
    def is_legacy_v2(self) -> bool:
        return self.record_version == 2

    def has_same_identity(self, other: VMHALifecycleState) -> bool:
        """Compare complete v3 identity, or the common v2-compatible subset."""

        if self.record_version == 2 or other.record_version == 2:
            return self._identity_payload(legacy=True) == other._identity_payload(legacy=True)
        return self._identity_payload() == other._identity_payload()

    def with_status(
        self,
        status: VMHALifecycleStatus,
        *,
        checkpoint: str | None = None,
    ) -> VMHALifecycleState:
        if self.transaction is None:
            raise ValueError("VM-HA lifecycle v2 requires an approved v3 successor")
        if self.transaction.pending_effect is not None:
            raise ValueError("VM-HA lifecycle cannot change status with a pending effect")
        transaction = self.transaction.advance(
            predecessor_sha256=self.record_sha256,
            checkpoint=checkpoint or status.value,
            pending_effect=None,
            observation_guard=None,
            accepted_cloud_operation_effect=None,
            accepted_cloud_operation_id=None,
        )
        return replace(self, status=status, transaction=transaction)

    def authorize_failed_passive_replacement(
        self,
        *,
        passive_instance_name: str,
        approval_digest: str,
        retired_compute_id: str,
        retired_disk_id: str,
        current_observation: object,
        replacement_cycle: int = 1,
    ) -> VMHALifecycleState:
        """Append exact approval for replacing one failed provisioning passive."""

        transaction = self.transaction
        if (
            self.record_version != 4
            or self.status is not VMHALifecycleStatus.PROVISIONING
            or transaction is None
        ):
            raise ValueError("VM-HA passive replacement requires a v4 PROVISIONING checkpoint")
        if not _DIGEST.fullmatch(approval_digest):
            raise ValueError("VM-HA passive replacement approval digest is invalid")
        passive = next(
            (member for member in self.members if member.instance_name == passive_instance_name),
            None,
        )
        if passive is None or passive.role != "passive":
            raise ValueError("VM-HA passive replacement target is not the configured passive")
        if transaction.pending_effect is not None:
            raise ValueError("VM-HA passive replacement cannot supersede a pending effect")
        forbidden = (
            "stage-",
            "install-apply-lock-",
            "activate-",
            "verify-active-",
            "verify-passive-",
        )
        if any(effect.startswith(forbidden) for effect in transaction.completed_effects):
            raise ValueError("VM-HA passive replacement is too late after activation began")
        if replacement_cycle < 1:
            raise ValueError("VM-HA passive replacement cycle is invalid")
        required = (
            {
                f"provision-{passive_instance_name}-boot-disk",
                f"provision-{passive_instance_name}-compute",
            }
            if replacement_cycle == 1
            else {
                vm_ha_passive_replacement_effect(
                    passive_instance_name,
                    replacement_cycle - 1,
                    "create-boot-disk",
                ),
                vm_ha_passive_replacement_effect(
                    passive_instance_name,
                    replacement_cycle - 1,
                    "create-compute",
                ),
            }
        )
        if not required.issubset(transaction.completed_effects):
            raise ValueError("VM-HA passive replacement target was not created by this transaction")

        bindings = dict(transaction.resource_bindings)
        effective_bindings = vm_ha_effective_resource_bindings(bindings)
        if (
            effective_bindings.get(f"compute:{passive_instance_name}") != retired_compute_id
            or effective_bindings.get(f"disk:{passive_instance_name}") != retired_disk_id
        ):
            raise ValueError("VM-HA passive replacement target identity changed")
        approval_key = vm_ha_passive_replacement_binding_key(
            "approval", passive_instance_name, replacement_cycle
        )
        retired_compute_key = vm_ha_passive_replacement_binding_key(
            "retired-compute", passive_instance_name, replacement_cycle
        )
        retired_disk_key = vm_ha_passive_replacement_binding_key(
            "retired-disk", passive_instance_name, replacement_cycle
        )
        existing = {
            approval_key: approval_digest,
            retired_compute_key: retired_compute_id,
            retired_disk_key: retired_disk_id,
        }
        present = {key: bindings.get(key) for key in existing}
        if any(value is not None for value in present.values()):
            if present != existing:
                raise ValueError("VM-HA passive replacement approval identity changed")
            return self

        raw_observation = current_observation
        if not isinstance(raw_observation, Mapping):
            raise ValueError("VM-HA passive replacement observation is malformed")
        raw_members = raw_observation.get("members")
        if not isinstance(raw_members, list):
            raise ValueError("VM-HA passive replacement member observation is malformed")
        matches = [
            (index, item)
            for index, item in enumerate(raw_members)
            if isinstance(item, Mapping) and item.get("instance_name") == passive_instance_name
        ]
        if len(matches) != 1:
            raise ValueError("VM-HA passive replacement member is not canonical")
        member_index, raw_passive = matches[0]
        if (
            raw_passive.get("present") is not True
            or raw_passive.get("compute_id") != retired_compute_id
            or raw_passive.get("boot_disk_id") != retired_disk_id
            or raw_passive.get("aliases") != []
        ):
            raise ValueError("VM-HA passive replacement footprint is not isolated")
        normalized = normalize_vm_ha_observation(current_observation)
        changed = vm_ha_observation_changed_paths(transaction.observation, normalized)
        permitted = {f"/members/{member_index}/compute_revision"}
        unexpected = changed - permitted
        if unexpected:
            raise ValueError(
                "VM-HA passive replacement observed unrelated cloud drift at: "
                + ", ".join(sorted(unexpected))
            )
        effect = vm_ha_passive_replacement_effect(
            passive_instance_name,
            replacement_cycle,
            "intent",
        )
        return replace(
            self,
            transaction=transaction.advance(
                predecessor_sha256=self.record_sha256,
                checkpoint=f"after-{effect}",
                pending_effect=None,
                completed_effect=effect,
                resource_updates=existing,
                observation=normalized,
                observation_guard=None,
                accepted_cloud_operation_effect=None,
                accepted_cloud_operation_id=None,
            ),
        )

    def begin_effect(
        self,
        effect: str,
        *,
        observation: object | None = None,
        permitted_paths: tuple[str, ...] = (),
    ) -> VMHALifecycleState:
        if self.transaction is None:
            raise ValueError("VM-HA lifecycle effect requires a v3 transaction")
        if effect in self.transaction.completed_effects:
            return self
        if self.transaction.pending_effect not in {None, effect}:
            raise ValueError("VM-HA lifecycle has another pending effect")
        normalized = None if observation is None else normalize_vm_ha_observation(observation)
        if self.transaction.pending_effect == effect:
            pending_guard = self.transaction.observation_guard
            if normalized is not None:
                if pending_guard is None:
                    raise ValueError("VM-HA pending effect has no observation guard")
                changed = vm_ha_observation_changed_paths(pending_guard.pre_observation, normalized)
                unexpected = pending_guard.unpermitted(changed)
                if unexpected:
                    raise ValueError(
                        "VM-HA pending effect observed unrelated cloud drift at: "
                        + ", ".join(unexpected)
                    )
            return self
        if self.record_version != 4:
            raise ValueError("VM-HA lifecycle effects require a v4 transaction")
        effect_guard: VMHAEffectObservationGuard | None = None
        if normalized is not None:
            if self.transaction.observation and normalized != self.transaction.observation:
                raise ValueError("VM-HA trusted cloud state changed before the next effect")
            effect_guard = VMHAEffectObservationGuard(
                effect=effect,
                permitted_paths=tuple(sorted(set(permitted_paths))),
                pre_observation=normalized,
            )
        elif permitted_paths:
            raise ValueError("VM-HA permitted cloud paths require an observation")
        return replace(
            self,
            transaction=self.transaction.advance(
                predecessor_sha256=self.record_sha256,
                checkpoint=f"before-{effect}",
                pending_effect=effect,
                observation=(self.transaction.observation if normalized is None else normalized),
                observation_guard=effect_guard,
                accepted_cloud_operation_effect=None,
                accepted_cloud_operation_id=None,
            ),
        )

    def rewind_host_activation_for_owner_adoption(
        self,
        adoption_effect: str,
    ) -> VMHALifecycleState:
        """Rewind an interrupted later host effect before owner adoption.

        Owner adoption was added ahead of activation. A lifecycle written by an
        older apply may therefore already have a later host-only activation
        effect pending. Clearing that intent is safe only while its cloud
        observation and accepted-operation slots are empty; the effect remains
        incomplete and is replayed after adoption.
        """

        transaction = self.transaction
        if transaction is None or self.record_version != 4:
            raise ValueError("VM-HA owner adoption rewind requires a v4 transaction")
        if adoption_effect in transaction.completed_effects:
            return self
        pending = transaction.pending_effect
        if pending is None or pending == adoption_effect:
            return self
        if (
            self.status is not VMHALifecycleStatus.ACTIVATING
            or not adoption_effect.startswith("install-owner-adoption-")
            or not (
                pending.startswith("activate-")
                or pending
                in {
                    "verify-active-forwarding-and-routes",
                    "verify-passive-unlocked-non-forwarding",
                }
            )
            or transaction.observation_guard is not None
            or transaction.accepted_cloud_operation_effect is not None
            or transaction.accepted_cloud_operation_id is not None
        ):
            raise ValueError("VM-HA owner adoption cannot rewind the pending lifecycle effect")
        return replace(
            self,
            transaction=transaction.advance(
                predecessor_sha256=self.record_sha256,
                checkpoint=f"rewind-before-{adoption_effect}",
                pending_effect=None,
                observation_guard=None,
                accepted_cloud_operation_effect=None,
                accepted_cloud_operation_id=None,
            ),
        )

    def rewind_standby_replacement_inhibition_for_owner_refresh(
        self,
        *,
        owner_refresh_effect: str,
        inhibition_effect: str,
    ) -> VMHALifecycleState:
        """Insert a newly required owner refresh ahead of an interrupted inhibition.

        The inhibition write is host-local and idempotent. If an older client
        persisted its intent before the owner-refresh capability gate existed,
        this transition may clear only that exact intent. Any already-written
        remote inhibition remains effective and is reproved by the ordinary
        replay after the refreshed controller is active.
        """

        transaction = self.transaction
        if transaction is None or self.record_version != 4:
            raise ValueError("VM-HA standby replacement rewind requires a v4 transaction")
        if owner_refresh_effect in transaction.completed_effects:
            return self
        if transaction.pending_effect in {None, owner_refresh_effect}:
            return self
        bindings = dict(transaction.resource_bindings)
        candidates: list[tuple[VMHALifecycleMember, int]] = []
        for member in self.members:
            for cycle in vm_ha_passive_replacement_cycles(bindings, member.instance_name):
                if (
                    bindings.get(
                        vm_ha_passive_replacement_binding_key(
                            "approval",
                            member.instance_name,
                            cycle,
                        )
                    )
                    == transaction.approval_digest
                ):
                    candidates.append((member, cycle))
        if len(candidates) != 1:
            raise ValueError("VM-HA standby replacement rewind authority is ambiguous")
        target, cycle = candidates[0]
        owners = [member for member in self.members if member.instance_name != target.instance_name]
        if len(owners) != 1:
            raise ValueError("VM-HA standby replacement rewind owner is ambiguous")
        owner = owners[0]
        intent_effect = vm_ha_missing_standby_replacement_effect(
            target.instance_name,
            cycle,
            "intent",
        )
        cloud_effects = {
            vm_ha_missing_standby_replacement_effect(
                target.instance_name,
                cycle,
                action,
            )
            for action in ("create-boot-disk", "create-compute")
        }
        exact_effects = bool(
            owner_refresh_effect == f"prepare-live-peer-replacement-owner-v5-{owner.node_id}"
            and inhibition_effect == f"install-standby-replacement-inhibition-{owner.node_id}"
            and transaction.pending_effect == inhibition_effect
        )
        if not (
            self.status is VMHALifecycleStatus.PROVISIONING
            and transaction.approval_kind == "recovery"
            and exact_effects
            and intent_effect in transaction.completed_effects
            and not cloud_effects.intersection(transaction.completed_effects)
            and transaction.observation_guard is None
            and transaction.accepted_cloud_operation_effect is None
            and transaction.accepted_cloud_operation_id is None
            and owner.compute_id
            and not target.compute_id
            and not target.disk_id
        ):
            raise ValueError(
                "VM-HA standby replacement inhibition cannot be rewound for owner refresh"
            )
        return replace(
            self,
            transaction=transaction.advance(
                predecessor_sha256=self.record_sha256,
                checkpoint=f"rewind-before-{owner_refresh_effect}",
                pending_effect=None,
                observation_guard=None,
                accepted_cloud_operation_effect=None,
                accepted_cloud_operation_id=None,
            ),
        )

    def complete_effect(
        self,
        effect: str,
        *,
        resource_updates: Mapping[str, str] | None = None,
        observation: object | None = None,
    ) -> VMHALifecycleState:
        if self.transaction is None or self.transaction.pending_effect != effect:
            raise ValueError("VM-HA lifecycle effect completion has no matching intent")
        normalized = None if observation is None else normalize_vm_ha_observation(observation)
        guard = self.transaction.observation_guard
        if guard is not None:
            if normalized is None:
                raise ValueError("VM-HA cloud effect completion requires a stable observation")
            changed = vm_ha_observation_changed_paths(guard.pre_observation, normalized)
            unexpected = guard.unpermitted(changed)
            if unexpected:
                raise ValueError(
                    "VM-HA cloud effect changed unapproved observation paths: "
                    + ", ".join(unexpected)
                )
        elif normalized is not None:
            raise ValueError("VM-HA non-cloud effect cannot replace trusted cloud state")
        return replace(
            self,
            transaction=self.transaction.advance(
                predecessor_sha256=self.record_sha256,
                checkpoint=f"after-{effect}",
                pending_effect=None,
                completed_effect=effect,
                resource_updates=resource_updates,
                observation=(self.transaction.observation if normalized is None else normalized),
                observation_guard=None,
                accepted_cloud_operation_effect=None,
                accepted_cloud_operation_id=None,
            ),
        )

    def record_cloud_operation(
        self,
        effect: str,
        cloud_operation_id: str,
    ) -> VMHALifecycleState:
        """Persist the exact accepted Nebius operation before waiting for it."""

        if (
            self.record_version != 4
            or self.transaction is None
            or self.transaction.pending_effect != effect
        ):
            raise ValueError("VM-HA accepted cloud operation has no matching pending effect")
        if self.transaction.accepted_cloud_operation_id is not None:
            if (
                self.transaction.accepted_cloud_operation_effect != effect
                or self.transaction.accepted_cloud_operation_id != cloud_operation_id
            ):
                raise ValueError("VM-HA accepted cloud operation identity changed")
            return self
        return replace(
            self,
            transaction=self.transaction.advance(
                predecessor_sha256=self.record_sha256,
                checkpoint=f"accepted-{effect}",
                accepted_cloud_operation_effect=effect,
                accepted_cloud_operation_id=cloud_operation_id,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        if self.record_version == 2:
            return {
                "identity": self._identity_payload(legacy=True),
                "record_sha256": self.record_sha256,
                "schema": LIFECYCLE_SCHEMA_V2,
                "status": self.status.value,
            }
        assert self.transaction is not None
        return {
            "identity": self._identity_payload(),
            "record_sha256": self.record_sha256,
            "schema": LIFECYCLE_SCHEMA_V3 if self.record_version == 3 else LIFECYCLE_SCHEMA,
            "status": self.status.value,
            "transaction": self.transaction.to_dict(record_version=self.record_version),
        }

    @classmethod
    def from_dict(cls, value: Any) -> VMHALifecycleState:
        if not isinstance(value, dict):
            raise ValueError("VM-HA lifecycle state is malformed")
        schema = value.get("schema")
        if not isinstance(schema, str):
            raise ValueError("VM-HA lifecycle schema is unsupported")
        legacy = schema == LIFECYCLE_SCHEMA_V2
        record_version = {
            LIFECYCLE_SCHEMA_V2: 2,
            LIFECYCLE_SCHEMA_V3: 3,
            LIFECYCLE_SCHEMA: 4,
        }.get(schema)
        expected = (
            {"identity", "record_sha256", "schema", "status"}
            if legacy
            else {"identity", "record_sha256", "schema", "status", "transaction"}
        )
        if set(value) != expected or record_version is None:
            raise ValueError("VM-HA lifecycle schema is unsupported")
        identity = value.get("identity")
        legacy_identity = {
            "allocation_id",
            "allocation_name",
            "cluster_id",
            "gateway_name",
            "members",
            "project_id",
        }
        current_identity = legacy_identity | {"route_runtime_id", "route_targets"}
        if not isinstance(identity, dict) or set(identity) != (
            legacy_identity if legacy else current_identity
        ):
            raise ValueError("VM-HA lifecycle identity is malformed")
        string_fields = legacy_identity - {"members"}
        if any(not isinstance(identity.get(field), str) for field in string_fields):
            raise ValueError("VM-HA lifecycle identity is malformed")
        raw_members = identity.get("members")
        if not isinstance(raw_members, list) or len(raw_members) != 2:
            raise ValueError("VM-HA lifecycle member set is malformed")
        raw_targets: list[str] = []
        if not legacy:
            raw_target_values = identity.get("route_targets")
            if not isinstance(raw_target_values, list) or any(
                not isinstance(item, str) or not item for item in raw_target_values
            ):
                raise ValueError("VM-HA lifecycle route targets are malformed")
            raw_targets = [str(item) for item in raw_target_values]
            if not isinstance(identity.get("route_runtime_id"), str):
                raise ValueError("VM-HA lifecycle route runtime is malformed")
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
            members=tuple(
                VMHALifecycleMember.from_dict(member, legacy=legacy) for member in raw_members
            ),  # type: ignore[arg-type]
            route_runtime_id="" if legacy else identity["route_runtime_id"],
            route_targets=tuple(raw_targets),
            transaction=(
                None
                if legacy
                else VMHAMigrationTransaction.from_dict(
                    value["transaction"], record_version=record_version
                )
            ),
            record_version=record_version,
        )
        digest = value.get("record_sha256")
        if not isinstance(digest, str) or digest != state.record_sha256:
            raise ValueError("VM-HA lifecycle record digest does not match")
        return state

    @classmethod
    def start_provisioning(
        cls,
        *,
        project_id: str,
        gateway_name: str,
        cluster_id: str,
        allocation_name: str,
        members: tuple[VMHALifecycleMember, VMHALifecycleMember],
        operation_id: str,
        approval_kind: str,
        approval_digest: str,
        desired_state_digest: str,
        current_state_digest: str,
        v2_predecessor_sha256: str | None = None,
        predecessor_sha256: str | None = None,
        initial_resource_bindings: Mapping[str, str] | None = None,
        current_observation: object | None = None,
    ) -> VMHALifecycleState:
        return cls(
            status=VMHALifecycleStatus.PROVISIONING,
            project_id=project_id,
            gateway_name=gateway_name,
            cluster_id=cluster_id,
            allocation_id="",
            allocation_name=allocation_name,
            members=members,
            transaction=VMHAMigrationTransaction(
                operation_id=operation_id,
                approval_kind=approval_kind,
                approval_digest=approval_digest,
                desired_state_digest=desired_state_digest,
                current_state_digest=current_state_digest,
                checkpoint="approved-intent",
                pending_effect=None,
                completed_effects=(),
                resource_bindings=tuple(sorted((initial_resource_bindings or {}).items())),
                revision=1,
                predecessor_sha256=predecessor_sha256 or v2_predecessor_sha256,
                v2_predecessor_sha256=v2_predecessor_sha256,
                observation=(
                    ()
                    if current_observation is None
                    else normalize_vm_ha_observation(current_observation)
                ),
            ),
        )

    @classmethod
    def start_destruction(
        cls,
        previous: VMHALifecycleState,
        *,
        operation_id: str,
        approval_digest: str,
        desired_state_digest: str,
        current_state_digest: str,
        destroy_plan_json: str,
        current_observation: object,
        resolved_cloud_operation_id: str | None = None,
    ) -> VMHALifecycleState:
        """Start one identity-bound teardown from any authoritative v4 state."""

        transaction = previous.transaction
        if previous.record_version != 4 or transaction is None:
            raise ValueError("VM-HA destruction requires a v4 lifecycle authority")
        if previous.status is VMHALifecycleStatus.DESTROYED:
            raise ValueError("VM-HA destruction is already complete")
        bindings = dict(transaction.resource_bindings)
        for member in previous.members:
            if not member.public_allocation_id:
                continue
            if member.network_interface_name != "eth0":
                raise ValueError("VM-HA retained public allocation must use eth0")
            public_key = f"public-allocation:{member.instance_name}:{member.network_interface_name}"
            existing_public = bindings.get(public_key)
            if existing_public not in {None, member.public_allocation_id}:
                raise ValueError("VM-HA retained public allocation binding changed")
            bindings[public_key] = member.public_allocation_id
        bindings["destroy-plan"] = destroy_plan_json
        pending = transaction.pending_effect
        accepted = transaction.accepted_cloud_operation_id
        if accepted is not None:
            if resolved_cloud_operation_id != accepted:
                raise ValueError(
                    "VM-HA destruction must resolve the accepted cloud operation first"
                )
            bindings["destroy-resolved-cloud-operation"] = accepted
        elif pending is not None:
            if (
                transaction.observation_guard is not None
                or not vm_ha_activation_effect_is_host_only(pending)
            ):
                raise ValueError("VM-HA destruction cannot supersede an ambiguous cloud effect")
            bindings["destroy-superseded-host-effect"] = pending
        elif resolved_cloud_operation_id is not None:
            raise ValueError("VM-HA destruction resolved an operation that was not pending")

        successor = cls(
            status=VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
            project_id=previous.project_id,
            gateway_name=previous.gateway_name,
            cluster_id=previous.cluster_id,
            allocation_id=previous.allocation_id,
            allocation_name=previous.allocation_name,
            members=previous.members,
            route_runtime_id=previous.route_runtime_id,
            route_targets=previous.route_targets,
            transaction=VMHAMigrationTransaction(
                operation_id=operation_id,
                approval_kind="destruction",
                approval_digest=approval_digest,
                desired_state_digest=desired_state_digest,
                current_state_digest=current_state_digest,
                checkpoint="destruction-approved",
                pending_effect=None,
                completed_effects=(),
                resource_bindings=tuple(sorted(bindings.items())),
                revision=1,
                predecessor_sha256=previous.record_sha256,
                observation=normalize_vm_ha_observation(current_observation),
            ),
            record_version=4,
        )
        _validate_destruction_successor(previous, successor)
        return successor

    @classmethod
    def recover_interrupted_activation(
        cls,
        previous: VMHALifecycleState,
        *,
        members: tuple[VMHALifecycleMember, VMHALifecycleMember],
        operation_id: str,
        approval_digest: str,
        desired_state_digest: str,
        current_state_digest: str,
        initial_resource_bindings: Mapping[str, str],
        current_observation: object,
    ) -> VMHALifecycleState:
        """Start one explicitly approved apply after a fenced activation reset."""

        transaction = previous.transaction
        if (
            previous.record_version != 4
            or previous.status is not VMHALifecycleStatus.ACTIVATING
            or transaction is None
        ):
            raise ValueError("VM-HA activation recovery requires a v4 ACTIVATING checkpoint")
        if not vm_ha_activation_effect_is_host_only(transaction.pending_effect):
            raise ValueError("VM-HA activation recovery cannot supersede a cloud effect")
        if transaction.accepted_cloud_operation_id is not None:
            raise ValueError("VM-HA activation recovery has an accepted cloud operation")
        if desired_state_digest != transaction.desired_state_digest:
            raise ValueError("VM-HA activation recovery desired state changed")

        successor = cls(
            status=VMHALifecycleStatus.PROVISIONING,
            project_id=previous.project_id,
            gateway_name=previous.gateway_name,
            cluster_id=previous.cluster_id,
            allocation_id=previous.allocation_id,
            allocation_name=previous.allocation_name,
            members=members,
            route_runtime_id=previous.route_runtime_id,
            route_targets=previous.route_targets,
            transaction=VMHAMigrationTransaction(
                operation_id=operation_id,
                approval_kind="recovery",
                approval_digest=approval_digest,
                desired_state_digest=desired_state_digest,
                current_state_digest=current_state_digest,
                checkpoint="activation-recovery-approved",
                pending_effect=None,
                completed_effects=(),
                resource_bindings=tuple(sorted(initial_resource_bindings.items())),
                revision=1,
                predecessor_sha256=previous.record_sha256,
                observation=normalize_vm_ha_observation(current_observation),
            ),
            record_version=4,
        )
        _validate_activation_recovery_replacement(previous, successor)
        return successor

    @classmethod
    def start_missing_standby_replacement(
        cls,
        previous: VMHALifecycleState,
        *,
        target_instance_name: str,
        replacement_cycle: int,
        replacement_disk_name: str,
        operation_id: str,
        approval_digest: str,
        desired_state_digest: str,
        current_state_digest: str,
        current_observation: object,
        ssh_identity_rotation: Mapping[str, str] | None = None,
    ) -> VMHALifecycleState:
        """Start one creation-only replacement for the proven current non-owner."""

        transaction = previous.transaction
        if (
            previous.record_version != 4
            or previous.status is not VMHALifecycleStatus.ACTIVE
            or transaction is None
            or transaction.pending_effect is not None
            or transaction.accepted_cloud_operation_id is not None
        ):
            raise ValueError("VM-HA missing standby replacement requires a quiescent v4 ACTIVE")
        if not _DIGEST.fullmatch(approval_digest) or not _DIGEST.fullmatch(operation_id):
            raise ValueError("VM-HA missing standby replacement approval identity is invalid")
        if desired_state_digest != transaction.desired_state_digest:
            raise ValueError("VM-HA missing standby replacement desired state changed")
        target = next(
            (member for member in previous.members if member.instance_name == target_instance_name),
            None,
        )
        if target is None or not target.compute_id or not target.disk_id:
            raise ValueError("VM-HA missing standby replacement target identity is incomplete")
        bindings = dict(transaction.resource_bindings)
        effective = vm_ha_effective_resource_bindings(bindings)
        if (
            effective.get(f"compute:{target_instance_name}") != target.compute_id
            or effective.get(f"disk:{target_instance_name}") != target.disk_id
        ):
            raise ValueError("VM-HA missing standby replacement target binding changed")
        prior_cycles = vm_ha_passive_replacement_cycles(bindings, target_instance_name)
        expected_cycle = 1 if not prior_cycles else prior_cycles[-1] + 1
        if replacement_cycle != expected_cycle:
            raise ValueError("VM-HA missing standby replacement cycle is not monotonic")
        expected_disk_name = vm_ha_missing_standby_disk_name(
            gateway_name=previous.gateway_name,
            instance_name=target_instance_name,
            predecessor_sha256=previous.record_sha256,
            cycle=replacement_cycle,
        )
        if replacement_disk_name != expected_disk_name:
            raise ValueError("VM-HA missing standby replacement disk name changed")

        if not isinstance(current_observation, Mapping):
            raise ValueError("VM-HA missing standby replacement observation is malformed")
        raw_members = current_observation.get("members")
        shared = current_observation.get("shared_allocation")
        if not isinstance(raw_members, list) or not isinstance(shared, Mapping):
            raise ValueError("VM-HA missing standby replacement cloud evidence is incomplete")
        observed = {
            str(item.get("instance_name")): item
            for item in raw_members
            if isinstance(item, Mapping) and isinstance(item.get("instance_name"), str)
        }
        if set(observed) != {member.instance_name for member in previous.members}:
            raise ValueError("VM-HA missing standby replacement member evidence is incomplete")
        if observed[target_instance_name] != {
            "instance_name": target_instance_name,
            "present": False,
        }:
            raise ValueError("VM-HA missing standby replacement target absence is not exact")
        owner = shared.get("owner")
        if not isinstance(owner, Mapping):
            raise ValueError("VM-HA missing standby replacement has no exact owner")
        owner_members = [
            member
            for member in previous.members
            if member.compute_id == owner.get("compute_id")
            and member.network_interface_name == owner.get("network_interface_name")
        ]
        if len(owner_members) != 1 or owner_members[0].instance_name == target_instance_name:
            raise ValueError("VM-HA missing standby replacement target is not the non-owner")
        raw_owner = observed[owner_members[0].instance_name]
        if (
            raw_owner.get("present") is not True
            or raw_owner.get("state") != "running"
            or raw_owner.get("compute_id") != owner_members[0].compute_id
            or raw_owner.get("network_interface_name") != owner_members[0].network_interface_name
        ):
            raise ValueError("VM-HA missing standby replacement owner is not stable")

        approval_key = vm_ha_passive_replacement_binding_key(
            "approval", target_instance_name, replacement_cycle
        )
        retired_compute_key = vm_ha_passive_replacement_binding_key(
            "retired-compute", target_instance_name, replacement_cycle
        )
        retired_disk_key = vm_ha_passive_replacement_binding_key(
            "retired-disk", target_instance_name, replacement_cycle
        )
        disk_name_key = vm_ha_missing_standby_disk_name_binding_key(
            target_instance_name,
            replacement_cycle,
        )
        owner_sequences = vm_ha_missing_standby_owner_sequences(bindings)
        owner_sequence = 1 if not owner_sequences else owner_sequences[-1] + 1
        additions = {
            approval_key: approval_digest,
            retired_compute_key: target.compute_id,
            retired_disk_key: target.disk_id,
            disk_name_key: replacement_disk_name,
            vm_ha_missing_standby_owner_binding_key("instance", owner_sequence): owner_members[
                0
            ].instance_name,
            vm_ha_missing_standby_owner_binding_key("compute", owner_sequence): owner_members[
                0
            ].compute_id,
            vm_ha_missing_standby_owner_binding_key("nic", owner_sequence): owner_members[
                0
            ].network_interface_name,
        }
        if ssh_identity_rotation is not None:
            expected_rotation_fields = {
                "action",
                "hostname",
                "old_fingerprint",
                "predecessor_projection_sha256",
                "predecessor_receipt_sha256",
                "storage_owner",
                "trust_scope_sha256",
            }
            if (
                set(ssh_identity_rotation) != expected_rotation_fields
                or ssh_identity_rotation.get("action")
                != "generate-and-rotate-missing-non-owner-ssh-identity"
                or ssh_identity_rotation.get("hostname") != target_instance_name
                or ssh_identity_rotation.get("storage_owner") != "product-managed-default"
                or not _DIGEST.fullmatch(str(ssh_identity_rotation.get("trust_scope_sha256") or ""))
                or not str(ssh_identity_rotation.get("old_fingerprint") or "").startswith("SHA256:")
            ):
                raise ValueError("VM-HA missing standby SSH rotation intent is invalid")
            rotation_values = {
                "old-fingerprint": ssh_identity_rotation["old_fingerprint"],
                "trust-scope": ssh_identity_rotation["trust_scope_sha256"],
                "predecessor-receipt": ssh_identity_rotation["predecessor_receipt_sha256"],
                "predecessor-projection": ssh_identity_rotation["predecessor_projection_sha256"],
                "storage-owner": ssh_identity_rotation["storage_owner"],
            }
            additions.update(
                {
                    vm_ha_missing_standby_ssh_binding_key(
                        kind,
                        target_instance_name,
                        replacement_cycle,
                    ): value
                    for kind, value in rotation_values.items()
                }
            )
        if any(key in bindings for key in additions):
            raise ValueError("VM-HA missing standby replacement audit identity already exists")
        bindings.update(additions)
        successor_members = tuple(
            replace(member, compute_id="", compute_revision="", disk_id="")
            if member.instance_name == target_instance_name
            else member
            for member in previous.members
        )
        intent = vm_ha_missing_standby_replacement_effect(
            target_instance_name,
            replacement_cycle,
            "intent",
        )
        successor = cls(
            status=VMHALifecycleStatus.PROVISIONING,
            project_id=previous.project_id,
            gateway_name=previous.gateway_name,
            cluster_id=previous.cluster_id,
            allocation_id=previous.allocation_id,
            allocation_name=previous.allocation_name,
            members=cast(
                tuple[VMHALifecycleMember, VMHALifecycleMember],
                successor_members,
            ),
            route_runtime_id=previous.route_runtime_id,
            route_targets=previous.route_targets,
            transaction=VMHAMigrationTransaction(
                operation_id=operation_id,
                approval_kind="recovery",
                approval_digest=approval_digest,
                desired_state_digest=desired_state_digest,
                current_state_digest=current_state_digest,
                checkpoint="missing-standby-replacement-approved",
                pending_effect=None,
                completed_effects=(intent,),
                resource_bindings=tuple(sorted(bindings.items())),
                revision=1,
                predecessor_sha256=previous.record_sha256,
                observation=normalize_vm_ha_observation(current_observation),
            ),
            record_version=4,
        )
        _validate_missing_standby_replacement_successor(previous, successor)
        return successor

    @classmethod
    def successor_from_v2(
        cls,
        previous: VMHALifecycleState,
        *,
        operation_id: str,
        approval_kind: str,
        approval_digest: str,
        desired_state_digest: str,
        current_state_digest: str,
        initial_resource_bindings: Mapping[str, str] | None = None,
        observed_members: tuple[VMHALifecycleMember, VMHALifecycleMember] | None = None,
        current_observation: object | None = None,
    ) -> VMHALifecycleState:
        """Create the first mutating v4 successor without rewriting a v2 read."""

        if not previous.is_legacy_v2:
            raise ValueError("VM-HA lifecycle successor source is not v2")
        return replace(
            previous,
            status=VMHALifecycleStatus.PROVISIONING,
            members=observed_members or previous.members,
            transaction=VMHAMigrationTransaction(
                operation_id=operation_id,
                approval_kind=approval_kind,
                approval_digest=approval_digest,
                desired_state_digest=desired_state_digest,
                current_state_digest=current_state_digest,
                checkpoint="v2-successor",
                pending_effect=None,
                completed_effects=(),
                resource_bindings=tuple(sorted((initial_resource_bindings or {}).items())),
                revision=1,
                predecessor_sha256=previous.record_sha256,
                v2_predecessor_sha256=previous.record_sha256,
                observation=(
                    ()
                    if current_observation is None
                    else normalize_vm_ha_observation(current_observation)
                ),
            ),
            record_version=4,
        )

    @classmethod
    def successor_from_v3(
        cls,
        previous: VMHALifecycleState,
        *,
        current_observation: object,
    ) -> VMHALifecycleState:
        """Upgrade one quiescent v3 transaction after a fresh authoritative reread."""

        if previous.record_version != 3 or previous.transaction is None:
            raise ValueError("VM-HA lifecycle successor source is not v3")
        if previous.transaction.pending_effect is not None:
            raise ValueError(
                "VM-HA lifecycle v3 has a pending effect; explicit recovery is required"
            )
        transaction = replace(
            previous.transaction,
            checkpoint="v3-successor",
            revision=previous.transaction.revision + 1,
            predecessor_sha256=previous.record_sha256,
            observation=normalize_vm_ha_observation(current_observation),
            observation_guard=None,
            accepted_cloud_operation_effect=None,
            accepted_cloud_operation_id=None,
        )
        return replace(previous, transaction=transaction, record_version=4)


def _validate_successor(
    previous: VMHALifecycleState,
    successor: VMHALifecycleState,
) -> None:
    if successor.transaction is None:
        raise ValueError("VM-HA lifecycle successor must use v3")
    if successor.transaction.predecessor_sha256 != previous.record_sha256:
        raise ValueError("VM-HA lifecycle predecessor compare-and-swap failed")
    if (
        successor.project_id != previous.project_id
        or successor.gateway_name != previous.gateway_name
    ):
        raise ValueError("VM-HA lifecycle project and gateway scope cannot change")
    if previous.transaction is None:
        if successor.transaction.revision != 1:
            raise ValueError("VM-HA lifecycle revision is not monotonic")
        if (
            successor.status not in {previous.status, VMHALifecycleStatus.PROVISIONING}
            or successor.transaction.v2_predecessor_sha256 != previous.record_sha256
            or not previous.has_same_identity(successor)
        ):
            raise ValueError("VM-HA lifecycle v2 successor is not approved")
        return
    if (
        successor.transaction.operation_id != previous.transaction.operation_id
        and successor.transaction.approval_kind == "destruction"
    ):
        _validate_destruction_successor(previous, successor)
        return
    if (
        previous.status is VMHALifecycleStatus.DESTROYED
        and successor.status is VMHALifecycleStatus.PROVISIONING
        and successor.transaction.operation_id != previous.transaction.operation_id
    ):
        _validate_destroyed_reprovision_successor(previous, successor)
        return
    if (
        previous.status is VMHALifecycleStatus.ACTIVATING
        and successor.status is VMHALifecycleStatus.PROVISIONING
        and successor.transaction.operation_id != previous.transaction.operation_id
    ):
        _validate_activation_recovery_replacement(previous, successor)
        return
    if (
        previous.status in {VMHALifecycleStatus.ACTIVE, VMHALifecycleStatus.REMOVED}
        and successor.status is VMHALifecycleStatus.PROVISIONING
        and successor.transaction.operation_id != previous.transaction.operation_id
    ):
        if (
            previous.status is VMHALifecycleStatus.ACTIVE
            and successor.transaction.checkpoint == "missing-standby-replacement-approved"
        ):
            _validate_missing_standby_replacement_successor(previous, successor)
            return
        if successor.transaction.revision != 1:
            raise ValueError("VM-HA lifecycle replacement transaction must start at revision 1")
        if previous.status is VMHALifecycleStatus.ACTIVE and not previous.has_same_identity(
            successor
        ):
            raise ValueError("VM-HA lifecycle active identity changed between transactions")
        return
    if successor.transaction.revision != previous.transaction.revision + 1:
        raise ValueError("VM-HA lifecycle revision is not monotonic")
    if successor.transaction.operation_id != previous.transaction.operation_id:
        raise ValueError("VM-HA lifecycle operation identity cannot change")
    for field in (
        "approval_kind",
        "approval_digest",
        "desired_state_digest",
        "current_state_digest",
        "v2_predecessor_sha256",
    ):
        if getattr(successor.transaction, field) != getattr(previous.transaction, field):
            raise ValueError(f"VM-HA lifecycle transaction {field} cannot change")
    previous_bindings = dict(previous.transaction.resource_bindings)
    successor_bindings = dict(successor.transaction.resource_bindings)
    if any(successor_bindings.get(key) != value for key, value in previous_bindings.items()):
        raise ValueError("VM-HA lifecycle resource identity cannot be rebound")
    if not set(previous.transaction.completed_effects).issubset(
        successor.transaction.completed_effects
    ):
        raise ValueError("VM-HA lifecycle completed effects cannot be removed")
    _validate_missing_standby_ssh_progress(successor)
    permitted: dict[VMHALifecycleStatus, set[VMHALifecycleStatus]] = {
        VMHALifecycleStatus.PROVISIONING: {
            VMHALifecycleStatus.PROVISIONING,
            VMHALifecycleStatus.ACTIVATING,
            VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
        },
        VMHALifecycleStatus.ACTIVATING: {
            VMHALifecycleStatus.ACTIVATING,
            VMHALifecycleStatus.ACTIVE,
            VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
        },
        VMHALifecycleStatus.ACTIVE: {
            VMHALifecycleStatus.ACTIVE,
            VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
        },
        VMHALifecycleStatus.REMOVAL_IN_PROGRESS: {
            VMHALifecycleStatus.REMOVAL_IN_PROGRESS,
            VMHALifecycleStatus.REMOVED,
            VMHALifecycleStatus.DESTROYED,
        },
        VMHALifecycleStatus.REMOVED: {VMHALifecycleStatus.REMOVED},
        VMHALifecycleStatus.DESTROYED: {VMHALifecycleStatus.DESTROYED},
    }
    if successor.status not in permitted[previous.status]:
        raise ValueError("VM-HA lifecycle status transition is invalid")
    if previous.status is VMHALifecycleStatus.PROVISIONING:
        _validate_provisioning_identity_advance(previous, successor)
    elif not previous.has_same_identity(successor):
        raise ValueError("VM-HA lifecycle authoritative identity cannot change")


def _validate_destruction_successor(
    previous: VMHALifecycleState,
    successor: VMHALifecycleState,
) -> None:
    """Validate the sole new transaction allowed to supersede HA convergence."""

    before = previous.transaction
    after = successor.transaction
    if before is None or after is None:
        raise ValueError("VM-HA destruction transaction is incomplete")
    before_bindings = dict(before.resource_bindings)
    after_bindings = dict(after.resource_bindings)
    if (
        previous.record_version != 4
        or previous.status is VMHALifecycleStatus.DESTROYED
        or successor.record_version != 4
        or successor.status is not VMHALifecycleStatus.REMOVAL_IN_PROGRESS
        or after.approval_kind != "destruction"
        or after.revision != 1
        or after.predecessor_sha256 != previous.record_sha256
        or after.operation_id == before.operation_id
        or not previous.has_same_identity(successor)
        or not after_bindings.get("destroy-plan")
        or any(after_bindings.get(key) != value for key, value in before_bindings.items())
    ):
        raise ValueError("VM-HA destruction successor is not exact")
    if before.accepted_cloud_operation_id is not None:
        if (
            after_bindings.get("destroy-resolved-cloud-operation")
            != before.accepted_cloud_operation_id
        ):
            raise ValueError("VM-HA destruction lost the resolved cloud operation")
    elif before.pending_effect is not None:
        if (
            before.observation_guard is not None
            or not vm_ha_activation_effect_is_host_only(before.pending_effect)
            or after_bindings.get("destroy-superseded-host-effect") != before.pending_effect
        ):
            raise ValueError("VM-HA destruction cannot supersede the pending effect")
    elif any(
        key in after_bindings
        for key in (
            "destroy-resolved-cloud-operation",
            "destroy-superseded-host-effect",
        )
    ):
        raise ValueError("VM-HA destruction contains an unbound predecessor effect")


def _validate_destroyed_reprovision_successor(
    previous: VMHALifecycleState,
    successor: VMHALifecycleState,
) -> None:
    """Allow a clean new transaction only after verified terminal destruction."""

    before = previous.transaction
    after = successor.transaction
    if before is None or after is None:
        raise ValueError("VM-HA destroyed reprovision transaction is incomplete")
    allowed_fresh_binding = re.compile(
        r"^(?:public-allocation:.+:.+|route-targets-digest|credential-(?:service-account|authorized-key|sha256):.+)$"
    )
    expected_public_bindings = vm_ha_destroyed_retained_public_bindings(previous)
    actual_public_bindings = {
        key: value for key, value in after.resource_bindings if key.startswith("public-allocation:")
    }
    member_cloud_identity_present = any(
        (
            member.compute_id,
            member.network_interface_name,
            member.public_ip,
            member.compute_revision,
            member.disk_id,
            member.network_interface_subnet_id,
            member.primary_allocation_id,
            member.public_allocation_id,
            member.alias_allocation_ids,
        )
        != ("", "", "", "", "", "", "", "", ())
        for member in successor.members
    )
    if (
        previous.record_version != 4
        or previous.status is not VMHALifecycleStatus.DESTROYED
        or before.approval_kind != "destruction"
        or before.checkpoint != "destruction-verified-absent"
        or before.pending_effect is not None
        or before.accepted_cloud_operation_id is not None
        or successor.record_version != 4
        or successor.status is not VMHALifecycleStatus.PROVISIONING
        or successor.allocation_id
        or successor.route_runtime_id
        or successor.route_targets
        or member_cloud_identity_present
        or after.approval_kind != "migration"
        or after.checkpoint != "approved-intent"
        or after.revision != 1
        or after.predecessor_sha256 != previous.record_sha256
        or after.pending_effect is not None
        or after.accepted_cloud_operation_id is not None
        or after.completed_effects
        or actual_public_bindings != expected_public_bindings
        or any(
            allowed_fresh_binding.fullmatch(key) is None for key, _value in after.resource_bindings
        )
    ):
        raise ValueError("VM-HA destroyed lifecycle cannot start an inexact reprovision")


def _validate_missing_standby_replacement_successor(
    previous: VMHALifecycleState,
    successor: VMHALifecycleState,
) -> None:
    """Validate the sole ACTIVE replacement that may retire one non-owner identity."""

    before = previous.transaction
    after = successor.transaction
    if before is None or after is None:
        raise ValueError("VM-HA missing standby replacement transaction is incomplete")
    if (
        previous.record_version != 4
        or previous.status is not VMHALifecycleStatus.ACTIVE
        or successor.record_version != 4
        or successor.status is not VMHALifecycleStatus.PROVISIONING
        or after.approval_kind != "recovery"
        or after.revision != 1
        or after.predecessor_sha256 != previous.record_sha256
        or after.operation_id == before.operation_id
        or after.desired_state_digest != before.desired_state_digest
        or after.pending_effect is not None
        or after.observation_guard is not None
        or after.accepted_cloud_operation_effect is not None
        or after.accepted_cloud_operation_id is not None
        or after.checkpoint != "missing-standby-replacement-approved"
    ):
        raise ValueError("VM-HA missing standby replacement successor is invalid")
    for field in (
        "project_id",
        "gateway_name",
        "cluster_id",
        "allocation_id",
        "allocation_name",
        "route_runtime_id",
        "route_targets",
    ):
        if getattr(previous, field) != getattr(successor, field):
            raise ValueError(f"VM-HA missing standby replacement {field} changed")

    before_bindings = dict(before.resource_bindings)
    after_bindings = dict(after.resource_bindings)
    if any(after_bindings.get(key) != value for key, value in before_bindings.items()):
        raise ValueError("VM-HA missing standby replacement changed prior audit history")
    candidates: list[tuple[VMHALifecycleMember, int]] = []
    for member in previous.members:
        for cycle in vm_ha_passive_replacement_cycles(after_bindings, member.instance_name):
            approval_key = vm_ha_passive_replacement_binding_key(
                "approval", member.instance_name, cycle
            )
            if (
                approval_key not in before_bindings
                and after_bindings.get(approval_key) == after.approval_digest
            ):
                candidates.append((member, cycle))
    if len(candidates) != 1:
        raise ValueError("VM-HA missing standby replacement target binding is not exact")
    target_before, cycle = candidates[0]
    owner_sequences = vm_ha_missing_standby_owner_sequences(after_bindings)
    previous_owner_sequences = vm_ha_missing_standby_owner_sequences(before_bindings)
    owner_sequence = 1 if not previous_owner_sequences else previous_owner_sequences[-1] + 1
    if not owner_sequences or owner_sequences[-1] != owner_sequence:
        raise ValueError("VM-HA missing standby replacement owner audit is not monotonic")
    owner_instance = after_bindings.get(
        vm_ha_missing_standby_owner_binding_key("instance", owner_sequence)
    )
    owner_compute = after_bindings.get(
        vm_ha_missing_standby_owner_binding_key("compute", owner_sequence)
    )
    owner_nic = after_bindings.get(vm_ha_missing_standby_owner_binding_key("nic", owner_sequence))
    owners = [
        member
        for member in previous.members
        if member.instance_name == owner_instance
        and member.compute_id == owner_compute
        and member.network_interface_name == owner_nic
    ]
    if len(owners) != 1 or owners[0].instance_name == target_before.instance_name:
        raise ValueError("VM-HA missing standby replacement owner binding is not exact")
    target_after = next(
        member
        for member in successor.members
        if member.instance_name == target_before.instance_name
    )
    owner_after = next(
        member for member in successor.members if member.instance_name == owners[0].instance_name
    )
    if owner_after != owners[0]:
        raise ValueError("VM-HA missing standby replacement serving owner changed")
    expected_target = replace(target_before, compute_id="", compute_revision="", disk_id="")
    if target_after != expected_target:
        raise ValueError("VM-HA missing standby replacement changed logical member identity")

    approval_key = vm_ha_passive_replacement_binding_key(
        "approval", target_before.instance_name, cycle
    )
    retired_compute_key = vm_ha_passive_replacement_binding_key(
        "retired-compute", target_before.instance_name, cycle
    )
    retired_disk_key = vm_ha_passive_replacement_binding_key(
        "retired-disk", target_before.instance_name, cycle
    )
    disk_name_key = vm_ha_missing_standby_disk_name_binding_key(
        target_before.instance_name,
        cycle,
    )
    expected_additions = {
        approval_key: after.approval_digest,
        retired_compute_key: target_before.compute_id,
        retired_disk_key: target_before.disk_id,
        disk_name_key: vm_ha_missing_standby_disk_name(
            gateway_name=previous.gateway_name,
            instance_name=target_before.instance_name,
            predecessor_sha256=previous.record_sha256,
            cycle=cycle,
        ),
        vm_ha_missing_standby_owner_binding_key("instance", owner_sequence): owners[
            0
        ].instance_name,
        vm_ha_missing_standby_owner_binding_key("compute", owner_sequence): owners[0].compute_id,
        vm_ha_missing_standby_owner_binding_key("nic", owner_sequence): owners[
            0
        ].network_interface_name,
    }
    rotation_kinds = (
        "old-fingerprint",
        "trust-scope",
        "predecessor-receipt",
        "predecessor-projection",
        "storage-owner",
    )
    rotation_keys = {
        kind: vm_ha_missing_standby_ssh_binding_key(
            kind,
            target_before.instance_name,
            cycle,
        )
        for kind in rotation_kinds
    }
    present_rotation = {
        kind: after_bindings[key] for kind, key in rotation_keys.items() if key in after_bindings
    }
    if present_rotation and set(present_rotation) != set(rotation_kinds):
        raise ValueError("VM-HA missing standby SSH rotation audit is incomplete")
    if present_rotation:
        if (
            not present_rotation["old-fingerprint"].startswith("SHA256:")
            or not _DIGEST.fullmatch(present_rotation["trust-scope"])
            or present_rotation["storage-owner"] != "product-managed-default"
            or any(
                value != "absent" and not _DIGEST.fullmatch(value)
                for value in (
                    present_rotation["predecessor-receipt"],
                    present_rotation["predecessor-projection"],
                )
            )
        ):
            raise ValueError("VM-HA missing standby SSH rotation audit is invalid")
        expected_additions.update(
            {rotation_keys[kind]: value for kind, value in present_rotation.items()}
        )
    additions = {key: value for key, value in after_bindings.items() if key not in before_bindings}
    if additions != expected_additions:
        raise ValueError("VM-HA missing standby replacement audit bindings are invalid")
    expected_intent = vm_ha_missing_standby_replacement_effect(
        target_before.instance_name,
        cycle,
        "intent",
    )
    if after.completed_effects != (expected_intent,):
        raise ValueError("VM-HA missing standby replacement intent is not exact")


def _validate_missing_standby_ssh_progress(state: VMHALifecycleState) -> None:
    """Keep SSH rotation checkpoints complete and ahead of cloud creation."""

    transaction = state.transaction
    if transaction is None:
        return
    bindings = dict(transaction.resource_bindings)
    candidates: list[tuple[str, int]] = []
    for member in state.members:
        for cycle in vm_ha_passive_replacement_cycles(bindings, member.instance_name):
            if (
                bindings.get(
                    vm_ha_passive_replacement_binding_key(
                        "approval",
                        member.instance_name,
                        cycle,
                    )
                )
                == transaction.approval_digest
            ):
                candidates.append((member.instance_name, cycle))
    if len(candidates) != 1:
        return
    hostname, cycle = candidates[0]
    old_key = vm_ha_missing_standby_ssh_binding_key(
        "old-fingerprint",
        hostname,
        cycle,
    )
    if old_key not in bindings:
        return
    initial_kinds = (
        "old-fingerprint",
        "trust-scope",
        "predecessor-receipt",
        "predecessor-projection",
        "storage-owner",
    )
    stage_kinds = (
        "stage-token",
        "new-fingerprint",
        "successor-receipt",
        "successor-projection",
    )
    if any(
        vm_ha_missing_standby_ssh_binding_key(kind, hostname, cycle) not in bindings
        for kind in initial_kinds
    ):
        raise ValueError("VM-HA missing standby SSH rotation intent is incomplete")
    stage_effect = vm_ha_missing_standby_replacement_effect(
        hostname,
        cycle,
        "stage-ssh-identity",
    )
    publish_effect = vm_ha_missing_standby_replacement_effect(
        hostname,
        cycle,
        "publish-ssh-trust",
    )
    create_effects = {
        vm_ha_missing_standby_replacement_effect(
            hostname,
            cycle,
            action,
        )
        for action in ("create-boot-disk", "create-compute")
    }
    stage_bound = all(
        vm_ha_missing_standby_ssh_binding_key(kind, hostname, cycle) in bindings
        for kind in stage_kinds
    )
    if stage_bound != (stage_effect in transaction.completed_effects):
        raise ValueError("VM-HA missing standby SSH staged identity is not exact")
    if (
        publish_effect in transaction.completed_effects
        or transaction.pending_effect == publish_effect
    ) and not stage_bound:
        raise ValueError("VM-HA missing standby SSH trust published before identity staging")
    cloud_started = bool(
        create_effects.intersection(transaction.completed_effects)
        or transaction.pending_effect in create_effects
    )
    if cloud_started and publish_effect not in transaction.completed_effects:
        raise ValueError("VM-HA missing standby cloud creation preceded SSH trust publication")


def _validate_activation_recovery_replacement(
    previous: VMHALifecycleState,
    successor: VMHALifecycleState,
) -> None:
    """Validate the sole ACTIVATING-to-PROVISIONING replacement transaction."""

    before = previous.transaction
    after = successor.transaction
    if before is None or after is None:
        raise ValueError("VM-HA activation recovery transaction is incomplete")
    if (
        previous.record_version != 4
        or previous.status is not VMHALifecycleStatus.ACTIVATING
        or successor.record_version != 4
        or successor.status is not VMHALifecycleStatus.PROVISIONING
        or after.approval_kind != "recovery"
        or after.revision != 1
        or after.predecessor_sha256 != previous.record_sha256
        or after.operation_id == before.operation_id
        or after.desired_state_digest != before.desired_state_digest
        or after.pending_effect is not None
        or after.completed_effects
        or after.observation_guard is not None
        or after.accepted_cloud_operation_id is not None
    ):
        raise ValueError("VM-HA activation recovery successor is invalid")
    if not vm_ha_activation_effect_is_host_only(before.pending_effect):
        raise ValueError("VM-HA activation recovery cannot supersede a cloud effect")
    if before.accepted_cloud_operation_id is not None:
        raise ValueError("VM-HA activation recovery has an accepted cloud operation")
    for field in (
        "project_id",
        "gateway_name",
        "cluster_id",
        "allocation_id",
        "allocation_name",
        "route_runtime_id",
        "route_targets",
    ):
        if getattr(previous, field) != getattr(successor, field):
            raise ValueError(f"VM-HA activation recovery {field} changed")

    for old_member, new_member in zip(previous.members, successor.members, strict=True):
        for field in (
            "instance_index",
            "instance_name",
            "node_id",
            "role",
            "compute_id",
            "disk_id",
            "network_interface_name",
            "network_interface_subnet_id",
            "primary_allocation_id",
            "public_allocation_id",
            "public_ip",
        ):
            if getattr(old_member, field) != getattr(new_member, field):
                raise ValueError(f"VM-HA activation recovery member {field} changed")
        if old_member.compute_revision != new_member.compute_revision:
            try:
                old_revision = int(old_member.compute_revision)
                new_revision = int(new_member.compute_revision)
            except ValueError as error:
                raise ValueError("VM-HA activation recovery member revision is invalid") from error
            if old_revision <= 0 or new_revision <= old_revision:
                raise ValueError("VM-HA activation recovery member revision did not advance")

    active = next(member for member in successor.members if member.role == "active")
    passive = next(member for member in successor.members if member.role == "passive")
    if active.alias_allocation_ids != (successor.allocation_id,) or passive.alias_allocation_ids:
        raise ValueError(
            "VM-HA activation recovery requires the exact configured-active alias owner"
        )

    before_bindings = vm_ha_effective_resource_bindings(dict(before.resource_bindings))
    after_bindings = vm_ha_effective_resource_bindings(dict(after.resource_bindings))
    immutable_keys = {
        key
        for key in before_bindings
        if key == "shared-allocation-id"
        or key == "route-runtime-id"
        or key == "route-targets-digest"
        or key.startswith(
            (
                "compute:",
                "disk:",
                "primary-allocation:",
                "public-allocation:",
            )
        )
    }
    if any(after_bindings.get(key) != before_bindings[key] for key in immutable_keys):
        raise ValueError("VM-HA activation recovery resource identity changed")
    if (
        after_bindings.get("shared-allocation-owner-compute") != active.compute_id
        or after_bindings.get("shared-allocation-owner-nic") != active.network_interface_name
    ):
        raise ValueError("VM-HA activation recovery owner binding is not configured-active")


def _validate_provisioning_identity_advance(
    previous: VMHALifecycleState,
    successor: VMHALifecycleState,
) -> None:
    """Allow only approved fill-once identity completion during provisioning."""

    for field in ("project_id", "gateway_name", "cluster_id", "allocation_name"):
        if getattr(previous, field) != getattr(successor, field):
            raise ValueError(f"VM-HA lifecycle {field} cannot change")
    for field in ("allocation_id", "route_runtime_id"):
        before = getattr(previous, field)
        after = getattr(successor, field)
        if before and before != after:
            raise ValueError(f"VM-HA lifecycle {field} cannot be rebound")
    if previous.route_targets and previous.route_targets != successor.route_targets:
        raise ValueError("VM-HA lifecycle route targets cannot be rebound")

    successor_allocation_id = successor.allocation_id
    previous_bindings = vm_ha_effective_resource_bindings(
        dict(previous.transaction.resource_bindings)  # type: ignore[union-attr]
    )
    retained_owner: tuple[str, str] | None = None
    if previous.allocation_id:
        bound_allocation = previous_bindings.get("shared-allocation-id")
        bound_compute = previous_bindings.get("shared-allocation-owner-compute")
        bound_nic = previous_bindings.get("shared-allocation-owner-nic")
        if bound_compute is not None or bound_nic is not None:
            if bound_allocation != previous.allocation_id or not bound_compute or not bound_nic:
                raise ValueError("VM-HA managed reapply owner binding is incomplete")
            retained_owner = (bound_compute, bound_nic)
    member_pairs = tuple(zip(previous.members, successor.members, strict=True))
    for before, after in member_pairs:
        for field in ("instance_index", "instance_name", "node_id", "role"):
            if getattr(before, field) != getattr(after, field):
                raise ValueError(f"VM-HA lifecycle member {field} cannot change")
        for field in (
            "compute_id",
            "disk_id",
            "network_interface_name",
            "network_interface_subnet_id",
            "primary_allocation_id",
            "public_allocation_id",
            "public_ip",
        ):
            previous_value = getattr(before, field)
            if previous_value and previous_value != getattr(after, field):
                raise ValueError(f"VM-HA lifecycle member {field} cannot be rebound")

        before_aliases = set(before.alias_allocation_ids)
        after_aliases = set(after.alias_allocation_ids)
        if retained_owner is None:
            permitted_aliases = set(before_aliases)
            if successor_allocation_id and after.role == "active":
                permitted_aliases.add(successor_allocation_id)
            if frozenset(after_aliases) not in {
                frozenset(before_aliases),
                frozenset(permitted_aliases),
            }:
                raise ValueError("VM-HA lifecycle member aliases cannot be rebound")
    if retained_owner is not None:
        aliases_unchanged = all(
            set(after.alias_allocation_ids) == set(before.alias_allocation_ids)
            for before, after in member_pairs
        )
        aliases_refreshed = all(
            set(after.alias_allocation_ids)
            == (
                (set(before.alias_allocation_ids) - {successor_allocation_id})
                | (
                    {successor_allocation_id}
                    if (after.compute_id, after.network_interface_name) == retained_owner
                    else set()
                )
            )
            for before, after in member_pairs
        )
        if not aliases_unchanged and not aliases_refreshed:
            raise ValueError("VM-HA lifecycle member aliases cannot be rebound")
    if retained_owner is not None and not aliases_unchanged:
        exact_owners = [
            member
            for member in successor.members
            if successor_allocation_id in member.alias_allocation_ids
            and (member.compute_id, member.network_interface_name) == retained_owner
        ]
        if len(exact_owners) != 1:
            raise ValueError("VM-HA managed reapply owner is not exact")

    for before, after in member_pairs:
        if before.compute_revision and before.compute_revision != after.compute_revision:
            try:
                previous_revision = int(before.compute_revision)
                current_revision = int(after.compute_revision)
            except ValueError as error:
                raise ValueError(
                    "VM-HA lifecycle member compute revision cannot be rebound"
                ) from error
            if previous_revision <= 0 or current_revision <= previous_revision:
                raise ValueError("VM-HA lifecycle member compute revision cannot be rebound")


class VMHAApplyLock:
    """Exclusive local apply-writer lock keyed by canonical project and gateway."""

    def __init__(
        self,
        *,
        project_id: str,
        gateway_name: str,
        runtime_dir: Path | None = None,
    ) -> None:
        if not project_id or not gateway_name:
            raise ValueError("Gateway apply lock requires project and gateway identity")
        if runtime_dir is not None:
            root = runtime_dir
        elif os.environ.get("XDG_RUNTIME_DIR"):
            root = Path(os.environ["XDG_RUNTIME_DIR"]) / "nebius-vpngw-apply-locks"
        else:
            root = Path(tempfile.gettempdir()) / (f"nebius-vpngw-apply-locks-{os.getuid()}")
        key = hashlib.sha256(f"{project_id}\0{gateway_name}".encode()).hexdigest()
        self.path = root / f"{key}.lock"
        self._identity = {"gateway_name": gateway_name, "project_id": project_id}
        self._descriptor = -1

    def __enter__(self) -> VMHAApplyLock:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_metadata = self.path.parent.lstat()
        if (
            stat.S_ISLNK(directory_metadata.st_mode)
            or not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != os.getuid()
        ):
            raise RuntimeError("Gateway apply lock runtime directory is not owner-controlled")
        os.chmod(self.path.parent, 0o700)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("Gateway apply lock is not a regular file")
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    "another gateway apply owns the project and gateway lock"
                ) from error
            payload = _canonical_json(self._identity) + b"\n"
            os.ftruncate(descriptor, 0)
            os.write(descriptor, payload)
            os.fsync(descriptor)
            self._descriptor = descriptor
            return self
        except Exception:
            os.close(descriptor)
            raise

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._descriptor >= 0:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = -1


@dataclass(frozen=True)
class VMHALifecycleSnapshot:
    """Immutable, owner-controlled lifecycle evidence used for legacy recovery."""

    path: Path
    state: VMHALifecycleState
    content_sha256: str
    file_identity: tuple[int, int, int, int, int, int, int, int]

    @staticmethod
    def _read_file(
        path: Path,
    ) -> tuple[bytes, tuple[int, int, int, int, int, int, int, int]] | None:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ValueError("VM-HA lifecycle trust anchor is unavailable") from error
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_size <= 0
                or before.st_size > 1024 * 1024
                or before.st_nlink != 1
                or before.st_mode & 0o022
                or (hasattr(os, "getuid") and before.st_uid != os.getuid())
            ):
                raise ValueError("VM-HA lifecycle trust anchor has unsafe local metadata")
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(descriptor)
            identity = (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_uid,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if not content or len(content) != before.st_size or identity != after_identity:
                raise ValueError("VM-HA lifecycle trust anchor changed while being read")
            return content, identity
        finally:
            os.close(descriptor)

    @classmethod
    def read(cls, path: Path) -> VMHALifecycleSnapshot | None:
        observed = cls._read_file(path)
        if observed is None:
            return None
        content, identity = observed
        try:
            payload = json.loads(content)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("VM-HA lifecycle trust anchor is malformed") from error
        return cls(
            path=path,
            state=VMHALifecycleState.from_dict(payload),
            content_sha256=hashlib.sha256(content).hexdigest(),
            file_identity=identity,
        )

    def assert_current(self) -> None:
        observed = self._read_file(self.path)
        if observed is None:
            raise RuntimeError("VM-HA lifecycle trust anchor changed after preflight")
        content, identity = observed
        if identity != self.file_identity or not hmac.compare_digest(
            hashlib.sha256(content).hexdigest(), self.content_sha256
        ):
            raise RuntimeError("VM-HA lifecycle trust anchor changed after preflight")


class VMHALifecycleStore:
    """Strict atomic record store with revision and predecessor CAS."""

    def __init__(self, config_path: Path) -> None:
        self.path = config_path.with_name(f"{config_path.name}.vm-ha-lifecycle.json")
        self._lock_path = self.path.with_name(f".{self.path.name}.write.lock")

    def _read_unscoped(self) -> VMHALifecycleState | None:
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
        return VMHALifecycleState.from_dict(payload)

    def read_hardened(
        self,
        *,
        expected_project_id: str | None,
        expected_gateway_name: str,
    ) -> VMHALifecycleSnapshot | None:
        """Read a lifecycle trust anchor without following or racing local links."""

        snapshot = VMHALifecycleSnapshot.read(self.path)
        if snapshot is None:
            return None
        state = snapshot.state
        if not expected_project_id or state.project_id != expected_project_id:
            raise ValueError("VM-HA lifecycle project identity does not match the config")
        if state.gateway_name != expected_gateway_name:
            raise ValueError("VM-HA lifecycle gateway identity does not match the config")
        return snapshot

    def read(
        self,
        *,
        expected_project_id: str | None,
        expected_gateway_name: str,
    ) -> VMHALifecycleState | None:
        state = self._read_unscoped()
        if state is None:
            return None
        if not expected_project_id or state.project_id != expected_project_id:
            raise ValueError("VM-HA lifecycle project identity does not match the config")
        if state.gateway_name != expected_gateway_name:
            raise ValueError("VM-HA lifecycle gateway identity does not match the config")
        return state

    def write_verified(
        self,
        state: VMHALifecycleState,
        *,
        predecessor_sha256: str | None = None,
    ) -> None:
        if state.record_version != 4 or state.transaction is None:
            raise ValueError("VM-HA lifecycle writes require a v4 transaction")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        lock_descriptor = os.open(self._lock_path, flags, 0o600)
        try:
            os.fchmod(lock_descriptor, 0o600)
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            previous = self._read_unscoped()
            if previous is None:
                if predecessor_sha256 is not None or state.transaction.revision != 1:
                    raise ValueError("VM-HA lifecycle initial compare-and-swap failed")
            elif previous == state:
                return
            else:
                if predecessor_sha256 != previous.record_sha256:
                    raise ValueError("VM-HA lifecycle predecessor compare-and-swap failed")
                _validate_successor(previous, state)
            payload = _canonical_json(state.to_dict()) + b"\n"
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
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)


class VMHALifecycleJournal:
    """Store-backed before/after effect journal for one approved transaction."""

    def __init__(
        self,
        store: VMHALifecycleStore,
        state: VMHALifecycleState,
    ) -> None:
        if state.transaction is None or state.record_version != 4:
            raise ValueError("VM-HA lifecycle journal requires a v4 transaction")
        self.store = store
        self.state = state

    def effect_operation_id(self, effect: str) -> str:
        """Return one replay-stable UUID-shaped Nebius idempotency key."""

        transaction = self.state.transaction
        assert transaction is not None
        raw = hashlib.sha256(f"{transaction.operation_id}\0{effect}".encode()).digest()[:16]
        return str(uuid.UUID(bytes=raw, version=4))

    def authorize_failed_passive_replacement(
        self,
        *,
        passive_instance_name: str,
        approval_digest: str,
        retired_compute_id: str,
        retired_disk_id: str,
        current_observation: object,
        replacement_cycle: int = 1,
    ) -> None:
        """Persist replacement approval before its first destructive effect."""

        successor = self.state.authorize_failed_passive_replacement(
            passive_instance_name=passive_instance_name,
            approval_digest=approval_digest,
            retired_compute_id=retired_compute_id,
            retired_disk_id=retired_disk_id,
            current_observation=current_observation,
            replacement_cycle=replacement_cycle,
        )
        if successor == self.state:
            return
        self.store.write_verified(
            successor,
            predecessor_sha256=self.state.record_sha256,
        )
        self.state = successor

    def begin(
        self,
        effect: str,
        *,
        observation: object | None = None,
        permitted_paths: tuple[str, ...] = (),
    ) -> str:
        """Persist effect intent before the external request."""

        transaction = self.state.transaction
        assert transaction is not None
        if effect in transaction.completed_effects:
            return self.effect_operation_id(effect)
        if transaction.pending_effect == effect:
            self.state.begin_effect(
                effect,
                observation=observation,
                permitted_paths=permitted_paths,
            )
            return self.effect_operation_id(effect)
        successor = self.state.begin_effect(
            effect,
            observation=observation,
            permitted_paths=permitted_paths,
        )
        self.store.write_verified(
            successor,
            predecessor_sha256=self.state.record_sha256,
        )
        self.state = successor
        return self.effect_operation_id(effect)

    def rewind_host_activation_for_owner_adoption(self, adoption_effect: str) -> None:
        """Persist a safe host-only rewind before a newly required adoption."""

        successor = self.state.rewind_host_activation_for_owner_adoption(adoption_effect)
        if successor == self.state:
            return
        self.store.write_verified(
            successor,
            predecessor_sha256=self.state.record_sha256,
        )
        self.state = successor

    def rewind_standby_replacement_inhibition_for_owner_refresh(
        self,
        *,
        owner_refresh_effect: str,
        inhibition_effect: str,
    ) -> None:
        """Persist the exact host-only replacement resume rewind."""

        successor = self.state.rewind_standby_replacement_inhibition_for_owner_refresh(
            owner_refresh_effect=owner_refresh_effect,
            inhibition_effect=inhibition_effect,
        )
        if successor == self.state:
            return
        self.store.write_verified(
            successor,
            predecessor_sha256=self.state.record_sha256,
        )
        self.state = successor

    def complete(
        self,
        effect: str,
        *,
        resource_updates: Mapping[str, str] | None = None,
        observation: object | None = None,
    ) -> None:
        """Persist the independently observed effect postcondition."""

        transaction = self.state.transaction
        assert transaction is not None
        if effect in transaction.completed_effects:
            if resource_updates:
                current = dict(transaction.resource_bindings)
                if any(current.get(key) != value for key, value in resource_updates.items()):
                    raise ValueError("VM-HA completed effect resource identity changed")
            return
        successor = self.state.complete_effect(
            effect,
            resource_updates=resource_updates,
            observation=observation,
        )
        self.store.write_verified(
            successor,
            predecessor_sha256=self.state.record_sha256,
        )
        self.state = successor

    def record_cloud_operation(self, effect: str, cloud_operation_id: str) -> None:
        """Checkpoint one accepted cloud operation before its bounded wait."""

        successor = self.state.record_cloud_operation(effect, cloud_operation_id)
        if successor == self.state:
            return
        self.store.write_verified(
            successor,
            predecessor_sha256=self.state.record_sha256,
        )
        self.state = successor

    def transition(
        self,
        state: VMHALifecycleState,
    ) -> None:
        """Persist a caller-constructed state successor through the same CAS."""

        self.store.write_verified(state, predecessor_sha256=self.state.record_sha256)
        self.state = state


def lifecycle_member_map(
    state: VMHALifecycleState,
) -> Mapping[str, VMHALifecycleMember]:
    """Return the canonical exact-name view used by cloud classification."""

    return {member.instance_name: member for member in state.members}
