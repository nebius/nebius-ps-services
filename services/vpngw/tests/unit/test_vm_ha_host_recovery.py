from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_vm_ha_lifecycle import _member, _missing_standby_state

from nebius_vpngw.agent.vm_ha.auto_healing import (
    AutoHealingPolicyError,
    AutoHealingPolicyPhase,
    AutoHealingPolicyRecord,
    AutoHealingPolicyStore,
    StandbyAutoHealing,
    is_initialized_enabled_policy,
    policy_decision_digest,
)
from nebius_vpngw.cli import _vm_ha_replacement_policy_reproof_transaction
from nebius_vpngw.deploy.vm_ha_host_recovery import (
    advance_host_recovery,
    authorize_host_recovery,
    host_recovery_effect,
    replacement_policy_key,
    validate_host_recovery_checkpoint,
    validate_host_recovery_evidence,
)
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleJournal,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
    vm_ha_missing_standby_replacement_effect,
    vm_ha_passive_replacement_binding_key,
)


def _policy(path: Path, node: str = "node-0", peer: str = "node-1", depth: int = 3):
    store = AutoHealingPolicyStore(path)
    value = None
    for index in range(depth):
        value = store.initialize(
            cluster_id="cluster",
            node_id=node,
            peer_node_id=peer,
            generation_id=f"{index + 1:064x}",
            updated_at=index + 1.0,
        )
    assert value is not None
    return store, value


def _checkpoint(tmp_path: Path):
    state = _missing_standby_state(prior_artifact_key="heartbeat-initial-artifact")
    state = replace(
        state,
        transaction=replace(
            state.transaction,
            resource_bindings=tuple(
                sorted(
                    {
                        **dict(state.transaction.resource_bindings),
                        "heartbeat-initial-artifact": "a" * 64,
                    }.items()
                )
            ),
        ),
    )
    store = VMHALifecycleStore(tmp_path / "gateway.yaml")
    store.write_verified(state)
    journal = VMHALifecycleJournal(store, state)
    for kind, action in (("disk", "create-boot-disk"), ("compute", "create-compute")):
        effect = vm_ha_missing_standby_replacement_effect("gateway-1", 1, action)
        journal.begin(effect)
        journal.complete(
            effect,
            resource_updates={
                vm_ha_passive_replacement_binding_key(kind, "gateway-1", 1): kind + "-new"
            },
        )
    before = journal.state
    assert before.transaction is not None
    state = replace(
        before,
        status=VMHALifecycleStatus.ACTIVATING,
        members=(
            before.members[0],
            replace(_member(1, "passive"), compute_id="compute-new", disk_id="disk-new"),
        ),
        transaction=before.transaction.advance(
            predecessor_sha256=before.record_sha256, checkpoint="activating"
        ),
    )
    store.write_verified(state, predecessor_sha256=before.record_sha256)
    _, owner = _policy(tmp_path / "owner")
    return store, state, owner


def _persist(store, before, after):
    store.write_verified(after, predecessor_sha256=before.record_sha256)
    loaded = store.read(expected_project_id="project-1", expected_gateway_name="gateway")
    assert loaded == after
    return loaded


@pytest.mark.parametrize("depth", (1, 2, 3))
@pytest.mark.parametrize("owner_node", ("node-0", "node-1"))
def test_rebound_adoption_and_reproof_at_every_persisted_policy_checkpoint(
    tmp_path, depth, owner_node
):
    peer_node = "node-1" if owner_node == "node-0" else "node-0"
    owner_store, original = _policy(tmp_path / "owner", owner_node, peer_node, depth)
    replacement_store = AutoHealingPolicyStore(tmp_path / "replacement")
    replacement_store.initialize(
        cluster_id="cluster",
        node_id=peer_node,
        peer_node_id=owner_node,
        generation_id=original.generation_id,
        updated_at=5.0,
    )
    adopted = replacement_store.adopt_replacement_peer(
        cluster_id="cluster",
        node_id=peer_node,
        peer_node_id=owner_node,
        generation_id=original.generation_id,
        peer=original,
        updated_at=6.0,
    )
    assert adopted.predecessor_digest == original.predecessor_digest
    assert adopted.decision_digest == original.decision_digest
    tx = _vm_ha_replacement_policy_reproof_transaction(
        owner=original, replacement=adopted, original=original
    )
    assert tx is not None and tx.predecessor_digest == original.decision_digest
    stores = {owner_node: owner_store, peer_node: replacement_store}
    coordinator, second = sorted(stores)
    for action, node in (
        ("prepare", coordinator),
        ("prepare", second),
        ("commit", second),
        ("commit", coordinator),
    ):
        other = next(n for n in stores if n != node)
        current_peer = stores[other].load()
        getattr(stores[node], action)(
            cluster_id="cluster",
            node_id=node,
            peer_node_id=other,
            generation_id=original.generation_id,
            desired=StandbyAutoHealing.ENABLED,
            operation_id=tx.operation_id,
            coordinator_node_id=coordinator,
            predecessor_digest=tx.predecessor_digest,
            peer=current_peer,
            updated_at=7.0,
        )
        # Both sides are reloaded from disk; no zero-root reconstruction is possible.
        assert (
            _vm_ha_replacement_policy_reproof_transaction(
                owner=AutoHealingPolicyStore(tmp_path / "owner").load(),
                replacement=AutoHealingPolicyStore(tmp_path / "replacement").load(),
                original=original,
            )
            == tx
        )
    for store in stores.values():
        final = store.load()
        assert final.phase is AutoHealingPolicyPhase.COMMITTED
        assert final.peer_ack_digest == final.decision_digest
        assert final.predecessor_digest == original.decision_digest


@pytest.mark.parametrize(
    "field,value",
    (
        ("operation_id", "f" * 64),
        ("coordinator_node_id", "node-1"),
        ("desired", StandbyAutoHealing.DISABLED),
        ("phase", AutoHealingPolicyPhase.PREPARED),
    ),
)
def test_noncanonical_unacknowledged_policy_is_not_adoptable(tmp_path, field, value):
    _, original = _policy(tmp_path / "owner")
    fields = {**original.__dict__, field: value}
    if field == "coordinator_node_id":
        with pytest.raises(AutoHealingPolicyError, match="coordinator"):
            AutoHealingPolicyRecord(**fields)
        return
    fields["decision_digest"] = policy_decision_digest(
        **{
            key: fields[key]
            for key in (
                "cluster_id",
                "generation_id",
                "desired",
                "operation_id",
                "coordinator_node_id",
                "predecessor_digest",
            )
        },
        member_node_ids=(original.node_id, original.peer_node_id),
    )
    changed = AutoHealingPolicyRecord(**fields)
    assert not is_initialized_enabled_policy(changed)
    replacement_store = AutoHealingPolicyStore(tmp_path / "replacement")
    replacement_store.initialize(
        cluster_id="cluster",
        node_id="node-1",
        peer_node_id="node-0",
        generation_id=original.generation_id,
        updated_at=5.0,
    )
    with pytest.raises(AutoHealingPolicyError):
        replacement_store.adopt_replacement_peer(
            cluster_id="cluster",
            node_id="node-1",
            peer_node_id="node-0",
            generation_id=original.generation_id,
            peer=changed,
            updated_at=6.0,
        )


def test_rebound_replacement_is_not_a_fresh_adoption_target(tmp_path):
    _, owner = _policy(tmp_path / "owner")
    replacement_store, _ = _policy(tmp_path / "replacement", "node-1", "node-0")
    with pytest.raises(AutoHealingPolicyError, match="default-initialized"):
        replacement_store.adopt_replacement_peer(
            cluster_id="cluster",
            node_id="node-1",
            peer_node_id="node-0",
            generation_id=owner.generation_id,
            peer=owner,
            updated_at=6.0,
        )


def test_recovery_retains_identities_audit_and_idempotency_across_every_checkpoint(tmp_path):
    store, initial, policy = _checkpoint(tmp_path)
    state = _persist(
        store,
        initial,
        authorize_host_recovery(
            initial,
            approval_digest="c" * 64,
            artifact_sha256="b" * 64,
            owner_policy=policy,
            ssh_trust_sha256="d" * 64,
            host_binding_sha256="e" * 64,
        ),
    )
    assert state.transaction.operation_id == initial.transaction.operation_id
    assert state.members == initial.members
    assert dict(state.transaction.resource_bindings)["heartbeat-initial-artifact"] == "a" * 64
    assert dict(state.transaction.resource_bindings)[replacement_policy_key(state)] == json.dumps(
        policy.to_dict(), sort_keys=True, separators=(",", ":")
    )
    assert state.transaction.replacement_host_recovery.prior_record_sha256 == initial.record_sha256
    assert (
        authorize_host_recovery(
            state,
            approval_digest="c" * 64,
            artifact_sha256="b" * 64,
            owner_policy=policy,
            ssh_trust_sha256="d" * 64,
            host_binding_sha256="e" * 64,
        )
        == state
    )
    with pytest.raises(ValueError):
        advance_host_recovery(state, "packages-ready")
    journal = VMHALifecycleJournal(store, state)
    for action in ("stop", "install"):
        for node in ("node-1", "node-0"):
            effect = host_recovery_effect(action, node)
            op = journal.begin(effect)
            journal = VMHALifecycleJournal(
                store, store.read(expected_project_id="project-1", expected_gateway_name="gateway")
            )
            assert journal.begin(effect) == op
            journal.complete(effect)
    state = _persist(store, journal.state, advance_host_recovery(journal.state, "packages-ready"))
    assert dict(state.transaction.resource_bindings)["heartbeat-initial-artifact"] == "b" * 64
    assert state.transaction.replacement_host_recovery.prior_artifact_sha256 == "a" * 64
    journal = VMHALifecycleJournal(store, state)
    effect = host_recovery_effect("refresh", "node-0")
    journal.begin(effect)
    journal.complete(effect)
    final = _persist(store, journal.state, advance_host_recovery(journal.state, "owner-ready"))
    assert set(initial.transaction.completed_effects).issubset(final.transaction.completed_effects)
    assert final.members == initial.members
    assert final.transaction.approval_digest == initial.transaction.approval_digest
    assert (
        final.transaction.replacement_host_recovery.owner_policy_json
        == state.transaction.replacement_host_recovery.owner_policy_json
    )
    with pytest.raises(ValueError, match="compare-and-swap"):
        store.write_verified(state, predecessor_sha256=initial.record_sha256)


@pytest.mark.parametrize(
    "mutation", ("artifact", "owner", "phase", "policy", "identity", "effect", "cloud")
)
def test_recovery_successor_cannot_rebind_or_skip_authority(tmp_path, mutation):
    store, initial, policy = _checkpoint(tmp_path)
    state = authorize_host_recovery(
        initial,
        approval_digest="c" * 64,
        artifact_sha256="b" * 64,
        owner_policy=policy,
        ssh_trust_sha256="d" * 64,
        host_binding_sha256="e" * 64,
    )
    recovery = state.transaction.replacement_host_recovery
    if mutation == "artifact":
        state = replace(
            state,
            transaction=replace(
                state.transaction,
                resource_bindings=tuple(
                    sorted(
                        {
                            **dict(state.transaction.resource_bindings),
                            "heartbeat-initial-artifact": "b" * 64,
                        }.items()
                    )
                ),
            ),
        )
    elif mutation == "owner":
        state = replace(state, transaction=replace(state.transaction, operation_id="9" * 64))
    elif mutation == "phase":
        state = replace(
            state,
            transaction=replace(
                state.transaction, replacement_host_recovery=replace(recovery, phase="owner-ready")
            ),
        )
    elif mutation == "policy":
        state = replace(
            state,
            transaction=replace(
                state.transaction,
                replacement_host_recovery=replace(recovery, prior_record_sha256="e" * 64),
            ),
        )
    elif mutation == "identity":
        state = replace(
            state, members=(replace(state.members[0], compute_id="foreign"), state.members[1])
        )
    elif mutation == "effect":
        state = replace(
            state,
            transaction=replace(
                state.transaction,
                completed_effects=tuple(
                    sorted((*state.transaction.completed_effects, "activate-node-1"))
                ),
            ),
        )
    else:
        state = replace(
            state,
            transaction=replace(
                state.transaction,
                pending_effect="cloud",
                accepted_cloud_operation_effect="cloud",
                accepted_cloud_operation_id="accepted",
            ),
        )
    with pytest.raises(ValueError):
        store.write_verified(state, predecessor_sha256=initial.record_sha256)


def test_recovery_requires_both_stop_receipts_before_either_install(tmp_path):
    store, initial, policy = _checkpoint(tmp_path)
    state = _persist(
        store,
        initial,
        authorize_host_recovery(
            initial,
            approval_digest="c" * 64,
            artifact_sha256="b" * 64,
            owner_policy=policy,
            ssh_trust_sha256="d" * 64,
            host_binding_sha256="e" * 64,
        ),
    )
    journal = VMHALifecycleJournal(store, state)
    with pytest.raises(ValueError, match="both writer stop"):
        journal.begin(host_recovery_effect("install", "node-1"))
    journal.begin(host_recovery_effect("stop", "node-1"))
    journal.complete(host_recovery_effect("stop", "node-1"))
    with pytest.raises(ValueError, match="both writer stop"):
        journal.begin(host_recovery_effect("install", "node-0"))
    with pytest.raises(ValueError):
        journal.begin("activate-node-1")


def _evidence(state, owner_policy):
    owner, target, _ = validate_host_recovery_checkpoint(state)
    nodes = {
        m.node_id: {"compute_id": m.compute_id, "nic": m.network_interface_name}
        for m in state.members
    }
    generations = {
        m.node_id: (
            owner_policy.generation_id,
            {"configuration": "a" * 64, "static_routes": "b" * 64, "bgp_policy": "c" * 64},
        )
        for m in state.members
    }
    evidence = {}
    for m in state.members:
        generation, digests = generations[m.node_id]
        identity = {
            "cluster_id": state.cluster_id,
            "node_id": m.node_id,
            "generation_id": generation,
            "operation_id": state.transaction.operation_id,
        }
        projection = {
            "cluster_id": state.cluster_id,
            "node_id": m.node_id,
            "generation_id": generation,
            "digests": digests,
            "allocation_id": state.allocation_id,
            "nodes": nodes,
            "mtls_operation_id": "e" * 64,
        }
        evidence[m.node_id] = {
            "installed": copy.deepcopy(projection) if m == owner else None,
            "staged": copy.deepcopy(projection),
            "policy": owner_policy.to_dict() if m == owner else None,
            "apply_lock": None
            if m == owner
            else {**identity, "schema": "nebius-vpngw/vm-ha-apply-lock-v2", "apply_locked": True},
            "inhibition": {
                **identity,
                "schema": "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
            }
            if m == owner
            else None,
            "pending": [],
            "forwarding": "0",
            "boot_id": "boot",
            "guard": {"data_plane_mode": "blocked", "guard_boot_id": "boot"},
            "units": {
                name: {
                    "LoadState": "loaded",
                    "ActiveState": "inactive",
                    "MainPID": "0",
                    "ControlPID": "0",
                    "Job": "",
                }
                for name in (
                    "nebius-vpngw-vm-ha.service",
                    "nebius-vpngw-vm-ha-rearm.service",
                    "nebius-vpngw-agent.service",
                    "frr.service",
                    "strongswan-starter.service",
                    "strongswan.service",
                )
            },
            "mtls": {
                "cluster_id": state.cluster_id,
                "node_id": m.node_id,
                "compute_id": m.compute_id,
                "operation_id": "e" * 64,
                "operation_kind": "replacement",
                "phase": "local-active",
                "preserve_local": m == owner,
                "inhibited": False,
                "inhibition_operation_id": None,
            },
        }
    return evidence, generations


@pytest.mark.parametrize(
    "mutation",
    (
        None,
        "failed-quiescent",
        "forwarding",
        "boot",
        "pid",
        "job",
        "inhibition",
        "lock",
        "pending",
        "mtls",
        "compute",
        "generation",
        "owner-manifest",
        "standby-manifest",
    ),
)
def test_recovery_host_barrier_rejects_changed_or_missing_proof(tmp_path, mutation):
    _, state, policy = _checkpoint(tmp_path)
    evidence, generations = _evidence(state, policy)
    if mutation == "failed-quiescent":
        evidence["node-0"]["units"]["frr.service"]["ActiveState"] = "failed"
    elif mutation == "forwarding":
        evidence["node-1"]["forwarding"] = "1"
    elif mutation == "boot":
        evidence["node-1"]["boot_id"] = "other"
    elif mutation == "pid":
        evidence["node-0"]["units"]["nebius-vpngw-vm-ha.service"]["MainPID"] = "12"
    elif mutation == "job":
        evidence["node-1"]["units"]["nebius-vpngw-vm-ha.service"]["Job"] = "1"
    elif mutation == "inhibition":
        evidence["node-0"]["inhibition"]["operation_id"] = "f" * 64
    elif mutation == "lock":
        evidence["node-1"]["apply_lock"] = None
    elif mutation == "pending":
        evidence["node-0"]["pending"] = ["accepted-cloud-operation.json"]
    elif mutation == "mtls":
        evidence["node-1"]["mtls"]["operation_id"] = "f" * 64
    elif mutation == "compute":
        evidence["node-1"]["staged"]["nodes"]["node-0"]["compute_id"] = "foreign"
    elif mutation == "generation":
        evidence["node-1"]["staged"]["generation_id"] = "f" * 64
    elif mutation == "owner-manifest":
        evidence["node-0"]["installed"] = None
    elif mutation == "standby-manifest":
        evidence["node-1"]["installed"] = copy.deepcopy(evidence["node-1"]["staged"])
    if mutation in {None, "failed-quiescent"}:
        assert (
            validate_host_recovery_evidence(state, evidence, generations, require_stopped=True)
            == policy
        )
    else:
        with pytest.raises(ValueError):
            validate_host_recovery_evidence(state, evidence, generations, require_stopped=True)


@pytest.mark.parametrize(
    "crash",
    (None, "stop-node-1", "stop-node-0", "install-node-1", "install-node-0", "refresh-node-0"),
)
@pytest.mark.parametrize("standby_activated", [False, True])
def test_public_recovery_executor_replays_host_effects_without_cloud_recreation(
    tmp_path, monkeypatch, crash, standby_activated
):
    from types import SimpleNamespace

    from nebius_vpngw import cli
    from nebius_vpngw.deploy import vm_ha_host_recovery as recovery

    store, initial, policy = _checkpoint(tmp_path)
    if standby_activated:
        journal = VMHALifecycleJournal(store, initial)
        journal.begin("activate-node-1")
        journal.complete("activate-node-1")
        initial = journal.state
    evidence, generations = _evidence(initial, policy)
    artifact = SimpleNamespace(sha256="b" * 64)
    report = cli._VMHAApplyPlanReport(
        kind="replacement-artifact-recovery",
        digest="c" * 64,
        engine_digest="d" * 64,
        effects=(),
        has_destructive_changes=False,
        managed_ssh_action=None,
        artifact_sha256=artifact.sha256,
        artifact=artifact,
    )
    instances = tuple(
        SimpleNamespace(
            vm_ha_node=SimpleNamespace(node_id=node),
            hostname="gateway-" + node[-1],
            external_ip="198.51.100." + str(10 + int(node[-1])),
        )
        for node in ("node-1", "node-0")
    )
    stopped, installed, events = set(), set(), []
    remaining_crash = [crash]
    for item in evidence.values():
        item["source_sha256"] = {"agent/main.py": "old"}
    authority = SimpleNamespace(
        condition="transitioning",
        lifecycle="activating",
        reasons=("lifecycle-activating",),
        unavailable_member_node_ids=(),
        observation_digest="a" * 64,
        operation_id=None,
        owner_node_id="node-0",
        member_compute_states=(("node-0", "running"), ("node-1", "running")),
    )
    inspection = SimpleNamespace(snapshot=SimpleNamespace(authority=authority))

    def current():
        return store.read(expected_project_id="project-1", expected_gateway_name="gateway")

    def inspected(*args, **kwargs):
        state = current()
        return report, cli._VMHAReplacementRecoveryContext(
            {},
            state,
            instances,
            SimpleNamespace(known_hosts_sha256="d" * 64),
            copy.deepcopy(evidence),
            generations,
            policy,
        )

    def event(name):
        assert current().transaction.replacement_host_recovery is not None
        events.append(name)
        if remaining_crash[0] == name:
            remaining_crash[0] = None
            raise RuntimeError("injected client loss after host effect")

    class SSH:
        def __init__(self, **kwargs):
            pass

        def stop_vm_ha_mutation_services(self, target, hostname, cfg):
            node = "node-" + hostname[-1]
            stopped.add(node)
            event("stop-" + node)

        def refresh_vm_ha_control_services(self, target, instance, cfg):
            assert installed == {"node-0", "node-1"}
            assert current().transaction.replacement_host_recovery.phase == "packages-ready"
            assert (
                dict(current().transaction.resource_bindings)["heartbeat-initial-artifact"]
                == "b" * 64
            )
            event("refresh-" + instance.vm_ha_node.node_id)

        def verify_vm_ha_standby_replacement_quiescent(self, *args, **kwargs):
            events.append("owner-ready-proof")

    def install(ssh, target, instance, local, *, artifact):
        assert stopped == {"node-0", "node-1"}
        node = instance.vm_ha_node.node_id
        if node == "node-0":
            assert "node-1" in installed
        installed.add(node)
        evidence[node]["source_sha256"] = {"agent/main.py": "new"}
        event("install-" + node)

    canonical = replace(report, kind="resume-transaction")

    def observe(*a, **kw):
        state = current()
        authority.operation_id = (
            state.transaction.operation_id if state.transaction.pending_effect else None
        )
        return inspection

    monkeypatch.setattr(cli, "_inspect_vm_ha_status_with_region", observe)
    monkeypatch.setattr(cli, "_inspect_vm_ha_replacement_artifact_recovery", inspected)
    monkeypatch.setattr(cli, "SSHPush", SSH)
    monkeypatch.setattr(
        cli, "_replacement_recovery_host_evidence", lambda *a, **kw: copy.deepcopy(evidence)
    )
    monkeypatch.setattr(cli, "_prepare_vm_ha_agent_package", install)
    monkeypatch.setattr(
        recovery, "host_recovery_artifact_sources", lambda a: {"agent/main.py": "new"}
    )
    monkeypatch.setattr(cli, "_plan_vm_ha_apply_convergence", lambda *a, **kw: canonical)
    monkeypatch.setattr(
        cli, "_execute_vm_ha_apply_convergence", lambda *a, **kw: events.append("canonical-resume")
    )
    if crash:
        with pytest.raises(RuntimeError, match="injected client loss"):
            cli._execute_vm_ha_replacement_artifact_recovery(
                tmp_path / "gateway.yaml", report, region=None, progress_sink=None
            )
    cli._execute_vm_ha_replacement_artifact_recovery(
        tmp_path / "gateway.yaml", report, region=None, progress_sink=None
    )
    final = current()
    assert final.transaction.replacement_host_recovery.phase == "owner-ready"
    assert final.transaction.operation_id == initial.transaction.operation_id
    assert final.members == initial.members
    assert events[-1] == "canonical-resume"
    assert (
        events.index("stop-node-0")
        < events.index("install-node-1")
        < events.index("install-node-0")
    )


def test_recovery_wire_is_strict_and_only_serializes_authorized_extension(tmp_path):
    _, initial, policy = _checkpoint(tmp_path)
    assert "replacement_host_recovery" not in initial.to_dict()["transaction"]
    state = authorize_host_recovery(
        initial,
        approval_digest="c" * 64,
        artifact_sha256="b" * 64,
        owner_policy=policy,
        ssh_trust_sha256="d" * 64,
        host_binding_sha256="e" * 64,
    )
    payload = state.to_dict()
    assert VMHALifecycleState.from_dict(payload) == state
    payload["transaction"]["replacement_host_recovery"]["override"] = True
    with pytest.raises(ValueError):
        VMHALifecycleState.from_dict(payload)
    with pytest.raises(ValueError):
        replace(state, record_version=3)


@pytest.mark.parametrize(
    "field", ("approval_digest", "artifact_sha256", "ssh_trust_sha256", "host_binding_sha256")
)
def test_recovery_replay_rejects_changed_authorization(tmp_path, field):
    _, initial, policy = _checkpoint(tmp_path)
    authority = dict(
        approval_digest="c" * 64,
        artifact_sha256="b" * 64,
        owner_policy=policy,
        ssh_trust_sha256="d" * 64,
        host_binding_sha256="e" * 64,
    )
    state = authorize_host_recovery(initial, **authority)
    authority[field] = "f" * 64
    with pytest.raises(ValueError, match="authorization changed"):
        authorize_host_recovery(state, **authority)


def test_recovery_requires_repaired_wheel_content(tmp_path):
    from test_ssh_push import _write_vm_ha_agent_wheel

    from nebius_vpngw.deploy.ssh_push import VMHAAgentArtifact, VMHAAgentArtifactError
    from nebius_vpngw.deploy.vm_ha_host_recovery import require_host_recovery_artifact

    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel)
    artifact = VMHAAgentArtifact.from_wheel(wheel, source="test")
    with pytest.raises(VMHAAgentArtifactError):
        require_host_recovery_artifact(artifact)


def test_recovery_admits_completed_standby_activation_without_releasing_authority(tmp_path):
    store, initial, policy = _checkpoint(tmp_path)
    journal = VMHALifecycleJournal(store, initial)
    journal.begin("activate-node-1")
    journal.complete("activate-node-1")
    activated = journal.state
    owner, target, _ = validate_host_recovery_checkpoint(activated)
    assert owner.node_id == "node-0" and target.node_id == "node-1"
    recovered = authorize_host_recovery(
        activated,
        approval_digest="c" * 64,
        artifact_sha256="b" * 64,
        owner_policy=policy,
        ssh_trust_sha256="d" * 64,
        host_binding_sha256="e" * 64,
    )
    store.write_verified(recovered, predecessor_sha256=activated.record_sha256)
    assert recovered.transaction.completed_effects == activated.transaction.completed_effects
    assert recovered.members == activated.members


@pytest.mark.parametrize(
    "effect",
    [
        "activate-node-0",
        "activate-foreign",
        "publish-live-replacement-peer-identity-node-0",
        "verify-passive-unlocked-non-forwarding",
        "reconcile-replacement-policy-node-1",
        "release-standby-replacement-inhibition-node-0",
    ],
)
def test_recovery_rejects_activation_or_postverification_frontier_drift(tmp_path, effect):
    _, initial, _ = _checkpoint(tmp_path)
    state = replace(
        initial,
        transaction=replace(
            initial.transaction,
            completed_effects=tuple(sorted((*initial.transaction.completed_effects, effect))),
        ),
    )
    with pytest.raises(ValueError):
        validate_host_recovery_checkpoint(state)


@pytest.mark.parametrize("mutation", [None, "ack", "generation", "prepared", "different-decision"])
def test_activated_recovery_requires_matching_committed_acknowledged_policy(tmp_path, mutation):
    store, initial, policy = _checkpoint(tmp_path)
    journal = VMHALifecycleJournal(store, initial)
    policy_key = replacement_policy_key(initial)
    fields = {**policy.__dict__, "operation_id": "7" * 64}
    fields["decision_digest"] = policy_decision_digest(
        **{
            key: fields[key]
            for key in (
                "cluster_id",
                "generation_id",
                "desired",
                "operation_id",
                "coordinator_node_id",
                "predecessor_digest",
            )
        },
        member_node_ids=(policy.node_id, policy.peer_node_id),
    )
    fields["peer_ack_digest"] = fields["decision_digest"]
    owner = AutoHealingPolicyRecord(**fields)
    original_policy_json = json.dumps(owner.to_dict(), sort_keys=True, separators=(",", ":"))
    journal.begin("persist-" + policy_key)
    journal.complete("persist-" + policy_key, resource_updates={policy_key: original_policy_json})
    journal.begin("activate-node-1")
    journal.complete("activate-node-1")
    peer = replace(owner, node_id=owner.peer_node_id, peer_node_id=owner.node_id)
    evidence, generations = _evidence(journal.state, owner)
    evidence["node-1"]["installed"] = copy.deepcopy(evidence["node-1"]["staged"])
    evidence["node-1"]["policy"] = peer.to_dict()
    if mutation == "ack":
        evidence["node-1"]["policy"]["peer_ack_digest"] = None
    elif mutation == "generation":
        evidence["node-1"]["policy"]["generation_id"] = "f" * 64
    elif mutation == "prepared":
        evidence["node-1"]["policy"]["phase"] = "prepared"
    elif mutation == "different-decision":
        evidence["node-1"]["policy"]["decision_digest"] = "f" * 64
    if mutation is None:
        assert (
            validate_host_recovery_evidence(
                journal.state,
                evidence,
                generations,
                require_stopped=True,
            )
            == owner
        )
        recovered = authorize_host_recovery(
            journal.state,
            approval_digest="c" * 64,
            artifact_sha256="b" * 64,
            owner_policy=owner,
            ssh_trust_sha256="d" * 64,
            host_binding_sha256="e" * 64,
        )
        loaded = _persist(store, journal.state, recovered)
        assert dict(loaded.transaction.resource_bindings)[policy_key] == original_policy_json
        assert (
            json.loads(loaded.transaction.replacement_host_recovery.owner_policy_json)
            == owner.to_dict()
        )
        assert loaded.members == journal.state.members
        assert loaded.transaction.completed_effects == journal.state.transaction.completed_effects
        for changed in (
            replace(
                journal.state,
                transaction=replace(
                    journal.state.transaction,
                    completed_effects=tuple(
                        e
                        for e in journal.state.transaction.completed_effects
                        if e != "activate-node-1"
                    ),
                ),
            ),
            replace(
                journal.state,
                transaction=replace(
                    journal.state.transaction,
                    resource_bindings=tuple(
                        (k, v)
                        for k, v in journal.state.transaction.resource_bindings
                        if k != policy_key
                    ),
                ),
            ),
        ):
            with pytest.raises(ValueError):
                authorize_host_recovery(
                    changed,
                    approval_digest="c" * 64,
                    artifact_sha256="b" * 64,
                    owner_policy=owner,
                    ssh_trust_sha256="d" * 64,
                    host_binding_sha256="e" * 64,
                )
    else:
        with pytest.raises((ValueError, AutoHealingPolicyError)):
            validate_host_recovery_evidence(
                journal.state, evidence, generations, require_stopped=True
            )


@pytest.mark.parametrize(
    "mutation", [None, "forwarding", "guard-boot", "guard-mode", "pending", "inhibition"]
)
def test_blocked_recovery_owner_requires_current_fence_and_exact_inhibition(tmp_path, mutation):
    from nebius_vpngw.deploy.vm_ha_host_recovery import validate_host_recovery_owner

    _, state, policy = _checkpoint(tmp_path)
    evidence, _ = _evidence(state, policy)
    record = {
        "state": "blocked",
        "promotion_ready": False,
        "transfer_inhibition_operation_id": state.transaction.operation_id,
        "transfer_inhibition_quiescent": True,
        "pending_operation_id": None,
    }
    item = evidence["node-0"]
    if mutation == "forwarding":
        item["forwarding"] = "1"
    elif mutation == "guard-boot":
        item["guard"]["guard_boot_id"] = "stale"
    elif mutation == "guard-mode":
        item["guard"]["data_plane_mode"] = "active"
    elif mutation == "pending":
        record["pending_operation_id"] = "foreign"
    elif mutation == "inhibition":
        record["transfer_inhibition_operation_id"] = "foreign"
    args = dict(operation_id=state.transaction.operation_id, owner_node_id="node-0")
    if mutation is None:
        validate_host_recovery_owner(record, item, **args)
    else:
        with pytest.raises(ValueError):
            validate_host_recovery_owner(record, item, **args)
