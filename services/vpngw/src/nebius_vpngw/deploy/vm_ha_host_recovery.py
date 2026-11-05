"""Strict host-only recovery of an already provisioned standby replacement."""

from __future__ import annotations

import ast
import hashlib
import json
import zipfile
from dataclasses import replace

from ..agent.vm_ha.auto_healing import (
    REBOUND_POLICY_CAPABILITY,
    AutoHealingPolicyPhase,
    AutoHealingPolicyRecord,
    StandbyAutoHealing,
)
from .vm_ha_lifecycle import (
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHAReplacementHostRecovery,
    vm_ha_missing_standby_disk_name_binding_key,
    vm_ha_missing_standby_owner_binding_key,
    vm_ha_missing_standby_owner_sequences,
    vm_ha_missing_standby_replacement_effect,
    vm_ha_passive_replacement_binding_key,
    vm_ha_passive_replacement_cycle_for_approval,
)


def require_host_recovery_artifact(artifact: object) -> None:
    """Require the selected, RECORD-verified wheel to advertise the repaired protocol."""

    from .ssh_push import VMHAAgentArtifact, VMHAAgentArtifactError, VMHAAgentArtifactProblem

    if not isinstance(artifact, VMHAAgentArtifact):
        raise ValueError("replacement recovery requires an exact wheel")
    with artifact.open_verified() as stream, zipfile.ZipFile(stream) as wheel:
        name = "nebius_vpngw/agent/vm_ha/auto_healing.py"
        payload = artifact._read_wheel_member(wheel, name, limit=1024 * 1024)
        import csv
        import io

        record_names = [name for name in wheel.namelist() if name.endswith(".dist-info/RECORD")]
        if len(record_names) != 1:
            raise ValueError("replacement recovery wheel RECORD is ambiguous")
        records = {
            row[0]: (row[1], row[2])
            for row in csv.reader(io.StringIO(wheel.read(record_names[0]).decode()))
        }
        artifact._validate_recorded_member(records, name, payload)
        try:
            tree = ast.parse(payload)
        except (SyntaxError, UnicodeError) as error:
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.INCOMPATIBLE,
                "replacement recovery policy source is invalid",
            ) from error
        declared = any(
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "REBOUND_POLICY_CAPABILITY"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and node.value.value == REBOUND_POLICY_CAPABILITY
            for node in tree.body
        )
        if not declared:
            raise VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.INCOMPATIBLE,
                "replacement recovery artifact lacks rebound policy support",
            )


def host_recovery_artifact_sources(artifact: object) -> dict[str, str]:
    from .ssh_push import VMHAAgentArtifact

    if not isinstance(artifact, VMHAAgentArtifact):
        raise ValueError("replacement recovery requires an exact wheel")
    with artifact.open_verified() as stream, zipfile.ZipFile(stream) as wheel:
        return {
            name.removeprefix("nebius_vpngw/"): hashlib.sha256(wheel.read(name)).hexdigest()
            for name in wheel.namelist()
            if name.startswith("nebius_vpngw/") and name.endswith(".py")
        }


def host_recovery_evidence_digest(evidence: dict) -> str:
    """Pin manifests, credential digests and mTLS authority across client restarts."""

    value = {
        node: {key: item[key] for key in ("installed", "staged", "mtls")}
        for node, item in evidence.items()
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# Never return the resolved YAML, keys, credentials or endpoint configuration.
# The shared lock prevents a concurrent writer during this admission snapshot.
REPLACEMENT_RECOVERY_INSPECTION = r"""
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import time
from pathlib import Path
import yaml
import nebius_vpngw
from nebius_vpngw.agent.vm_ha.auto_healing import AutoHealingPolicyRecord
from nebius_vpngw.agent.vm_ha.runtime import validate_installed_credential_bundle
from nebius_vpngw.schema import VMHARuntimeBinding
from nebius_vpngw.cli import _vm_ha_mtls_operation_id

root = Path('/var/lib/nebius-vpngw/vm-ha')
def read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1048576:
            raise ValueError('unsafe recovery inspection file')
        return f.read(1048577)
def record(name):
    path = root / name
    return json.loads(read(path)) if path.exists() else None
def project(raw):
    ha = yaml.safe_load(raw)['vm_ha']
    binding = VMHARuntimeBinding.model_validate(ha['runtime_binding'])
    local = next(n for n in binding.nodes if n.node_id == ha['node']['node_id'])
    credential = validate_installed_credential_bundle(binding, local)
    return {
        'cluster_id': ha['cluster_id'], 'node_id': local.node_id,
        'generation_id': ha['generation']['generation_id'],
        'digests': ha['generation']['digests'],
        'allocation_id': binding.shared_allocation_id,
        'nodes': {n.node_id: {'compute_id': n.compute_id,
                            'nic': n.network_interface_name} for n in binding.nodes},
        'credential_sha256': credential.bundle_digest,
        'mtls_operation_id': _vm_ha_mtls_operation_id(binding),
        'sha256': hashlib.sha256(raw).hexdigest(),
    }
with (root / 'rearm.lock').open('rb') as lock:
    deadline = time.monotonic() + 10
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline: raise
            time.sleep(0.05)
    installed_path = Path('/etc/nebius-vpngw/config-resolved.yaml')
    installed = read(installed_path) if installed_path.exists() else None
    policy_raw = record('standby-auto-healing-policy.json')
    policy = AutoHealingPolicyRecord.from_mapping(policy_raw) if policy_raw is not None else None
    staged = read('/etc/nebius-vpngw/vm-ha-staged/' + generation_id + '.yaml')
    pending = [n for n in (
        'manual-failover.json', 'manual-failback.json', 'rearm-request.json',
        'rearm-cloud-operation.json', 'standby-auto-healing-recovery.json',
        'accepted-cloud-operation.json', 'standby-replacement.release.json',
    ) if (root / n).exists()]
    # A disconnected package client may leave a live installer. Do not overlap it.
    for process in Path('/proc').glob('[0-9]*'):
        try:
            arguments = (process / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError):
            continue
        names = {Path(argument.decode(errors='replace')).name for argument in arguments if argument}
        if names.intersection({'pip', 'pip3', 'apt', 'apt-get', 'dpkg'}):
            pending.append('package-writer-active')
            break
    checkpoint = checkpoint_upgrade.inspect_checkpoint(record('controller-checkpoint.json'))
    if checkpoint.pending_action is not None:
        raise ValueError('pending controller action')
    from nebius_vpngw.agent.vm_ha.tunnels import require_tunnel_quiescent
    require_tunnel_quiescent(root)
    units = {}
    for unit in ('nebius-vpngw-vm-ha.service', 'nebius-vpngw-vm-ha-rearm.service',
                 'nebius-vpngw-agent.service', 'frr.service',
                 'strongswan-starter.service', 'strongswan.service'):
        p = subprocess.run(['systemctl', 'show', unit, '--property=LoadState,ActiveState,MainPID,ControlPID,Job'],
                           capture_output=True, text=True, check=True, timeout=10)
        units[unit] = dict(line.split('=', 1) for line in p.stdout.splitlines() if '=' in line)
    result = {
        'installed': project(installed) if installed is not None else None,
        'staged': project(staged),
        'policy': policy.to_dict() if policy is not None else None, 'pending': pending,
        'apply_lock': record('apply.lock'),
        'inhibition': record('standby-replacement.inhibition.json'),
        'guard': record('guard.json'),
        'boot_id': read('/proc/sys/kernel/random/boot_id').decode().strip(),
        'forwarding': read('/proc/sys/net/ipv4/ip_forward').decode().strip(),
        'units': units,
        'source_sha256': {str(p.relative_to(Path(nebius_vpngw.__file__).parent)):
                         hashlib.sha256(read(p)).hexdigest()
                         for p in Path(nebius_vpngw.__file__).parent.rglob('*.py')},
    }
    if (read(installed_path) if installed_path.exists() else None) != installed:
        raise ValueError('installed recovery configuration changed')
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
"""


def validate_host_recovery_evidence(
    state: VMHALifecycleState,
    evidence: dict,
    generations: dict,
    *,
    require_stopped: bool = False,
) -> AutoHealingPolicyRecord:
    """Validate both pinned hosts against independent lifecycle/cloud identities."""

    owner, target, cycle = validate_host_recovery_checkpoint(state)
    tx = state.transaction
    assert tx is not None
    bindings = dict(tx.resource_bindings)
    expected_nodes = {
        m.node_id: {"compute_id": m.compute_id, "nic": m.network_interface_name}
        for m in state.members
    }
    if set(evidence) != set(expected_nodes) or set(generations) != set(expected_nodes):
        raise ValueError("replacement recovery host evidence is incomplete")
    for member in state.members:
        item = evidence[member.node_id]
        generation_id, digests = generations[member.node_id]
        staged, installed = item["staged"], item["installed"]
        if installed is None and not (
            member == target
            and item["policy"] is None
            and item["units"]["nebius-vpngw-vm-ha.service"]["ActiveState"] == "inactive"
            and item["units"]["nebius-vpngw-vm-ha.service"]["MainPID"] == "0"
        ):
            raise ValueError("only the fresh replacement may lack an installed manifest")
        for projection in (staged,) if installed is None else (staged, installed):
            if not (
                projection["cluster_id"] == state.cluster_id
                and projection["node_id"] == member.node_id
                and projection["generation_id"] == generation_id
                and projection["digests"] == digests
                and projection["allocation_id"] == state.allocation_id
                and projection["nodes"][member.node_id] == expected_nodes[member.node_id]
            ):
                raise ValueError("replacement recovery installed generation or identity drifted")
        prior_nodes = {key: dict(value) for key, value in expected_nodes.items()}
        prior_nodes[target.node_id]["compute_id"] = bindings[
            vm_ha_passive_replacement_binding_key("retired-compute", target.instance_name, cycle)
        ]
        if staged["nodes"] != expected_nodes or (
            installed is not None
            and installed["nodes"]
            not in (
                expected_nodes,
                prior_nodes if member == owner else expected_nodes,
            )
        ):
            raise ValueError("replacement recovery peer identity drifted")
        if item["pending"]:
            raise ValueError("replacement recovery conflicts with pending host work")
        identity = {
            "cluster_id": state.cluster_id,
            "node_id": member.node_id,
            "generation_id": generation_id,
            "operation_id": tx.operation_id,
        }
        if member == owner:
            if item["apply_lock"] is not None or item["inhibition"] != {
                **identity,
                "schema": "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
            }:
                raise ValueError("replacement recovery owner inhibition changed")
        elif item["inhibition"] is not None or item["apply_lock"] != {
            **identity,
            "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
            "apply_locked": True,
        }:
            raise ValueError("replacement recovery standby lock changed")
        mtls = item["mtls"]
        if not (
            mtls["cluster_id"] == state.cluster_id
            and mtls["node_id"] == member.node_id
            and mtls["compute_id"] == member.compute_id
            and mtls["operation_id"] == staged["mtls_operation_id"]
            and mtls["operation_kind"] == "replacement"
            and mtls["phase"] == "local-active"
            and mtls["preserve_local"] is (member == owner)
            and mtls["inhibited"] is False
            and mtls["inhibition_operation_id"] is None
        ):
            raise ValueError("replacement recovery mTLS transaction changed")
        if require_stopped:
            guard = item["guard"]
            if not (
                item["forwarding"] == "0"
                and isinstance(guard, dict)
                and guard.get("data_plane_mode") == "blocked"
                and guard.get("guard_boot_id") == item["boot_id"]
                and item["boot_id"]
            ):
                raise ValueError("replacement recovery current-boot fence is absent")
            expected_units = {
                "nebius-vpngw-vm-ha.service",
                "nebius-vpngw-vm-ha-rearm.service",
                "nebius-vpngw-agent.service",
                "frr.service",
                "strongswan-starter.service",
                "strongswan.service",
            }
            if set(item["units"]) != expected_units:
                raise ValueError("replacement recovery service evidence is incomplete")
            for name, unit in item["units"].items():
                if unit["LoadState"] == "not-found":
                    if name in {"nebius-vpngw-vm-ha.service", "nebius-vpngw-vm-ha-rearm.service"}:
                        raise ValueError("replacement recovery required service is absent")
                    continue
                if not (
                    unit["ActiveState"] in {"inactive", "failed"}
                    and unit["MainPID"] == "0"
                    and unit["ControlPID"] == "0"
                    and unit["Job"] == ""
                ):
                    raise ValueError("replacement recovery writer or service job is live")
    policy = AutoHealingPolicyRecord.from_mapping(evidence[owner.node_id]["policy"])
    replacement_raw = evidence[target.node_id]["policy"]
    replacement_policy = (
        AutoHealingPolicyRecord.from_mapping(replacement_raw)
        if replacement_raw is not None
        else None
    )
    from ..agent.vm_ha.auto_healing import is_initialized_enabled_policy

    canonical_initial = bool(
        is_initialized_enabled_policy(policy)
        and policy.peer_ack_digest is None
        and (
            (replacement_policy is None and evidence[target.node_id]["installed"] is None)
            or (
                replacement_policy is not None
                and is_initialized_enabled_policy(replacement_policy)
                and replacement_policy.predecessor_digest == "0" * 64
                and replacement_policy.peer_ack_digest is None
            )
        )
    )
    activated_pair = bool(
        f"activate-{target.node_id}" in tx.completed_effects
        and replacement_policy is not None
        and evidence[target.node_id]["installed"] is not None
        and policy.desired is replacement_policy.desired is StandbyAutoHealing.ENABLED
        and policy.phase is replacement_policy.phase is AutoHealingPolicyPhase.COMMITTED
        and policy.peer_ack_digest == replacement_policy.peer_ack_digest == policy.decision_digest
        and all(
            getattr(policy, key) == getattr(replacement_policy, key)
            for key in (
                "operation_id",
                "coordinator_node_id",
                "predecessor_digest",
                "decision_digest",
            )
        )
    )
    if not (
        (canonical_initial or activated_pair)
        and policy.cluster_id == state.cluster_id
        and policy.node_id == owner.node_id
        and policy.peer_node_id == target.node_id
        and policy.generation_id == generations[owner.node_id][0]
        and (
            replacement_policy is None
            or (
                replacement_policy.cluster_id == state.cluster_id
                and replacement_policy.node_id == target.node_id
                and replacement_policy.peer_node_id == owner.node_id
                and replacement_policy.generation_id == policy.generation_id
            )
        )
    ):
        raise ValueError("replacement recovery policy is not canonical and quiescent")
    recovery = tx.replacement_host_recovery
    if recovery is not None and policy.to_dict() != json.loads(recovery.owner_policy_json):
        raise ValueError("replacement recovery original policy changed")
    return policy


def validate_host_recovery_owner(
    record: object, evidence: dict, *, operation_id: str, owner_node_id: str
) -> None:
    """Admit a quiescent inhibited owner, including an independently fenced crash."""
    if not (
        isinstance(record, dict)
        and record.get("transfer_inhibition_operation_id") == operation_id
        and record.get("transfer_inhibition_quiescent") is True
        and record.get("pending_operation_id") is None
    ):
        raise ValueError("replacement recovery retained owner is not safely inhibited")
    active = (
        record.get("state") == "active"
        and record.get("promotion_ready") is True
        and record.get("observed_owner_node_id") == owner_node_id
        and evidence["forwarding"] == "1"
    )
    guard = evidence["guard"]
    fenced = (
        record.get("state") == "blocked"
        and record.get("promotion_ready") is False
        and evidence["forwarding"] == "0"
        and isinstance(guard, dict)
        and guard.get("data_plane_mode") == "blocked"
        and guard.get("guard_boot_id") == evidence["boot_id"]
        and bool(evidence["boot_id"])
    )
    if not (active or fenced):
        raise ValueError(
            "replacement recovery retained owner lacks active or current-boot fence proof"
        )


def replacement_policy_key(state: VMHALifecycleState) -> str:
    """Identify the original policy for this exact replacement, never a later one."""

    tx = state.transaction
    if tx is None:
        raise ValueError("replacement policy requires a transaction")
    candidates = []
    for member in state.members:
        cycle = vm_ha_passive_replacement_cycle_for_approval(
            dict(tx.resource_bindings), member.instance_name, tx.approval_digest
        )
        if cycle is not None:
            candidates.append(f"standby-replacement-{cycle}-policy:{member.instance_name}")
    if len(candidates) != 1:
        raise ValueError("replacement policy cycle is unavailable or ambiguous")
    return candidates[0]


def host_recovery_effect(action: str, node_id: str) -> str:
    if action not in {"stop", "install", "refresh"}:
        raise ValueError("replacement host recovery action is invalid")
    return f"replacement-host-recovery-{action}-{node_id}"


def validate_host_recovery_checkpoint(
    state: VMHALifecycleState,
) -> tuple[VMHALifecycleMember, VMHALifecycleMember, int]:
    """Admit exact created resources before replacement verification and release."""

    tx = state.transaction
    if (
        state.record_version != 4
        or state.status is not VMHALifecycleStatus.ACTIVATING
        or tx is None
        or tx.accepted_cloud_operation_id is not None
        or tx.accepted_cloud_operation_effect is not None
        or tx.observation_guard is not None
    ):
        raise ValueError("replacement host recovery requires an exact preverification checkpoint")
    allowed = {
        host_recovery_effect(action, member.node_id)
        for member in state.members
        for action in ("stop", "install", "refresh")
    }
    if tx.pending_effect is not None and not (
        tx.replacement_host_recovery is not None and tx.pending_effect in allowed
    ):
        raise ValueError("replacement host recovery has an unrelated pending effect")
    bindings = dict(tx.resource_bindings)
    matches = []
    for member in state.members:
        cycle = vm_ha_passive_replacement_cycle_for_approval(
            bindings, member.instance_name, tx.approval_digest
        )
        if (
            cycle is not None
            and vm_ha_missing_standby_disk_name_binding_key(member.instance_name, cycle) in bindings
        ):
            matches.append((member, cycle))
    if len(matches) != 1 or "heartbeat-upgrade-artifact" in bindings:
        raise ValueError("replacement host recovery target or artifact is ambiguous")
    target, cycle = matches[0]
    owner = next(member for member in state.members if member != target)
    if any(
        (effect.startswith("activate-") and effect != f"activate-{target.node_id}")
        or effect.startswith(
            (
                "publish-live-replacement-peer-identity-",
                "reconcile-replacement-policy-",
                "release-standby-replacement-inhibition-",
                "unlock-",
            )
        )
        or effect == "verify-passive-unlocked-non-forwarding"
        for effect in tx.completed_effects
    ):
        raise ValueError("replacement host recovery passed its verification frontier")
    required = {
        vm_ha_missing_standby_replacement_effect(target.instance_name, cycle, action)
        for action in ("create-boot-disk", "create-compute")
    }
    if not required.issubset(tx.completed_effects) or any(
        "release" in effect and "inhibition" in effect for effect in tx.completed_effects
    ):
        raise ValueError("replacement host recovery creation or inhibition frontier is invalid")
    for kind, identity in (("compute", target.compute_id), ("disk", target.disk_id)):
        if (
            not identity
            or identity
            != bindings.get(
                vm_ha_passive_replacement_binding_key(kind, target.instance_name, cycle)
            )
            or identity
            == bindings.get(
                vm_ha_passive_replacement_binding_key(
                    "retired-" + kind, target.instance_name, cycle
                )
            )
        ):
            raise ValueError("replacement host recovery created identity is invalid")
    sequences = vm_ha_missing_standby_owner_sequences(bindings)
    if not sequences:
        raise ValueError("replacement host recovery retained owner is unavailable")
    sequence = sequences[-1]
    if any(
        bindings.get(vm_ha_missing_standby_owner_binding_key(kind, sequence)) != value
        for kind, value in (
            ("instance", owner.instance_name),
            ("compute", owner.compute_id),
            ("nic", owner.network_interface_name),
        )
    ):
        raise ValueError("replacement host recovery retained owner changed")
    return owner, target, cycle


def authorize_host_recovery(
    state: VMHALifecycleState,
    *,
    approval_digest: str,
    artifact_sha256: str,
    owner_policy: AutoHealingPolicyRecord,
    ssh_trust_sha256: str,
    host_binding_sha256: str,
) -> VMHALifecycleState:
    owner, target, cycle = validate_host_recovery_checkpoint(state)
    tx = state.transaction
    assert tx is not None
    policy_json = json.dumps(owner_policy.to_dict(), sort_keys=True, separators=(",", ":"))
    existing = tx.replacement_host_recovery
    if existing is not None:
        if (
            existing.approval_digest != approval_digest
            or existing.artifact_sha256 != artifact_sha256
            or existing.owner_policy_json != policy_json
            or existing.ssh_trust_sha256 != ssh_trust_sha256
            or existing.host_binding_sha256 != host_binding_sha256
        ):
            raise ValueError("replacement host recovery authorization changed")
        return state
    if tx.pending_effect is not None or not (
        owner_policy.cluster_id == state.cluster_id
        and owner_policy.node_id == owner.node_id
        and owner_policy.peer_node_id == target.node_id
    ):
        raise ValueError("replacement host recovery policy identity is foreign")
    if owner_policy.peer_ack_digest is not None and not (
        f"activate-{target.node_id}" in tx.completed_effects
        and dict(tx.resource_bindings).get(replacement_policy_key(state)) == policy_json
    ):
        raise ValueError(
            "replacement host recovery acknowledged policy lacks exact activation history"
        )
    recovery = VMHAReplacementHostRecovery(
        approval_digest=approval_digest,
        prior_record_sha256=state.record_sha256,
        prior_artifact_sha256=dict(tx.resource_bindings).get("heartbeat-initial-artifact", ""),
        artifact_sha256=artifact_sha256,
        owner_node_id=owner.node_id,
        target_instance_name=target.instance_name,
        cycle=cycle,
        owner_policy_json=policy_json,
        ssh_trust_sha256=ssh_trust_sha256,
        host_binding_sha256=host_binding_sha256,
    )
    audit_key = f"standby-replacement-{cycle}-host-recovery:{target.instance_name}"
    advanced = tx.advance(
        predecessor_sha256=state.record_sha256,
        checkpoint="replacement-host-recovery-authorized",
        resource_updates={
            audit_key: json.dumps(recovery.to_dict(), sort_keys=True, separators=(",", ":")),
            replacement_policy_key(state): policy_json,
        },
    )
    return replace(state, transaction=replace(advanced, replacement_host_recovery=recovery))


def advance_host_recovery(state: VMHALifecycleState, phase: str) -> VMHALifecycleState:
    owner, _target, _cycle = validate_host_recovery_checkpoint(state)
    tx = state.transaction
    assert tx is not None
    recovery = tx.replacement_host_recovery
    if recovery is None or tx.pending_effect is not None:
        raise ValueError("replacement host recovery cannot advance")
    if phase == recovery.phase:
        return state
    if (recovery.phase, phase) not in {
        ("authorized", "packages-ready"),
        ("packages-ready", "owner-ready"),
    }:
        raise ValueError("replacement host recovery phase cannot be skipped")
    required = {
        host_recovery_effect(action, member.node_id)
        for member in state.members
        for action in ("stop", "install")
    }
    if phase == "owner-ready":
        required.add(host_recovery_effect("refresh", owner.node_id))
    if not required.issubset(tx.completed_effects):
        raise ValueError("replacement host recovery has incomplete host effects")
    bindings = dict(tx.resource_bindings)
    expected = (
        recovery.prior_artifact_sha256
        if recovery.phase == "authorized"
        else recovery.artifact_sha256
    )
    if bindings.get("heartbeat-initial-artifact") != expected:
        raise ValueError("replacement host recovery artifact binding drifted")
    bindings["heartbeat-initial-artifact"] = recovery.artifact_sha256
    advanced = tx.advance(
        predecessor_sha256=state.record_sha256,
        checkpoint="replacement-host-recovery-" + phase,
    )
    return replace(
        state,
        transaction=replace(
            advanced,
            resource_bindings=tuple(sorted(bindings.items())),
            replacement_host_recovery=replace(recovery, phase=phase),
        ),
    )


def validate_host_recovery_successor(
    previous: VMHALifecycleState,
    successor: VMHALifecycleState,
) -> None:
    before, after = previous.transaction, successor.transaction
    if before is None or after is None or after.replacement_host_recovery is None:
        raise ValueError("replacement host recovery authority cannot disappear")
    recovery = after.replacement_host_recovery
    if before.replacement_host_recovery is None:
        expected = authorize_host_recovery(
            previous,
            approval_digest=recovery.approval_digest,
            artifact_sha256=recovery.artifact_sha256,
            owner_policy=AutoHealingPolicyRecord.from_mapping(
                json.loads(recovery.owner_policy_json)
            ),
            ssh_trust_sha256=recovery.ssh_trust_sha256,
            host_binding_sha256=recovery.host_binding_sha256,
        )
    else:
        expected = advance_host_recovery(previous, recovery.phase)
    if successor != expected:
        raise ValueError("replacement host recovery successor changed unrelated authority")


def validate_recovery_host_effects(
    previous: VMHALifecycleState, successor: VMHALifecycleState
) -> None:
    before, after = previous.transaction, successor.transaction
    assert before is not None and after is not None
    recovery = before.replacement_host_recovery
    if recovery is None or recovery.phase == "owner-ready":
        return
    allowed = {
        host_recovery_effect(action, member.node_id)
        for member in previous.members
        for action in ("stop", "install")
    }
    if recovery.phase == "packages-ready":
        allowed = {host_recovery_effect("refresh", recovery.owner_node_id)}
    added = set(after.completed_effects) - set(before.completed_effects)
    installing = any(
        effect.startswith("replacement-host-recovery-install-")
        for effect in added | ({after.pending_effect} if after.pending_effect else set())
    )
    stops = {host_recovery_effect("stop", member.node_id) for member in previous.members}
    if installing and not stops.issubset(before.completed_effects):
        raise ValueError("both writer stop proofs must precede either package install")
    owner_install = host_recovery_effect("install", recovery.owner_node_id)
    target_node = next(
        m.node_id for m in previous.members if m.instance_name == recovery.target_instance_name
    )
    if (owner_install in added or after.pending_effect == owner_install) and (
        host_recovery_effect("install", target_node) not in before.completed_effects
    ):
        raise ValueError("replacement package install must precede the retained owner")
    if (
        successor.status is not VMHALifecycleStatus.ACTIVATING
        or not previous.has_same_identity(successor)
        or after.resource_bindings != before.resource_bindings
        or after.observation != before.observation
        or after.accepted_cloud_operation_id is not None
        or after.observation_guard is not None
        or (after.pending_effect is not None and after.pending_effect not in allowed)
        or not added.issubset(allowed)
    ):
        raise ValueError("only declared host effects may advance replacement recovery")
