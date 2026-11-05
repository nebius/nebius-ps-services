"""Admission for unchanged route scope and explicitly approved pre-host recovery."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ..agent.vm_ha.progress import validate_transfer_progress
from ..checkpoint_upgrade import inspect_checkpoint
from ..schema import VMHARouteTarget, VMHARuntimeBinding
from .vm_ha_lifecycle import (
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHAMigrationTransaction,
    normalize_vm_ha_observation,
    vm_ha_effective_resource_bindings,
)

PREHOST_EFFECTS = frozenset(
    {
        "resolve-authoritative-route-targets",
        "provision-shared-allocation",
        "attach-shared-allocation-active",
        "construct-authoritative-runtime-binding",
    }
)
RECOVERY_CHECKPOINT = "prehost-recovery-approved"
AUDIT_PREFIX = "prehost-superseded-"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_retained_route_scope(
    state: VMHALifecycleState, observation: Mapping[str, object]
) -> None:
    """Refuse route identity drift before starting any managed reapply effects."""

    if not state.route_runtime_id or state.status in {
        VMHALifecycleStatus.REMOVED,
        VMHALifecycleStatus.DESTROYED,
    }:
        return
    raw = observation.get("route_targets")
    if not isinstance(raw, list):
        raise ValueError("VM-HA authoritative route targets are unavailable")
    targets = tuple(VMHARouteTarget.model_validate(item) for item in raw)
    if (
        _serialized_targets(targets) != state.route_targets
        or VMHARuntimeBinding.derive_route_runtime_id(
            state.cluster_id, state.allocation_id, targets
        )
        != state.route_runtime_id
    ):
        raise ValueError(
            "VM-HA managed route scope cannot change during apply; "
            "restore the local prefixes selecting the retained route targets"
        )


def validate_prehost_checkpoint(state: VMHALifecycleState) -> None:
    tx = state.transaction
    if (
        state.record_version != 4
        or state.status is not VMHALifecycleStatus.PROVISIONING
        or tx is None
        or not state.allocation_id
        or not state.route_runtime_id
        or not state.route_targets
        or tx.pending_effect is not None
        or tx.accepted_cloud_operation_id is not None
        or tx.observation_guard is not None
        or tx.checkpoint != "after-construct-authoritative-runtime-binding"
        or set(tx.completed_effects) != PREHOST_EFFECTS
        or not tx.observation
        or any(
            not all(
                (
                    member.compute_id,
                    member.compute_revision,
                    member.disk_id,
                    member.network_interface_name,
                    member.network_interface_subnet_id,
                    member.primary_allocation_id,
                    member.public_allocation_id,
                    member.public_ip,
                )
            )
            for member in state.members
        )
    ):
        raise ValueError("No complete pre-host VM-HA reapply is eligible for recovery")


def _rows(leaves: Mapping[str, str], prefix: str) -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for path, value in leaves.items():
        if path.startswith(prefix):
            index, separator, key = path[len(prefix) :].partition("/")
            if not separator or not index.isdecimal():
                raise ValueError("VM-HA recovery cloud observation is malformed")
            rows.setdefault(index, {})[key] = value
    return [rows[key] for key in sorted(rows, key=int)]


def validate_prehost_cloud(
    state: VMHALifecycleState,
    observation: Mapping[str, object],
    bindings: Mapping[str, str],
) -> None:
    """Prove allocation/member reuse and unchanged retained routes, not effect names."""

    validate_prehost_checkpoint(state)
    validate_retained_route_scope(state, observation)
    assert state.transaction is not None
    recorded_observation = _cloud_observation(state.transaction.observation)
    if (
        _credential_observation_digest(state, recorded_observation)
        != state.transaction.current_state_digest
    ):
        raise ValueError("VM-HA pre-host recovery cannot prove cloud effects were reuse only")
    before = dict(state.transaction.observation)
    after = dict(normalize_vm_ha_observation(observation))
    prefixes = ("/members/", "/shared_allocation/", "/project_id")
    if {k: v for k, v in before.items() if k.startswith(prefixes)} != {
        k: v for k, v in after.items() if k.startswith(prefixes)
    }:
        raise ValueError("VM-HA pre-host recovery cloud identity or revision changed")
    retained_tables = {
        json.dumps(json.loads(target)["route_table_id"]) for target in state.route_targets
    }
    old_routes = [
        row for row in _rows(before, "/routes/") if row.get("route_table_id") in retained_tables
    ]
    new_routes = _rows(after, "/routes/")
    if sorted(old_routes, key=_digest) != sorted(new_routes, key=_digest):
        raise ValueError("VM-HA pre-host recovery retained routes changed")
    old = vm_ha_effective_resource_bindings(dict(state.transaction.resource_bindings))
    current = vm_ha_effective_resource_bindings(dict(bindings))
    immutable = {
        key: value
        for key, value in old.items()
        if key.startswith(
            (
                "compute:",
                "disk:",
                "primary-allocation:",
                "public-allocation:",
                "credential-",
                "shared-allocation-",
            )
        )
    }
    if not immutable or any(current.get(key) != value for key, value in immutable.items()):
        raise ValueError("VM-HA pre-host recovery resource or credential identity changed")
    if current.get("shared-allocation-id") != state.allocation_id:
        raise ValueError("VM-HA pre-host recovery retained allocation changed")
    if current.get("route-runtime-id") != state.route_runtime_id:
        raise ValueError("VM-HA pre-host recovery retained runtime identity changed")
    if current.get("route-targets-digest") != _digest(observation["route_targets"]):
        raise ValueError("VM-HA pre-host recovery route target binding changed")
    rows = observation.get("members")
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError("VM-HA pre-host recovery requires both retained members")
    by_name = {row.get("instance_name"): row for row in rows if isinstance(row, dict)}
    for member in state.members:
        row = by_name.get(member.instance_name, {})
        expected = {
            "compute_id": member.compute_id,
            "boot_disk_id": member.disk_id,
            "network_interface_name": member.network_interface_name,
            "subnet_id": member.network_interface_subnet_id,
            "primary_allocation_id": member.primary_allocation_id,
            "public_allocation_id": member.public_allocation_id,
            "public_ip": member.public_ip,
            "present": True,
            "state": "running",
        }
        if any(row.get(key) != value for key, value in expected.items()):
            raise ValueError("VM-HA pre-host recovery retained member identity changed")


def validate_prehost_hosts(state: VMHALifecycleState, hosts: Mapping[str, object]) -> None:
    """Inspect installed state independently of target-generation capabilities."""

    if set(hosts) != {member.instance_name for member in state.members}:
        raise ValueError("VM-HA pre-host recovery requires both pinned hosts")
    generations: set[str] = set()
    assert state.transaction is not None
    resources = vm_ha_effective_resource_bindings(dict(state.transaction.resource_bindings))
    for member in state.members:
        raw = hosts[member.instance_name]
        if (
            not isinstance(raw, dict)
            or raw.get("pending_files") != []
            or raw.get("writer_admission") is not True
        ):
            raise ValueError("VM-HA pre-host recovery host has outstanding work")
        binding = VMHARuntimeBinding.model_validate(raw.get("runtime_binding"))
        expected_credential = resources.get(f"credential-sha256:{member.node_id}")
        if (
            not expected_credential
            or raw.get("credential_sha256") != expected_credential
            or binding.nebius_project_id != state.project_id
            or not binding.nebius_service_account_id
            or binding.nebius_service_account_id
            != resources.get(f"credential-service-account:{member.node_id}")
            or not binding.nebius_authorized_key_id
            or binding.nebius_authorized_key_id
            != resources.get(f"credential-authorized-key:{member.node_id}")
        ):
            raise ValueError("VM-HA pre-host recovery installed credentials changed")
        if (
            binding.cluster_id != state.cluster_id
            or binding.shared_allocation_id != state.allocation_id
            or binding.route_runtime_id != state.route_runtime_id
            or _serialized_targets(binding.route_targets) != state.route_targets
            or raw.get("node_id") != member.node_id
            or raw.get("cluster_id") != state.cluster_id
            or raw.get("node")
            != {
                "node_id": member.node_id,
                "instance_index": member.instance_index,
                "role": member.role,
            }
        ):
            raise ValueError("VM-HA pre-host recovery installed identity changed")
        for node in binding.nodes:
            retained = next((item for item in state.members if item.node_id == node.node_id), None)
            if retained is None or (
                node.compute_id != retained.compute_id
                or node.network_interface_name != retained.network_interface_name
                or node.role.value != retained.role
            ):
                raise ValueError("VM-HA pre-host recovery installed member changed")
        generation = raw.get("generation")
        if (
            not isinstance(generation, dict)
            or generation.get("generation_id") != binding.generation_id
        ):
            raise ValueError("VM-HA pre-host recovery installed generation is inconsistent")
        expected_digests = {
            "configuration": binding.configuration_digest,
            "static_routes": binding.static_routes_digest,
            "bgp_policy": binding.bgp_policy_digest,
        }
        if generation.get("digests") != expected_digests:
            raise ValueError("VM-HA pre-host recovery installed digests are inconsistent")
        readiness = raw.get("readiness")
        if (
            not isinstance(readiness, dict)
            or readiness.get("generation_id") != binding.generation_id
            or readiness.get("digests") != expected_digests
            or not isinstance(readiness.get("required_node_ids"), list)
            or sorted(readiness["required_node_ids"])
            != sorted(node.node_id for node in binding.nodes)
        ):
            raise ValueError("VM-HA pre-host recovery installed readiness is inconsistent")
        generations.add(_digest({"id": binding.generation_id, "digests": expected_digests}))
        checkpoint = inspect_checkpoint(raw.get("checkpoint"))
        if checkpoint.pending_action is not None or checkpoint.repair_attempt is not None:
            raise ValueError("VM-HA pre-host recovery controller is not quiescent")
        progress = raw.get("transfer")
        if progress is not None:
            latest = validate_transfer_progress(progress)["history"][-1]
            if latest["state"] != "completed" or latest["action"] != "enable-active":
                raise ValueError("VM-HA pre-host recovery transfer is not complete")
        rearm = raw.get("rearm")
        if rearm is not None and (
            not isinstance(rearm, dict)
            or rearm.get("schema") != "nebius-vpngw/vm-ha-rearm-checkpoint-v1"
            or rearm.get("phase") != "running"
            or not isinstance(rearm.get("completed_at"), (int, float))
            or rearm.get("failure_reason") is not None
        ):
            raise ValueError("VM-HA pre-host recovery rearm is not complete")
        for field in ("boot_id", "config_sha256", "package_version"):
            if not isinstance(raw.get(field), str) or not raw[field]:
                raise ValueError("VM-HA pre-host recovery host observation is incomplete")
        if not isinstance(raw.get("staged_configs"), dict):
            raise ValueError("VM-HA pre-host recovery staged configuration observation is missing")
    if len(generations) != 1:
        raise ValueError("VM-HA pre-host recovery installed generations disagree")


def recover_prehost(
    previous: VMHALifecycleState,
    *,
    operation_id: str,
    approval_digest: str,
    desired_state_digest: str,
    bindings: Mapping[str, str],
    observation: Mapping[str, object],
    host_evidence: Mapping[str, object],
) -> VMHALifecycleState:
    validate_prehost_cloud(previous, observation, bindings)
    validate_prehost_hosts(previous, host_evidence)
    assert previous.transaction is not None
    audit = dict(bindings)
    audit.update(
        {
            AUDIT_PREFIX + "record": previous.record_sha256,
            AUDIT_PREFIX + "operation": previous.transaction.operation_id,
            AUDIT_PREFIX + "desired": previous.transaction.desired_state_digest,
            AUDIT_PREFIX + "hosts": _digest(host_evidence),
            AUDIT_PREFIX + "host-evidence": json.dumps(
                host_evidence, sort_keys=True, separators=(",", ":")
            ),
        }
    )
    return replace(
        previous,
        transaction=VMHAMigrationTransaction(
            operation_id=operation_id,
            approval_kind="recovery",
            approval_digest=approval_digest,
            desired_state_digest=desired_state_digest,
            current_state_digest=_credential_observation_digest(previous, observation),
            checkpoint=RECOVERY_CHECKPOINT,
            pending_effect=None,
            completed_effects=(),
            resource_bindings=tuple(sorted(audit.items())),
            revision=1,
            predecessor_sha256=previous.record_sha256,
            observation=normalize_vm_ha_observation(observation),
        ),
    )


def validate_prehost_successor(previous: VMHALifecycleState, successor: VMHALifecycleState) -> None:
    """Store-level guard; constructing a dataclass cannot bypass recovery admission."""

    validate_prehost_checkpoint(previous)
    before, after = previous.transaction, successor.transaction
    assert before is not None
    if (
        after is None
        or successor.record_version != 4
        or successor.status is not VMHALifecycleStatus.PROVISIONING
        or not previous.has_same_identity(successor)
        or previous.members != successor.members
        or after.operation_id == before.operation_id
        or after.approval_kind != "recovery"
        or after.checkpoint != RECOVERY_CHECKPOINT
        or after.revision != 1
        or after.predecessor_sha256 != previous.record_sha256
        or after.pending_effect is not None
        or after.completed_effects
        or after.observation_guard is not None
        or after.accepted_cloud_operation_id is not None
    ):
        raise ValueError("VM-HA pre-host recovery successor is invalid")
    bindings = dict(after.resource_bindings)
    for key, value in (
        ("record", previous.record_sha256),
        ("operation", before.operation_id),
        ("desired", before.desired_state_digest),
    ):
        if bindings.get(AUDIT_PREFIX + key) != value:
            raise ValueError("VM-HA pre-host recovery audit lineage is invalid")
    observation = _cloud_observation(after.observation)
    validate_prehost_cloud(previous, observation, bindings)
    if after.current_state_digest != _credential_observation_digest(successor, observation):
        raise ValueError("VM-HA pre-host recovery initial observation digest changed")
    try:
        hosts = json.loads(bindings[AUDIT_PREFIX + "host-evidence"])
    except (KeyError, ValueError) as error:
        raise ValueError("VM-HA pre-host recovery host evidence is missing") from error
    validate_prehost_hosts(previous, hosts)
    if bindings.get(AUDIT_PREFIX + "hosts") != _digest(hosts):
        raise ValueError("VM-HA pre-host recovery host evidence is missing")


def _credential_observation_digest(
    state: VMHALifecycleState, observation: Mapping[str, object]
) -> str:
    assert state.transaction is not None
    resources = vm_ha_effective_resource_bindings(dict(state.transaction.resource_bindings))
    try:
        credentials = [
            {
                "authorized_key_id": resources[f"credential-authorized-key:{member.node_id}"],
                "credential_sha256": resources[f"credential-sha256:{member.node_id}"],
                "node_id": member.node_id,
                "project_id": state.project_id,
                "service_account_id": resources[f"credential-service-account:{member.node_id}"],
            }
            for member in sorted(state.members, key=lambda member: member.node_id)
        ]
    except KeyError as error:
        raise ValueError("VM-HA pre-host recovery credential binding is incomplete") from error
    return _digest(dict(observation, runtime_credentials=credentials))


def _cloud_observation(leaves: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    observation: dict[str, Any] = {}
    for key in ("members", "routes", "route_targets"):
        observation[key] = [_unflatten_row(row) for row in _rows(dict(leaves), f"/{key}/")]
    observation["shared_allocation"] = _unflatten_row(
        {
            p[len("/shared_allocation/") :]: v
            for p, v in leaves
            if p.startswith("/shared_allocation/")
        }
    )
    observation["project_id"] = json.loads(dict(leaves).get("/project_id", "null"))
    if normalize_vm_ha_observation(observation) != leaves:
        raise ValueError("VM-HA pre-host recovery cloud observation is incomplete")
    return observation


def _unflatten_row(row: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for path, value in row.items():
        parts = [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")]
        current = result
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = json.loads(value)
    # The only list nested in these cloud rows is the NIC allocation alias list.
    if isinstance(result.get("aliases"), dict):
        result["aliases"] = [
            v for _k, v in sorted(result["aliases"].items(), key=lambda x: int(x[0]))
        ]
    return result


def _serialized_targets(targets: tuple[VMHARouteTarget, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            json.dumps(target.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            for target in targets
        )
    )
