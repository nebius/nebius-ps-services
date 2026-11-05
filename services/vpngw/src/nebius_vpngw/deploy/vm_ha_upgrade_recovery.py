"""Digest-bound host recovery of an interrupted in-place two-member upgrade."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any

from .vm_ha_host_recovery import REPLACEMENT_RECOVERY_INSPECTION
from .vm_ha_lifecycle import VMHALifecycleState, VMHALifecycleStatus

AUTHORITY = "upgrade-artifact-recovery-authority"
READY = "upgrade-artifact-recovery-packages-ready"
ARTIFACT = "heartbeat-upgrade-artifact"
UNITS = (
    "nebius-vpngw-vm-ha.service",
    "nebius-vpngw-vm-ha-rearm.service",
    "nebius-vpngw-agent.service",
    "frr.service",
    "strongswan-starter.service",
    "strongswan.service",
    "nebius-vpngw-fix-routes.timer",
    "nebius-vpngw-fix-routes.service",
    "nebius-vpngw-health-monitor.service",
)

# Reuse the bounded, no-secret installed-manifest inspector. The upgrade has its
# own lock identity and must also observe maintenance triggers, including timers.
UPGRADE_RECOVERY_INSPECTION = (
    REPLACEMENT_RECOVERY_INSPECTION.replace(
        "from nebius_vpngw.cli import _vm_ha_mtls_operation_id",
        "from nebius_vpngw.cli import _vm_ha_mtls_operation_id, _vm_ha_apply_operation_id",
    )
    .replace(
        "'mtls_operation_id': _vm_ha_mtls_operation_id(binding),",
        "'mtls_operation_id': _vm_ha_mtls_operation_id(binding), "
        "'apply_operation_id': _vm_ha_apply_operation_id(binding),",
    )
    .replace(
        "'strongswan-starter.service', 'strongswan.service'):",
        "'strongswan-starter.service', 'strongswan.service', "
        "'nebius-vpngw-fix-routes.timer', 'nebius-vpngw-fix-routes.service', "
        "'nebius-vpngw-health-monitor.service'):",
    )
    .replace(
        "if checkpoint.pending_action is not None:\n        raise ValueError('pending controller action')",
        "# Keep local passive intent for canonical controller replay.",
    )
    .replace(
        "'installed': project(installed) if installed is not None else None,",
        "'controller': record('controller-checkpoint.json'),\n"
        "        'released_inhibition': record('standby-replacement.release.json'),\n"
        "        'installed': project(installed) if installed is not None else None,",
    )
    .replace(
        "'accepted-cloud-operation.json', 'standby-replacement.release.json',",
        "'accepted-cloud-operation.json',",
    )
)


def recovery_authority(state: VMHALifecycleState) -> dict[str, str] | None:
    tx = state.transaction
    raw = None if tx is None else dict(tx.resource_bindings).get(AUTHORITY)
    if raw is None:
        return None
    value = json.loads(raw)
    keys = {
        "approval",
        "prior_record",
        "prior_artifact",
        "artifact",
        "ssh_trust",
        "hosts",
        "owner",
        "superseded_effect",
        "packages",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("upgrade recovery authority is invalid")
    if any(
        not isinstance(value[key], str) or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
        for key in keys - {"owner", "superseded_effect", "packages"}
    ) or not isinstance(value["superseded_effect"], str):
        raise ValueError("upgrade recovery authority digest is invalid")
    if value["owner"] not in {m.node_id for m in state.members}:
        raise ValueError("upgrade recovery owner is invalid")
    packages = json.loads(value["packages"])
    if not isinstance(packages, dict) or set(packages) != {m.instance_name for m in state.members}:
        raise ValueError("upgrade recovery package member set is invalid")
    for package in packages.values():
        if not (
            isinstance(package, dict)
            and set(package) == {"closure", "wheels"}
            and isinstance(package["closure"], str)
            and re.fullmatch(r"[0-9a-f]{64}", package["closure"])
            and isinstance(package["wheels"], dict)
            and all(
                isinstance(name, str)
                and re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name)
                and isinstance(sha, str)
                and re.fullmatch(r"[0-9a-f]{64}", sha)
                for name, sha in package["wheels"].items()
            )
        ):
            raise ValueError("upgrade recovery package authority is invalid")
    return value


def package_authority(artifact: Any) -> str:
    """Pin desired dependency closure and exact downloads, without predecessor state."""
    packages = {}
    for name, plan in artifact.dependency_plans:
        closure = {
            key: plan.manifest[key] for key in ("expected_dependencies", "expected_requirements")
        }
        packages[name] = {
            "closure": hashlib.sha256(
                json.dumps(closure, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "wheels": plan.manifest["dependency_wheels"],
        }
    return json.dumps(packages, sort_keys=True, separators=(",", ":"))


def validate_packages(state: VMHALifecycleState, artifact: Any) -> None:
    record = recovery_authority(state)
    if record is None:
        return
    expected, current = json.loads(record["packages"]), json.loads(package_authority(artifact))
    if set(current) != set(expected) or any(
        item["closure"] != expected[name]["closure"]
        or any(expected[name]["wheels"].get(wheel) != sha for wheel, sha in item["wheels"].items())
        for name, item in current.items()
    ):
        raise ValueError("upgrade recovery approved dependency closure or wheel changed")


def install_effect(node_id: str) -> str:
    return "upgrade-artifact-recovery-install-" + node_id


def release_started(state: VMHALifecycleState) -> bool:
    tx = state.transaction
    if tx is None:
        return False
    effects = (*tx.completed_effects, *((tx.pending_effect,) if tx.pending_effect else ()))
    return any(
        effect in {"verify-active-forwarding-and-routes", "verify-passive-unlocked-non-forwarding"}
        or effect.startswith(("release-", "unlock-"))
        for effect in effects
    )


def validate_checkpoint(state: VMHALifecycleState) -> None:
    tx = state.transaction
    if (
        state.record_version != 4
        or state.status is not VMHALifecycleStatus.ACTIVATING
        or tx is None
        or tx.accepted_cloud_operation_id is not None
        or tx.accepted_cloud_operation_effect is not None
        or tx.observation_guard is not None
        or tx.replacement_host_recovery is not None
        or len(state.members) != 2
        or not state.allocation_id
    ):
        raise ValueError("upgrade recovery requires an exact host-only checkpoint")
    bindings = dict(tx.resource_bindings)
    if (
        re.fullmatch(r"[0-9a-f]{64}", bindings.get(ARTIFACT, "")) is None
        or "heartbeat-initial-artifact" in bindings
        or any(key.startswith(("standby-replacement-", "passive-replacement-")) for key in bindings)
        or not all(member.compute_id and member.disk_id for member in state.members)
    ):
        raise ValueError("upgrade recovery requires an existing retained pair")
    allowed = {
        f"{action}-{member.node_id}"
        for member in state.members
        for action in (
            "stage",
            "install-apply-lock",
            "stop-upgrade-writers",
            "prepare-agent-package",
            "install-owner-adoption",
            "activate",
        )
    } | {"prepare-heartbeat-upgrade"}
    authority = recovery_authority(state)
    if authority is not None:
        allowed |= {install_effect(m.node_id) for m in state.members}
    if tx.pending_effect is not None and tx.pending_effect not in allowed:
        raise ValueError("upgrade recovery host frontier is unsafe")
    if release_started(state):
        raise ValueError("upgrade recovery cannot rewind a release frontier")
    if authority is not None:
        expected = authority["artifact"] if READY in bindings else authority["prior_artifact"]
        if bindings[ARTIFACT] != expected or (
            READY in bindings and bindings[READY] != authority["artifact"]
        ):
            raise ValueError("upgrade recovery artifact binding changed")


def host_digest(evidence: dict[str, Any]) -> str:
    # Controller checkpoints are progress, never allocation authority. Their
    # pending intent is independently validated below, including after restart.
    stable = {
        node: {
            key: item[key]
            for key in (
                "installed",
                "staged",
                "mtls",
                "policy",
                "apply_lock",
                "released_inhibition",
            )
        }
        for node, item in evidence.items()
    }
    for item in stable.values():
        item["mtls"] = {
            key: value
            for key, value in item["mtls"].items()
            if key
            not in {
                "state",
                "operation_id",
                "operation_kind",
                "phase",
                "inhibited",
                "inhibition_operation_id",
            }
        }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_evidence(
    state: VMHALifecycleState,
    evidence: dict[str, Any],
    generations: dict[str, Any],
    *,
    require_stopped: bool = False,
) -> None:
    from ..agent.vm_ha.auto_healing import AutoHealingPolicyPhase, AutoHealingPolicyRecord

    validate_checkpoint(state)
    assert state.transaction is not None
    nodes = {
        m.node_id: {"compute_id": m.compute_id, "nic": m.network_interface_name}
        for m in state.members
    }
    if set(evidence) != set(nodes) or set(generations) != set(nodes):
        raise ValueError("upgrade recovery host evidence is incomplete")
    operations, policies = set(), []
    for member in state.members:
        item = evidence[member.node_id]
        generation, digests = generations[member.node_id]
        controller = item["controller"]
        if (
            not isinstance(controller, dict)
            or controller.get("transfer_continuity") is not None
            or controller.get("repair_attempt") is not None
        ):
            raise ValueError("upgrade recovery has competing controller continuity")
        action = controller.get("pending_action") if isinstance(controller, dict) else None
        if action is not None and not (
            isinstance(action, dict)
            and action.get("kind") == "enter-passive"
            and action.get("boot_id") == item["boot_id"]
            and action.get("target_node_id") == member.node_id
            and action.get("allocation_id") == state.allocation_id
            and action.get("generation_id") == generation
            and action.get("digests") == digests
            and type(controller.get("sequence")) is int
            and controller["sequence"] > 0
            and action.get("ownership_incarnation") == controller.get("ownership_incarnation")
            and action.get("operation_id")
            == f"{item['boot_id']}:{controller['sequence']}:enter-passive:{member.node_id}"
            and controller.get("transfer_continuity") is None
            and controller.get("repair_attempt") is None
        ):
            raise ValueError("upgrade recovery has a competing controller operation")
        for projection in (item["installed"], item["staged"]):
            if not isinstance(projection, dict) or not (
                projection["cluster_id"] == state.cluster_id
                and projection["node_id"] == member.node_id
                and projection["generation_id"] == generation
                and projection["digests"] == digests
                and projection["allocation_id"] == state.allocation_id
                and projection["nodes"] == nodes
                and re.fullmatch(r"[0-9a-f]{64}", projection["apply_operation_id"])
            ):
                raise ValueError("upgrade recovery installed identity or generation drifted")
            operations.add(projection["apply_operation_id"])
        if item["pending"] or item["inhibition"] is not None:
            raise ValueError("upgrade recovery conflicts with pending host work")
        released = item["released_inhibition"]
        if released is not None and not (
            isinstance(released, dict)
            and set(released)
            == {"schema", "cluster_id", "node_id", "generation_id", "operation_id"}
            and released["schema"] == "nebius-vpngw/vm-ha-standby-replacement-release-v1"
            and released["cluster_id"] == state.cluster_id
            and released["node_id"] == member.node_id
            and released["generation_id"] == generation
            and isinstance(released["operation_id"], str)
            and re.fullmatch(r"[0-9a-f]{64}", released["operation_id"])
            and released["operation_id"]
            not in {state.transaction.operation_id, item["staged"]["apply_operation_id"]}
        ):
            raise ValueError("upgrade recovery retained release receipt is foreign or current")
        if item["apply_lock"] != {
            "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
            "apply_locked": True,
            "cluster_id": state.cluster_id,
            "node_id": member.node_id,
            "generation_id": generation,
            "operation_id": item["staged"]["apply_operation_id"],
        }:
            raise ValueError("upgrade recovery exact apply lock is absent")
        mtls = item["mtls"]
        terminal_mtls = (
            mtls.get("state") == "healthy"
            and mtls.get("operation_id") is None
            and mtls.get("operation_kind") is None
            and mtls.get("phase") is None
        )
        applying_mtls = (
            mtls.get("state") == "transitioning"
            and mtls.get("operation_id") == item["staged"]["mtls_operation_id"]
            and mtls.get("operation_kind") in {"bootstrap", "replacement", "recovery"}
            and mtls.get("phase") in {"local-active", "verified", "committed"}
        )
        if not (
            mtls["cluster_id"] == state.cluster_id
            and mtls["node_id"] == member.node_id
            and mtls["compute_id"] == member.compute_id
            and (terminal_mtls or applying_mtls)
            and type(mtls.get("epoch")) is int
            and mtls["epoch"] >= 1
            and all(
                isinstance(mtls.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", mtls[key])
                for key in ("certificate_fingerprint", "spki_fingerprint")
            )
            and mtls["inhibited"] is False
            and mtls["inhibition_operation_id"] is None
        ):
            raise ValueError("upgrade recovery mTLS authority is not terminal")
        policy = AutoHealingPolicyRecord.from_mapping(item["policy"])
        if not (
            policy.cluster_id == state.cluster_id
            and policy.node_id == member.node_id
            and policy.peer_node_id == next(node for node in nodes if node != member.node_id)
            and policy.generation_id == generation
            and policy.phase is AutoHealingPolicyPhase.COMMITTED
        ):
            raise ValueError("upgrade recovery policy authority is not terminal")
        policies.append(policy)
        if require_stopped:
            guard = item["guard"]
            if not (
                item["forwarding"] == "0"
                and isinstance(guard, dict)
                and guard.get("data_plane_mode") == "blocked"
                and guard.get("guard_boot_id") == item["boot_id"]
                and item["boot_id"]
            ):
                raise ValueError("upgrade recovery current-boot fence is absent")
            if set(item["units"]) != set(UNITS):
                raise ValueError("upgrade recovery service evidence is incomplete")
            for name, unit in item["units"].items():
                if unit["LoadState"] == "not-found":
                    if name in UNITS[:2]:
                        raise ValueError("upgrade recovery required controller is absent")
                    continue
                if not (
                    unit["ActiveState"] in {"inactive", "failed"}
                    and unit["Job"] == ""
                    and (
                        name.endswith(".timer")
                        or (unit["MainPID"] == "0" and unit["ControlPID"] == "0")
                    )
                ):
                    raise ValueError("upgrade recovery writer or maintenance job is live")
    for node, item in evidence.items():
        peer = next(value for key, value in evidence.items() if key != node)
        if item["mtls"].get("peer_fingerprints") != [peer["mtls"]["certificate_fingerprint"]]:
            raise ValueError("upgrade recovery mTLS peers are not cross-pinned")
    if len(operations) != 1 or len({(p.decision_digest, p.desired) for p in policies}) != 1:
        raise ValueError("upgrade recovery peer authority disagrees")
    authority = recovery_authority(state)
    if authority is not None and authority["hosts"] != host_digest(evidence):
        raise ValueError("upgrade recovery approved host authority changed")


def authorize(
    state: VMHALifecycleState,
    *,
    approval: str,
    artifact: str,
    ssh_trust: str,
    hosts: str,
    owner: str,
    packages: str,
) -> VMHALifecycleState:
    validate_checkpoint(state)
    tx = state.transaction
    assert tx is not None
    bindings = dict(tx.resource_bindings)
    existing = recovery_authority(state)
    requested = dict(
        approval=approval,
        artifact=artifact,
        ssh_trust=ssh_trust,
        hosts=hosts,
        owner=owner,
        packages=packages,
    )
    if existing is not None:
        if any(existing[key] != value for key, value in requested.items()):
            raise ValueError("upgrade recovery approval changed")
        return state
    if artifact == bindings[ARTIFACT]:
        raise ValueError("same-artifact upgrade must use ordinary resume")
    record = dict(
        requested,
        prior_record=state.record_sha256,
        prior_artifact=bindings[ARTIFACT],
        superseded_effect=tx.pending_effect or "",
    )
    result = replace(
        state,
        transaction=tx.advance(
            predecessor_sha256=state.record_sha256,
            checkpoint="upgrade-artifact-recovery-authorized",
            pending_effect=None,
            resource_updates={AUTHORITY: json.dumps(record, sort_keys=True, separators=(",", ":"))},
        ),
    )
    recovery_authority(result)
    return result


def publish_packages(state: VMHALifecycleState) -> VMHALifecycleState:
    validate_checkpoint(state)
    tx = state.transaction
    assert tx is not None
    record = recovery_authority(state)
    if (
        record is None
        or tx.pending_effect is not None
        or not {install_effect(m.node_id) for m in state.members}.issubset(tx.completed_effects)
    ):
        raise ValueError("upgrade recovery package proof is incomplete")
    bindings = dict(tx.resource_bindings)
    if READY in bindings:
        return state
    bindings.update({ARTIFACT: record["artifact"], READY: record["artifact"]})
    return replace(
        state,
        transaction=replace(
            tx.advance(
                predecessor_sha256=state.record_sha256,
                checkpoint="upgrade-artifact-recovery-packages-ready",
            ),
            resource_bindings=tuple(sorted(bindings.items())),
        ),
    )


def validate_successor(previous: VMHALifecycleState, successor: VMHALifecycleState) -> bool:
    """Validate special authority transitions; restrict effects until both packages verify."""
    before, after = previous.transaction, successor.transaction
    assert before is not None and after is not None
    old, new = dict(before.resource_bindings), dict(after.resource_bindings)
    if (old.get(AUTHORITY), old.get(READY)) != (new.get(AUTHORITY), new.get(READY)):
        if AUTHORITY not in old:
            record = recovery_authority(successor)
            if record is None:
                raise ValueError("upgrade recovery authority is absent")
            expected = authorize(
                previous,
                **{
                    key: record[key]
                    for key in ("approval", "artifact", "ssh_trust", "hosts", "owner", "packages")
                },
            )
        else:
            expected = publish_packages(previous)
        if successor != expected:
            raise ValueError("upgrade recovery successor changed unrelated authority")
        return True
    if AUTHORITY in old and READY not in old:
        record = recovery_authority(previous)
        assert record is not None
        allowed = {install_effect(m.node_id) for m in previous.members}
        added = set(after.completed_effects) - set(before.completed_effects)
        affected = added | ({after.pending_effect} if after.pending_effect else set())
        if install_effect(record["owner"]) in affected and not (
            allowed - {install_effect(record["owner"])}
        ).issubset(before.completed_effects):
            raise ValueError("upgrade recovery must install non-owner first")
        if not (
            successor.status is VMHALifecycleStatus.ACTIVATING
            and previous.has_same_identity(successor)
            and old == new
            and after.observation == before.observation
            and after.observation_guard is None
            and after.accepted_cloud_operation_id is None
            and affected.issubset(allowed)
        ):
            raise ValueError("only approved package effects may advance upgrade recovery")
    return False
