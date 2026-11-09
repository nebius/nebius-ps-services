from copy import deepcopy

import pytest

from nebius_vpngw.deploy.ha_composite import Composite


def test_durable_composite_never_changes_approved_intent(tmp_path):
    journal = Composite(tmp_path, "rotate-mtls")
    intent = {
        "action": "rotate-mtls",
        "prerequisite": "a" * 64,
        "artifact": "b" * 64,
        "rotation": {"operation": "c" * 64, "epoch": 3},
    }
    pending = journal.begin(intent)
    resumed = Composite(tmp_path, "rotate-mtls")
    assert resumed.begin(intent) == pending
    changed = deepcopy(intent)
    changed["artifact"] = "d" * 64
    with pytest.raises(RuntimeError, match="pending"):
        resumed.begin(changed)
    prepared = resumed.prepared(pending, "e" * 64)
    bound = resumed.bind_terminal(prepared, intent["rotation"])
    with pytest.raises(RuntimeError, match="intent changed"):
        resumed.bind_terminal(bound, {"operation": "f" * 64, "epoch": 3})
    expected = {"member-1": "1" * 64, "member-2": "2" * 64}
    recorded = resumed.record_result(bound, expected)
    with pytest.raises(RuntimeError, match="result identity"):
        resumed.record_result(recorded, {"member-1": "3" * 64})
    assert resumed.complete(recorded)["phase"] == "complete"


def test_prerequisite_receipt_cannot_be_replaced_on_resume(tmp_path):
    journal = Composite(tmp_path, "enable-standby-auto-healing")
    state = journal.begin({"action": "enable-standby-auto-healing"})
    state = journal.prepared(state, "a" * 64)
    with pytest.raises(RuntimeError, match="receipt changed"):
        journal.prepared(state, "b" * 64)
    with pytest.raises(RuntimeError, match="terminal effect"):
        journal.complete(state)


@pytest.fixture
def composed(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace as NS

    from nebius_vpngw import cli
    from nebius_vpngw.deploy import apply_command

    config = tmp_path / "gateway.yaml"
    config.write_text("project_id: project-a\ngateway_group:\n  name: gateway-a\n")
    state = {"command": None, "applies": 0, "locks": 0, "terminal_reads": 0, "crash": False}

    class Command:
        def __init__(self, *_):
            self.root = tmp_path / "journal"

        def snapshot(self):
            return state["command"]

    monkeypatch.setattr(apply_command, "CommandJournal", Command)
    members = [
        NS(
            node_id=f"node-{i}",
            compute_id=f"compute-{i}",
            role=role,
            network_interface_name="eth0",
            public_ip=f"192.0.2.{i + 1}",
        )
        for i, role in enumerate(("active", "passive"))
    ]
    lifecycle = NS(
        cluster_id="cluster",
        allocation_id="allocation",
        members=members,
        status=cli.VMHALifecycleStatus.ACTIVE,
        transaction=NS(pending_effect=None),
    )
    inspection = NS(
        project_id="project-a",
        gateway_name="gateway-a",
        snapshot=NS(
            lifecycle_state=lifecycle,
            authority=NS(condition="exact", owner_node_id="node-0"),
            authority_digest="d" * 64,
            view=NS(overall="HEALTHY"),
            members=[
                NS(node_id=member.node_id, record={"apply_locked": False}) for member in members
            ],
        ),
    )
    report = NS(
        digest="a" * 64,
        artifact_sha256="b" * 64,
        artifact=None,
        has_destructive_changes=False,
        kind="apply-convergence",
        effects=("upgrade",),
        impact=NS(resource_creation=False),
        managed_ssh_action=None,
        managed_credential_action="reuse",
    )

    @contextmanager
    def lock(**_):
        state["locks"] += 1
        yield

    def execute(*_args, **_kwargs):
        state["applies"] += 1
        state["command"] = {
            "bindings": {"runtime": {"approval": report.digest}},
            "transaction": {
                "approval": "9" * 64,
                "phase": "complete",
                "execution_id": "e" * 32,
                "receipts": {"runtime": {"sha256": "f" * 64}},
            },
        }

    def statuses(**_kwargs):
        state["terminal_reads"] += 1
        if state["crash"] and state["applies"]:
            state["crash"] = False
            raise RuntimeError("interrupted after prerequisite")
        return state["policies"]

    from nebius_vpngw.agent.vm_ha.auto_healing import AutoHealingPolicyStore

    state["policies"] = []
    for index, member in enumerate(members):
        policy = AutoHealingPolicyStore(tmp_path / f"policy-{index}.json").initialize(
            cluster_id="cluster",
            node_id=member.node_id,
            peer_node_id=members[1 - index].node_id,
            generation_id="a" * 64,
            updated_at=1,
        )
        state["policies"].append(
            {
                "node_id": member.node_id,
                "operation_id": policy.operation_id,
                "desired": "enabled",
                "record": policy.to_dict(),
            }
        )

    api = NS(
        **{
            name: getattr(cli, name)
            for name in (
                "VMHALifecycleStatus",
                "VMHACommandResult",
                "VMHACommandOutcome",
                "VMHACommandClassification",
                "VMHACommandHealth",
                "VMHACommandApproval",
                "VMHACommandImpact",
                "StandbyAutoHealing",
            )
        }
    )
    api.load_local_config = lambda _: {
        "project_id": "project-a",
        "gateway_group": {"name": "gateway-a"},
    }
    api.merge_with_peer_configs = lambda *_: NS(gateway_group=NS(name="gateway-a"))
    api._inspect_vm_ha_status_with_region = lambda *_a, **_k: inspection
    api._inspect_vm_ha_prerequisite_status = api._inspect_vm_ha_status_with_region
    api._plan_vm_ha_convergence_with_region = lambda *_a, **_k: report
    api._execute_vm_ha_convergence_with_region = execute
    api.VMHAApplyLock = lock
    api._run_vm_ha_auto_healing_statuses = statuses
    api._vm_ha_auto_healing_is_terminal = lambda *_: True
    api._confirm_vm_ha_healthy = lambda _p, observed, **_k: observed
    api._vm_ha_result_from_snapshot = lambda **_: "verified"
    return api, config, state, report


def test_composite_dry_run_then_one_lock_and_one_prerequisite(composed):
    from nebius_vpngw.deploy.ha_composite import converge

    api, config, state, _ = composed
    plan = converge(api, config, "enable-standby-auto-healing", dry_run=True, approve=None)
    assert state["applies"] == state["locks"] == 0
    assert plan.impact.vpn_traffic_interruption is True
    assert (
        converge(
            api, config, "enable-standby-auto-healing", dry_run=False, approve=plan.approval.digest
        )
        == "verified"
    )
    assert state["applies"] == state["locks"] == 1


def test_crash_after_upgrade_resumes_terminal_without_reapplying(composed):
    from nebius_vpngw.deploy.ha_composite import converge

    api, config, state, _ = composed
    plan = converge(api, config, "enable-standby-auto-healing", dry_run=True, approve=None)
    state["crash"] = True
    with pytest.raises(RuntimeError, match="interrupted"):
        converge(
            api, config, "enable-standby-auto-healing", dry_run=False, approve=plan.approval.digest
        )
    assert state["applies"] == 1
    assert (
        converge(api, config, "enable-standby-auto-healing", dry_run=False, approve=None)
        == "verified"
    )
    assert state["applies"] == 1


def test_composite_rejects_artifact_plan_drift_before_effect(composed):
    from nebius_vpngw.deploy.ha_composite import converge

    api, config, state, report = composed
    plan = converge(api, config, "enable-standby-auto-healing", dry_run=True, approve=None)
    report.artifact_sha256 = "d" * 64
    with pytest.raises(RuntimeError, match="approval"):
        converge(
            api, config, "enable-standby-auto-healing", dry_run=False, approve=plan.approval.digest
        )
    assert state["applies"] == state["locks"] == 0


def test_prerequisite_admission_is_rechecked_under_lock(composed):
    from contextlib import contextmanager

    from nebius_vpngw.deploy.ha_composite import converge

    api, config, state, _ = composed
    plan = converge(api, config, "enable-standby-auto-healing", dry_run=True, approve=None)

    @contextmanager
    def racing_lock(**_):
        api._inspect_vm_ha_prerequisite_status(config).snapshot.members[0].record["repair"] = {
            "pending": True
        }
        yield

    api.VMHAApplyLock = racing_lock
    with pytest.raises(RuntimeError, match="existing workflow"):
        converge(
            api, config, "enable-standby-auto-healing", dry_run=False, approve=plan.approval.digest
        )
    assert state["applies"] == 0


def test_policy_rebind_requires_exact_approved_predecessor(tmp_path):
    from nebius_vpngw.agent.vm_ha.auto_healing import AutoHealingPolicyStore
    from nebius_vpngw.deploy.ha_composite import policy_preserved

    store = AutoHealingPolicyStore(tmp_path / "policy.json")
    first = store.initialize(
        cluster_id="cluster",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=1,
    ).to_dict()
    second = store.initialize(
        cluster_id="cluster",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="b" * 64,
        updated_at=2,
    ).to_dict()

    def authority(record):
        return [{"node": "node-a", "record": record}]

    assert policy_preserved(authority(first), authority(second))
    changed = {**second, "predecessor_digest": "f" * 64}
    assert not policy_preserved(authority(first), authority(changed))


def test_missing_capability_observation_keeps_real_status_identity_validation():
    from types import SimpleNamespace as NS

    from nebius_vpngw import cli

    instance = NS(
        vm_ha_node=NS(node_id="node", role=NS(value="active")),
        vm_ha_generation=NS(
            generation_id="a" * 64,
            digests=NS(configuration="a" * 64, static_routes="b" * 64, bgp_policy="c" * 64),
        ),
    )
    binding = NS(cluster_id="cluster", shared_allocation_id="allocation", route_runtime_id="routes")
    payload = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "cluster_id": "cluster",
        "node_id": "node",
        "configured_role": "active",
        "apply_locked": False,
        "allocation_id": "allocation",
        "route_runtime_id": "routes",
        "controller_capabilities": [],
        "generation_id": "a" * 64,
        "digests": {"configuration": "a" * 64, "static_routes": "b" * 64, "bgp_policy": "c" * 64},
    }

    def validate(value):
        return cli._validate_vm_ha_agent_status(value, inst_cfg=instance, runtime_binding=binding)

    with pytest.raises(cli._VMHAAgentStatusStale, match="capability"):
        validate(payload)
    token = cli._VM_HA_PREREQUISITE_OBSERVATION.set(True)
    try:
        assert validate(payload) is payload
        with pytest.raises(cli._VMHAAgentStatusPermanent, match="foreign"):
            validate({**payload, "node_id": "another-node"})
        with pytest.raises(cli._VMHAAgentStatusStale, match="capability"):
            validate({**payload, "controller_capabilities": "unknown"})
    finally:
        cli._VM_HA_PREREQUISITE_OBSERVATION.reset(token)


def test_terminal_policy_validation_uses_the_execution_sample(composed):
    from nebius_vpngw.agent.vm_ha.auto_healing import (
        AutoHealingPolicyRecord,
        StandbyAutoHealing,
        policy_decision_digest,
    )
    from nebius_vpngw.deploy.ha_composite import converge

    api, config, state, _ = composed
    plan = converge(api, config, "enable-standby-auto-healing", dry_run=True, approve=None)
    original = api._run_vm_ha_auto_healing_statuses

    def statuses(**kwargs):
        values = deepcopy(original(**kwargs))
        if kwargs["require_capability"]:
            for value in values:
                record = value["record"]
                record["operation_id"] = "f" * 64
                record["decision_digest"] = policy_decision_digest(
                    cluster_id=record["cluster_id"],
                    member_node_ids=(record["node_id"], record["peer_node_id"]),
                    generation_id=record["generation_id"],
                    desired=StandbyAutoHealing.ENABLED,
                    operation_id=record["operation_id"],
                    coordinator_node_id=record["coordinator_node_id"],
                    predecessor_digest=record["predecessor_digest"],
                )
                AutoHealingPolicyRecord.from_mapping(record)
                value["operation_id"] = record["operation_id"]
        return values

    api._run_vm_ha_auto_healing_statuses = statuses
    with pytest.raises(RuntimeError, match="approved policy predecessor"):
        converge(
            api, config, "enable-standby-auto-healing", dry_run=False, approve=plan.approval.digest
        )
    assert state["applies"] == 1


def test_rotation_composite_resumes_same_epoch_after_one_member_commits(composed):
    from types import SimpleNamespace as NS

    from nebius_vpngw.deploy.ha_composite import converge

    api, config, state, _ = composed
    rotation = NS(
        operation_id="d" * 64,
        target_epoch=2,
        members=tuple(
            NS(
                node_id=f"node-{i}",
                compute_id=f"compute-{i}",
                generation_id="a" * 64,
                mtls={
                    "epoch": 1,
                    "certificate_fingerprint": "1" * 64,
                    "operation_id": None,
                    "inhibited": False,
                },
            )
            for i in range(2)
        ),
    )
    api._inspect_vm_ha_mtls_rotation = lambda *_a, **_k: rotation
    executions = []

    def rotate(current, *, prepared_callback):
        assert current.operation_id == "d" * 64 and current.target_epoch == 2
        prepared_callback({member.node_id: "2" * 64 for member in current.members})
        executions.append(1)
        current.members[0].mtls.update(epoch=2, certificate_fingerprint="2" * 64)
        if len(executions) == 1:
            current.members[1].mtls.update(operation_id=current.operation_id, inhibited=True)
            raise RuntimeError("interrupted after first commit")
        current.members[1].mtls.update(
            epoch=2, certificate_fingerprint="2" * 64, operation_id=None, inhibited=False
        )

    api._execute_vm_ha_mtls_rotation = rotate
    plan = converge(api, config, "rotate-mtls", dry_run=True, approve=None)
    with pytest.raises(RuntimeError, match="first commit"):
        converge(api, config, "rotate-mtls", dry_run=False, approve=plan.approval.digest)
    assert converge(api, config, "rotate-mtls", dry_run=False, approve=None) == "verified"
    assert state["applies"] == 1 and len(executions) == 2
