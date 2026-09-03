from __future__ import annotations

import fcntl
import json
import os
import typing as t
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from nebius_vpngw.agent.main import _advance_standby_restoration
from nebius_vpngw.agent.vm_ha.auto_healing import (
    AutoHealingPolicyPhase,
    AutoHealingPolicyRecord,
    AutoHealingPolicyStore,
    AutoHealingRecoveryPhase,
    AutoHealingRecoveryRecord,
    AutoHealingRecoveryStore,
    StandbyAutoHealing,
    policy_decision_digest,
)
from nebius_vpngw.agent.vm_ha.models import DigestSet, PeerHeartbeat
from nebius_vpngw.agent.vm_ha.mtls import ManagedMTLSStore
from nebius_vpngw.agent.vm_ha.promotion_receipt import promotion_receipt_id_v1
from nebius_vpngw.agent.vm_ha.restoration import (
    PolicyAgreementStore,
    RestorationPhase,
    RestorationSource,
    StandbyRestorationStore,
)
from nebius_vpngw.agent.vm_ha_rearm import (
    APPLY_LOCK_PATH,
    PROMOTION_RECEIPT_PATH,
    REARM_CHECKPOINT_PATH,
    REARM_LOCK_PATH,
    REARM_REQUEST_PATH,
    REARM_STATUS_PATH,
    RearmCheckpoint,
    RearmRuntime,
    _acquire_rearm_lock,
    _operation_id,
    _write_checkpoint,
    request_rearm_retry,
)
from nebius_vpngw.deploy.vm_ha_cloud import (
    AcceptedCloudOperation,
    AllocationObservation,
    AllocationOwner,
    ComputeObservation,
    InstanceCloudState,
    RetryableHACloudError,
)
from nebius_vpngw.schema import VMHARouteTarget, VMHARuntimeBinding

DIGESTS = {
    "configuration": "a" * 64,
    "static_routes": "b" * 64,
    "bgp_policy": "c" * 64,
}
NODE_A_RECEIPT_ID = promotion_receipt_id_v1(
    allocation_id="allocation-a",
    first_operation_id="stop-former-owner",
    generation_id="a" * 64,
    intent="planned-failback",
    owner_node_id="node-a",
    ownership_epoch="10",
    route_operation_id="route-operation",
)
NODE_A_AUTOMATIC_RECEIPT_ID = promotion_receipt_id_v1(
    allocation_id="allocation-a",
    first_operation_id="stop-former-owner",
    generation_id="a" * 64,
    intent="automatic-failover",
    owner_node_id="node-a",
    ownership_epoch="10",
    route_operation_id="route-operation",
)


def _binding() -> VMHARuntimeBinding:
    target = VMHARouteTarget(
        project_id="project-a",
        network_id="network-a",
        workload_subnet_id="subnet-a",
        route_table_id="route-table-a",
    )
    credentials_path = "/etc/nebius-vpngw/vm-ha/nebius-credentials.json"
    return VMHARuntimeBinding(
        cluster_id="cluster-a",
        shared_allocation_id="allocation-a",
        nodes=(
            {
                "node_id": "node-a",
                "role": "active",
                "compute_id": "compute-a",
                "network_interface_name": "eth0",
                "peer_endpoint": "10.0.0.1:9443",
                "nebius_credentials_path": credentials_path,
            },
            {
                "node_id": "node-b",
                "role": "passive",
                "compute_id": "compute-b",
                "network_interface_name": "eth0",
                "peer_endpoint": "10.0.0.2:9443",
                "nebius_credentials_path": credentials_path,
            },
        ),
        route_targets=(target,),
        route_runtime_id=VMHARuntimeBinding.derive_route_runtime_id(
            "cluster-a", "allocation-a", (target,)
        ),
        generation_id="a" * 64,
        configuration_digest=DIGESTS["configuration"],
        static_routes_digest=DIGESTS["static_routes"],
        bgp_policy_digest=DIGESTS["bgp_policy"],
    )


def _compute(
    instance_id: str,
    state: InstanceCloudState,
    *,
    revision: str,
    alias: bool,
) -> ComputeObservation:
    return ComputeObservation(
        instance_id=instance_id,
        state=state,
        resource_version=revision,
        nic_allocations=(("eth0", f"primary-{instance_id}"),),
        nic_alias_allocations=(("eth0", ("allocation-a",) if alias else ()),),
    )


class FakeCloud:
    def __init__(self, *, owner: str, target_state: InstanceCloudState) -> None:
        self.owner = owner
        target = "compute-b" if owner == "compute-a" else "compute-a"
        self.instances = {
            owner: _compute(owner, InstanceCloudState.RUNNING, revision="10", alias=True),
            target: _compute(target, target_state, revision="20", alias=False),
        }
        self.starts: list[tuple[str, str]] = []
        self.accepted: AcceptedCloudOperation | None = None
        self.finalized: list[AcceptedCloudOperation] = []
        self.fail_start = False
        self.start_errors: list[Exception] = []
        self.start_target_state = InstanceCloudState.RUNNING
        self.invalid_journal = False
        self.fail_finalize = False
        self.finalize_target_state: InstanceCloudState | None = InstanceCloudState.RUNNING
        self.on_start: t.Callable[[], None] | None = None

    def observe_instance(self, instance_id: str) -> ComputeObservation:
        return self.instances[instance_id]

    def observe_allocation(self, allocation_id: str) -> AllocationObservation:
        assert allocation_id == "allocation-a"
        return AllocationObservation(allocation_id, AllocationOwner(self.owner, "eth0"))

    def start_instance(self, instance_id: str, operation_id: str) -> None:
        self.starts.append((instance_id, operation_id))
        if self.on_start is not None:
            self.on_start()
        if self.start_errors:
            raise self.start_errors.pop(0)
        if self.fail_start:
            raise RuntimeError("start failed")
        self.instances[instance_id] = replace(
            self.instances[instance_id],
            state=self.start_target_state,
            resource_version=str(int(self.instances[instance_id].resource_version) + 1),
        )

    def accepted_operation(self) -> AcceptedCloudOperation | None:
        if self.invalid_journal:
            raise ValueError("invalid operation journal")
        return self.accepted

    def finalize_accepted_start(self, operation: AcceptedCloudOperation) -> None:
        assert operation == self.accepted
        if self.fail_finalize:
            raise RuntimeError("operation status unavailable")
        self.finalized.append(operation)
        self.accepted = None
        if self.finalize_target_state is not None:
            target = next(
                instance_id for instance_id in self.instances if instance_id != self.owner
            )
            self.instances[target] = replace(
                self.instances[target],
                state=self.finalize_target_state,
                resource_version=str(int(self.instances[target].resource_version) + 1),
            )


def _write_receipt(path: Path, *, owner: str, intent: str | None = None) -> None:
    former = "node-b" if owner == "node-a" else "node-a"
    effective_intent = intent or ("planned-failback" if owner == "node-a" else "planned-failover")
    receipt = {
        "schema": "nebius-vpngw/vm-ha-promotion-receipt-v1",
        "intent": effective_intent,
        "cluster_id": "cluster-a",
        "owner_node_id": owner,
        "former_owner_node_id": former,
        "allocation_id": "allocation-a",
        "ownership_epoch": "10",
        "generation_id": "a" * 64,
        "digests": DIGESTS,
        "route_operation_id": "route-operation",
        "committed_at": 10.0,
        "common_cutover_seconds": 2.0,
    }
    receipt["receipt_id"] = promotion_receipt_id_v1(
        allocation_id="allocation-a",
        first_operation_id="stop-former-owner",
        generation_id="a" * 64,
        intent=effective_intent,
        owner_node_id=owner,
        ownership_epoch="10",
        route_operation_id="route-operation",
    )
    path.write_text(json.dumps(receipt), encoding="utf-8")


def _runtime(
    tmp_path: Path,
    *,
    local_node_id: str,
    target_state: InstanceCloudState = InstanceCloudState.STOPPED,
    clock: t.Callable[[], float] = lambda: 100.0,
    receipt_intent: str | None = None,
) -> tuple[RearmRuntime, FakeCloud]:
    binding = _binding()
    local = next(node for node in binding.nodes if node.node_id == local_node_id)
    peer = next(node for node in binding.nodes if node.node_id != local_node_id)
    enabled = tmp_path / "enabled"
    enabled.touch()
    cloud = FakeCloud(owner=local.compute_id, target_state=target_state)
    _write_receipt(
        tmp_path / PROMOTION_RECEIPT_PATH.name,
        owner=local.node_id,
        intent=receipt_intent,
    )
    return (
        RearmRuntime(
            binding=binding,
            local=local,
            peer=peer,
            cloud=cloud,
            state_dir=tmp_path,
            enabled_path=enabled,
            clock=clock,
            auto_healing_inhibition_provider=lambda: None,
        ),
        cloud,
    )


def _commit_restoration_authority(
    tmp_path: Path,
    *,
    owner_node_id: str,
    source: RestorationSource | None = None,
) -> None:
    peer_node_id = "node-b" if owner_node_id == "node-a" else "node-a"
    policy = AutoHealingPolicyStore(tmp_path).initialize(
        cluster_id="cluster-a",
        node_id=owner_node_id,
        peer_node_id=peer_node_id,
        generation_id="a" * 64,
        updated_at=90.0,
    )
    heartbeat = PeerHeartbeat(
        cluster_id="cluster-a",
        node_id=peer_node_id,
        boot_id=f"boot-{peer_node_id}",
        sequence=1,
        sent_at="1970-01-01T00:01:40Z",
        configured_role="passive" if peer_node_id == "node-b" else "active",
        observed_owner_id=owner_node_id,
        generation_id="a" * 64,
        mtls_epoch=1,
        certificate_fingerprint="d" * 64,
        digests=DigestSet.from_mapping(DIGESTS),
        service_healthy=True,
        route_ready=True,
        promotion_ready=False,
        auto_healing_policy_state="enabled",
        auto_healing_policy_digest=policy.decision_digest,
    )
    certificate = PolicyAgreementStore(tmp_path).capture(
        policy=policy,
        heartbeat=heartbeat,
        captured_at=100.0,
    )
    intent = source or (
        RestorationSource.PLANNED_FAILBACK
        if owner_node_id == "node-a"
        else RestorationSource.PLANNED_FAILOVER
    )
    store = StandbyRestorationStore(tmp_path)
    store.arm(
        source=intent,
        certificate=certificate,
        owner_node_id=owner_node_id,
        former_owner_node_id=peer_node_id,
        allocation_id="allocation-a",
        generation_id="a" * 64,
        digests=DIGESTS,
        request_fingerprint=(None if intent is RestorationSource.AUTOMATIC_FAILOVER else "e" * 64),
        first_operation_id="stop-former-owner",
        updated_at=101.0,
    )
    receipt = json.loads((tmp_path / PROMOTION_RECEIPT_PATH.name).read_text(encoding="utf-8"))
    store.commit(receipt=receipt, updated_at=102.0)


def _refresh_latest_agreement(tmp_path: Path, *, owner_node_id: str) -> None:
    peer_node_id = "node-b" if owner_node_id == "node-a" else "node-a"
    policy = AutoHealingPolicyStore(tmp_path).require_bound(
        cluster_id="cluster-a",
        node_id=owner_node_id,
        peer_node_id=peer_node_id,
        generation_id="a" * 64,
    )
    previous = PolicyAgreementStore(tmp_path).load()
    assert previous is not None
    refreshed = PolicyAgreementStore(tmp_path).capture(
        policy=policy,
        heartbeat=PeerHeartbeat(
            cluster_id="cluster-a",
            node_id=peer_node_id,
            boot_id=f"boot-{peer_node_id}",
            sequence=previous.peer_sequence + 1,
            sent_at="1970-01-01T00:01:41Z",
            configured_role="passive" if peer_node_id == "node-b" else "active",
            observed_owner_id=owner_node_id,
            generation_id="a" * 64,
            mtls_epoch=1,
            certificate_fingerprint="d" * 64,
            digests=DigestSet.from_mapping(DIGESTS),
            service_healthy=True,
            route_ready=True,
            promotion_ready=False,
            auto_healing_policy_state="enabled",
            auto_healing_policy_digest=policy.decision_digest,
        ),
        captured_at=101.0,
    )
    assert refreshed.certificate_id != previous.certificate_id


def _arm_disabled_recovery(tmp_path: Path, *, stopped_revision: str = "20") -> str:
    owner_store = AutoHealingPolicyStore(tmp_path)
    peer_store = AutoHealingPolicyStore(tmp_path / "peer")
    owner_initial = owner_store.initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=1.0,
    )
    peer_initial = peer_store.initialize(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        updated_at=1.0,
    )
    disable_operation = "d" * 64
    owner_prepared = owner_store.prepare(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=disable_operation,
        coordinator_node_id="node-a",
        predecessor_digest=owner_initial.decision_digest,
        peer=peer_initial,
        updated_at=2.0,
    )
    peer_store.prepare(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=disable_operation,
        coordinator_node_id="node-a",
        predecessor_digest=peer_initial.decision_digest,
        peer=owner_prepared,
        updated_at=2.0,
    )
    peer_committed = peer_store.commit(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=disable_operation,
        coordinator_node_id="node-a",
        predecessor_digest=peer_initial.decision_digest,
        peer=owner_prepared,
        updated_at=3.0,
    )
    owner_committed = owner_store.commit(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=disable_operation,
        coordinator_node_id="node-a",
        predecessor_digest=owner_initial.decision_digest,
        peer=peer_committed,
        updated_at=3.0,
    )
    enable_operation = "e" * 64
    AutoHealingRecoveryStore(tmp_path).arm(
        AutoHealingRecoveryRecord(
            cluster_id="cluster-a",
            node_id="node-a",
            target_node_id="node-b",
            generation_id="a" * 64,
            desired=StandbyAutoHealing.ENABLED,
            operation_id=enable_operation,
            approval_digest="f" * 64,
            policy_digest=owner_committed.decision_digest,
            predecessor_digest=owner_committed.decision_digest,
            promotion_receipt_id=NODE_A_RECEIPT_ID,
            allocation_id="allocation-a",
            ownership_epoch="10",
            stopped_revision=stopped_revision,
            phase=AutoHealingRecoveryPhase.ARMED,
            rearm_operation_id=None,
            updated_at=4.0,
        )
    )
    return enable_operation


@pytest.mark.parametrize("local_node_id", ["node-a", "node-b"])
def test_rearm_starts_exact_non_owner_once_in_both_directions(
    tmp_path: Path, local_node_id: str
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id=local_node_id)

    first = runtime.step()
    second = runtime.step()

    assert first["phase"] == "running"
    assert second["phase"] == "running"
    assert len(cloud.starts) == 1
    checkpoint = json.loads((tmp_path / REARM_CHECKPOINT_PATH.name).read_text())
    assert checkpoint["phase"] == "running"


@pytest.mark.parametrize("local_node_id", ["node-a", "node-b"])
def test_committed_restoration_bypasses_expired_peer_heartbeat_once(
    tmp_path: Path,
    local_node_id: str,
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id=local_node_id)
    _commit_restoration_authority(tmp_path, owner_node_id=local_node_id)
    runtime.auto_healing_inhibition_provider = lambda: (
        "standby-auto-healing-peer-policy-unavailable"
    )

    status = runtime.step()

    assert status["phase"] == "running"
    assert len(cloud.starts) == 1
    authority = StandbyRestorationStore(tmp_path).load()
    assert authority is not None
    assert authority.phase is RestorationPhase.AWAITING_STANDBY
    assert authority.attempt_count == 1


@pytest.mark.parametrize("local_node_id", ["node-a", "node-b"])
def test_committed_restoration_adopts_an_already_running_target_without_starting_it(
    tmp_path: Path,
    local_node_id: str,
) -> None:
    runtime, cloud = _runtime(
        tmp_path,
        local_node_id=local_node_id,
        target_state=InstanceCloudState.RUNNING,
    )
    _commit_restoration_authority(tmp_path, owner_node_id=local_node_id)

    status = runtime.step()

    assert status["phase"] == "running"
    assert cloud.starts == []
    authority = StandbyRestorationStore(tmp_path).load()
    assert authority is not None
    assert authority.phase is RestorationPhase.AWAITING_STANDBY
    assert authority.stopped_revision == "20"
    assert authority.rearm_operation_id == _operation_id(
        authority.promotion_receipt_id or "",
        runtime.peer.node_id,
        "20",
    )
    assert authority.attempt_count == 0


@pytest.mark.parametrize(
    ("target_node_id", "operation_id"),
    (
        (
            "foreign-node",
            _operation_id(NODE_A_RECEIPT_ID, "foreign-node", "999"),
        ),
        ("node-b", "attacker-controlled-operation"),
    ),
)
def test_running_target_rejects_a_same_receipt_checkpoint_with_foreign_identity(
    tmp_path: Path,
    target_node_id: str,
    operation_id: str,
) -> None:
    runtime, cloud = _runtime(
        tmp_path,
        local_node_id="node-a",
        target_state=InstanceCloudState.RUNNING,
    )
    _commit_restoration_authority(tmp_path, owner_node_id="node-a")
    _write_checkpoint(
        tmp_path / REARM_CHECKPOINT_PATH.name,
        RearmCheckpoint(
            receipt_id=NODE_A_RECEIPT_ID,
            target_node_id=target_node_id,
            stopped_revision="999",
            operation_id=operation_id,
            phase="start-requested",
            attempted_at=90.0,
        ),
    )

    status = runtime.step()

    assert status["phase"] == "blocked"
    assert status["reason"] == "rearm-checkpoint-invalid"
    assert cloud.starts == []
    authority = StandbyRestorationStore(tmp_path).load()
    assert authority is not None
    assert authority.phase is RestorationPhase.COMMITTED


@pytest.mark.parametrize("local_node_id", ["node-a", "node-b"])
def test_planned_restoration_completes_after_latest_agreement_refresh(
    tmp_path: Path,
    local_node_id: str,
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id=local_node_id)
    _commit_restoration_authority(tmp_path, owner_node_id=local_node_id)
    authorization = StandbyRestorationStore(tmp_path).load()
    assert authorization is not None

    _refresh_latest_agreement(tmp_path, owner_node_id=local_node_id)
    latest = PolicyAgreementStore(tmp_path).load()
    assert latest is not None
    assert latest.certificate_id != authorization.certificate_id

    status = runtime.step()

    assert status["phase"] == "running"
    assert status["reason"] is None
    assert len(cloud.starts) == 1
    restored = StandbyRestorationStore(tmp_path).load()
    assert restored is not None
    assert restored.phase is RestorationPhase.AWAITING_STANDBY

    _advance_standby_restoration(
        status={
            "redundancy_ready": True,
            "observed_owner_node_id": local_node_id,
            "allocation_id": "allocation-a",
            "generation_id": "a" * 64,
            "digests": DIGESTS,
        },
        state_dir=tmp_path,
        clock=lambda: 104.0,
    )

    peer_compute_id = "compute-b" if cloud.owner == "compute-a" else "compute-a"
    assert cloud.instances[peer_compute_id].state is InstanceCloudState.RUNNING
    assert cloud.observe_allocation("allocation-a").owner == AllocationOwner(
        cloud.owner,
        "eth0",
    )
    completed = StandbyRestorationStore(tmp_path).load()
    assert completed is not None
    assert completed.phase is RestorationPhase.COMPLETED


def test_restoration_policy_failure_projects_closed_reason_code(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    _commit_restoration_authority(tmp_path, owner_node_id="node-a")
    AutoHealingPolicyStore(tmp_path).path.unlink()

    status = runtime.step()

    assert status["phase"] == "inhibited"
    assert status["reason"] == "standby-restoration-policy-unavailable"
    assert "authority" not in str(status["reason"])
    assert cloud.starts == []


def test_committed_restoration_retries_five_times_with_one_operation_id(
    tmp_path: Path,
) -> None:
    now = [100.0]
    runtime, cloud = _runtime(
        tmp_path,
        local_node_id="node-a",
        clock=lambda: now[0],
    )
    _commit_restoration_authority(tmp_path, owner_node_id="node-a")
    cloud.start_errors = [RetryableHACloudError("unavailable") for _ in range(5)]

    for delay in (5.0, 15.0, 30.0, 60.0):
        status = runtime.step()
        assert status["phase"] == "starting"
        assert status["reason"] == "compute-start-retry-scheduled"
        now[0] += delay

    status = runtime.step()

    assert status["phase"] == "blocked"
    assert status["reason"] == "automatic-retry-exhausted"
    assert len(cloud.starts) == 5
    assert len({operation_id for _instance, operation_id in cloud.starts}) == 1
    authority = StandbyRestorationStore(tmp_path).load()
    assert authority is not None
    assert authority.phase is RestorationPhase.BLOCKED
    assert authority.attempt_count == 5


def test_committed_restoration_untyped_start_failure_blocks_authority(
    tmp_path: Path,
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    _commit_restoration_authority(tmp_path, owner_node_id="node-a")
    cloud.fail_start = True

    status = runtime.step()

    assert status["phase"] == "blocked"
    assert status["reason"] == "compute-start-failed"
    authority = StandbyRestorationStore(tmp_path).load()
    assert authority is not None
    assert authority.phase is RestorationPhase.BLOCKED
    assert authority.blocked_reason == "compute-start-failed"
    assert authority.attempt_count == 1


def test_explicit_vm_ha_retry_restarts_exact_blocked_restoration(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    _commit_restoration_authority(tmp_path, owner_node_id="node-a")
    operation_id = _operation_id(NODE_A_RECEIPT_ID, "node-b", "20")
    store = StandbyRestorationStore(tmp_path)
    store.accept_start(
        receipt_id=NODE_A_RECEIPT_ID,
        stopped_revision="20",
        rearm_operation_id=operation_id,
        updated_at=103.0,
    )
    store.block(
        receipt_id=NODE_A_RECEIPT_ID,
        reason="automatic-retry-exhausted",
        updated_at=104.0,
    )
    config_path = tmp_path / "config-resolved.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "vm_ha": {
                    "enabled": True,
                    "node": {"node_id": "node-a"},
                    "runtime_binding": _binding().model_dump(mode="json"),
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    request_rearm_retry(config_path=config_path, state_dir=tmp_path)
    status = runtime.step()

    assert status["phase"] == "running"
    assert cloud.starts == [("compute-b", operation_id)]
    restarted = store.load()
    assert restarted is not None
    assert restarted.source is RestorationSource.OPERATOR_RESTORATION
    assert restarted.phase is RestorationPhase.AWAITING_STANDBY


def test_operator_retry_rejects_automatic_prepared_disable_authority(
    tmp_path: Path,
) -> None:
    _runtime(
        tmp_path,
        local_node_id="node-a",
        receipt_intent=RestorationSource.AUTOMATIC_FAILOVER.value,
    )
    _commit_restoration_authority(
        tmp_path,
        owner_node_id="node-a",
        source=RestorationSource.AUTOMATIC_FAILOVER,
    )
    store = StandbyRestorationStore(tmp_path)
    operation_id = _operation_id(NODE_A_AUTOMATIC_RECEIPT_ID, "node-b", "20")
    store.accept_start(
        receipt_id=NODE_A_AUTOMATIC_RECEIPT_ID,
        stopped_revision="20",
        rearm_operation_id=operation_id,
        updated_at=103.0,
    )
    store.block(
        receipt_id=NODE_A_AUTOMATIC_RECEIPT_ID,
        reason="automatic-retry-exhausted",
        updated_at=104.0,
    )
    policy_store = AutoHealingPolicyStore(tmp_path)
    enabled = policy_store.load()
    assert enabled is not None
    disable_operation_id = "f" * 64
    prepared = AutoHealingPolicyRecord(
        cluster_id=enabled.cluster_id,
        node_id=enabled.node_id,
        peer_node_id=enabled.peer_node_id,
        generation_id=enabled.generation_id,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=disable_operation_id,
        coordinator_node_id=enabled.coordinator_node_id,
        predecessor_digest=enabled.decision_digest,
        phase=AutoHealingPolicyPhase.PREPARED,
        decision_digest=policy_decision_digest(
            cluster_id=enabled.cluster_id,
            member_node_ids=(enabled.node_id, enabled.peer_node_id),
            generation_id=enabled.generation_id,
            desired=StandbyAutoHealing.DISABLED,
            operation_id=disable_operation_id,
            coordinator_node_id=enabled.coordinator_node_id,
            predecessor_digest=enabled.decision_digest,
        ),
        peer_ack_digest=None,
        updated_at=105.0,
    )
    policy_store.path.write_text(
        json.dumps(prepared.to_dict(), sort_keys=True),
        encoding="utf-8",
    )
    config_path = tmp_path / "config-resolved.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "vm_ha": {
                    "enabled": True,
                    "node": {"node_id": "node-a"},
                    "runtime_binding": _binding().model_dump(mode="json"),
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError,
        match="blocked standby restoration requires committed enabled policy",
    ):
        request_rearm_retry(config_path=config_path, state_dir=tmp_path)

    unchanged = store.load()
    assert unchanged is not None
    assert unchanged.source is RestorationSource.AUTOMATIC_FAILOVER
    assert unchanged.phase is RestorationPhase.BLOCKED


def test_rearm_consumes_owner_local_policy_recovery_once(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    recovery_operation = _arm_disabled_recovery(tmp_path)

    status = runtime.step()

    assert status["phase"] == "running"
    assert len(cloud.starts) == 1
    recovery = AutoHealingRecoveryStore(tmp_path).load()
    assert recovery is not None
    assert recovery.operation_id == recovery_operation
    assert recovery.phase is AutoHealingRecoveryPhase.COMPLETED


def test_rearm_rejects_recovery_for_a_different_stopped_revision(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    _arm_disabled_recovery(tmp_path, stopped_revision="19")

    status = runtime.step()

    assert status["phase"] == "inhibited"
    assert status["reason"] == ("standby-auto-healing-recovery-authority-stale-or-foreign")
    assert cloud.starts == []


def test_rearm_projects_closed_reason_for_unavailable_recovery_policy(
    tmp_path: Path,
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    _arm_disabled_recovery(tmp_path)
    AutoHealingPolicyStore(tmp_path).path.unlink()

    status = runtime.step()

    assert status["phase"] == "inhibited"
    assert status["reason"] == "standby-auto-healing-recovery-policy-unavailable"
    assert cloud.starts == []


def test_rearm_projects_closed_reason_for_changed_recovery_policy(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    _arm_disabled_recovery(tmp_path)
    policy_store = AutoHealingPolicyStore(tmp_path)
    policy_store.path.unlink()
    policy_store.initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=5.0,
    )

    status = runtime.step()

    assert status["phase"] == "inhibited"
    assert status["reason"] == "standby-auto-healing-recovery-policy-changed"
    assert cloud.starts == []


def test_rearm_projects_closed_reason_for_invalid_recovery_record(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    AutoHealingRecoveryStore(tmp_path).path.write_text("{}", encoding="utf-8")

    status = runtime.step()

    assert status["phase"] == "inhibited"
    assert status["reason"] == "standby-auto-healing-recovery-invalid"
    assert cloud.starts == []


def test_rearm_publishes_starting_checkpoint_and_status_before_cloud_start(
    tmp_path: Path,
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    observed: dict[str, object] = {}

    def observe_in_flight_state() -> None:
        checkpoint = json.loads((tmp_path / REARM_CHECKPOINT_PATH.name).read_text(encoding="utf-8"))
        status = json.loads((tmp_path / REARM_STATUS_PATH.name).read_text(encoding="utf-8"))
        observed.update(checkpoint=checkpoint, status=status)

    cloud.on_start = observe_in_flight_state

    result = runtime.step()

    assert result["phase"] == "running"
    assert t.cast(dict[str, object], observed["checkpoint"])["phase"] == "starting"
    assert t.cast(dict[str, object], observed["status"])["phase"] == "starting"


def test_rearm_adopts_running_alias_free_target_without_mutation(tmp_path: Path) -> None:
    runtime, cloud = _runtime(
        tmp_path, local_node_id="node-a", target_state=InstanceCloudState.RUNNING
    )

    status = runtime.step()

    assert status["phase"] == "running"
    assert cloud.starts == []


def test_rearm_never_infers_promotion_from_topology(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    (tmp_path / PROMOTION_RECEIPT_PATH.name).unlink()

    status = runtime.step()

    assert status == {
        "schema": "nebius-vpngw/vm-ha-rearm-status-v1",
        "phase": "idle",
        "reason": "promotion-receipt-unavailable",
        "promotion_receipt_id": None,
        "operation_id": None,
        "completed_at": None,
        "updated_at": 100.0,
    }
    assert cloud.starts == []


def test_rearm_blocks_owner_or_alias_drift_without_start(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    cloud.owner = "compute-b"

    status = runtime.step()

    assert status["phase"] == "blocked"
    assert status["reason"] == "ownership-or-alias-drift"
    assert cloud.starts == []


def test_rearm_blocks_a_stale_receipt_from_an_earlier_ownership_epoch(
    tmp_path: Path,
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    receipt_path = tmp_path / PROMOTION_RECEIPT_PATH.name
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["ownership_epoch"] = "9"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    status = runtime.step()

    assert status["phase"] == "blocked"
    assert status["reason"] == "ownership-or-alias-drift"
    assert cloud.starts == []


def test_rearm_apply_lock_inhibits_before_cloud_observation(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    (tmp_path / APPLY_LOCK_PATH.name).write_text("locked", encoding="utf-8")

    status = runtime.step()

    assert status["phase"] == "inhibited"
    assert status["reason"] == "apply-lock-held"
    assert cloud.starts == []


def test_rearm_disabled_marker_inhibits_before_cloud_observation(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    runtime.enabled_path.unlink()

    status = runtime.step()

    assert status["phase"] == "inhibited"
    assert status["reason"] == "vm-ha-removal-or-disable-active"
    assert cloud.starts == []


def test_rearm_serializes_the_only_start_writer(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    descriptor = os.open(tmp_path / REARM_LOCK_PATH.name, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        status = runtime.step()
    finally:
        os.close(descriptor)

    assert status["phase"] == "inhibited"
    assert status["reason"] == "rearm-writer-busy"
    assert cloud.starts == []


def test_rearm_lock_inode_survives_sibling_state_cleanup(tmp_path: Path) -> None:
    first = _acquire_rearm_lock(tmp_path / REARM_LOCK_PATH.name, timeout_seconds=0)
    assert first is not None
    lock_path = tmp_path / REARM_LOCK_PATH.name
    before = lock_path.stat().st_ino
    (tmp_path / "status.json").write_text("{}", encoding="utf-8")
    (tmp_path / "status.json").unlink()

    assert lock_path.stat().st_ino == before
    assert _acquire_rearm_lock(lock_path, timeout_seconds=0) is None
    os.close(first)

    second = _acquire_rearm_lock(lock_path, timeout_seconds=0)
    assert second is not None
    try:
        assert lock_path.stat().st_ino == before
    finally:
        os.close(second)


def test_rearm_observes_foreign_transitional_start_without_mutation(tmp_path: Path) -> None:
    runtime, cloud = _runtime(
        tmp_path, local_node_id="node-a", target_state=InstanceCloudState.TRANSITIONAL
    )

    status = runtime.step()

    assert status["phase"] == "starting"
    assert cloud.starts == []


def test_rearm_observes_its_submitted_transitional_start_without_resubmitting(
    tmp_path: Path,
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    _commit_restoration_authority(tmp_path, owner_node_id="node-a")
    cloud.start_target_state = InstanceCloudState.TRANSITIONAL

    first = runtime.step()
    second = runtime.step()

    assert first["phase"] == "starting"
    assert second["phase"] == "starting"
    assert len(cloud.starts) == 1
    authority = StandbyRestorationStore(tmp_path).load()
    assert authority is not None
    assert authority.attempt_count == 1


@pytest.mark.parametrize(
    "target_state",
    [
        InstanceCloudState.ERROR,
        InstanceCloudState.STOPPING,
        InstanceCloudState.UNKNOWN,
    ],
)
def test_rearm_blocks_ambiguous_or_unsafe_target_states(
    tmp_path: Path, target_state: InstanceCloudState
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a", target_state=target_state)

    status = runtime.step()

    assert status["phase"] == "blocked"
    assert status["reason"] == f"target-compute-{target_state.value}"
    assert cloud.starts == []


def test_rearm_finalizes_only_its_accepted_transitional_operation(tmp_path: Path) -> None:
    runtime, cloud = _runtime(
        tmp_path, local_node_id="node-a", target_state=InstanceCloudState.TRANSITIONAL
    )
    operation_id = _operation_id(NODE_A_RECEIPT_ID, "node-b", "20")
    checkpoint = RearmCheckpoint(
        receipt_id=NODE_A_RECEIPT_ID,
        target_node_id="node-b",
        stopped_revision="20",
        operation_id=operation_id,
        phase="starting",
        attempted_at=90.0,
    )
    _write_checkpoint(tmp_path / REARM_CHECKPOINT_PATH.name, checkpoint)
    cloud.accepted = AcceptedCloudOperation(
        action_operation_id=operation_id,
        kind="start-instance",
        cloud_operation_id="cloud-operation-a",
    )

    status = runtime.step()

    assert status["phase"] == "running"
    assert cloud.starts == []
    assert cloud.finalized == [
        AcceptedCloudOperation(
            action_operation_id=operation_id,
            kind="start-instance",
            cloud_operation_id="cloud-operation-a",
        )
    ]


def test_rearm_start_failure_is_checkpointed_and_requires_retry(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    cloud.fail_start = True

    failed = runtime.step()
    cloud.fail_start = False
    status = runtime.step()

    assert failed["phase"] == "blocked"
    assert failed["reason"] == "compute-start-failed"
    assert status["phase"] == "blocked"
    assert status["reason"] == "explicit-retry-required"
    assert len(cloud.starts) == 1


def test_rearm_explicit_retry_reuses_the_stable_logical_operation(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    cloud.fail_start = True
    assert runtime.step()["reason"] == "compute-start-failed"
    checkpoint = json.loads((tmp_path / REARM_CHECKPOINT_PATH.name).read_text())
    operation_id = checkpoint["operation_id"]
    config_path = tmp_path / "config-resolved.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "vm_ha": {
                    "enabled": True,
                    "node": {"node_id": "node-a"},
                    "runtime_binding": _binding().model_dump(mode="json"),
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    request = request_rearm_retry(config_path=config_path, state_dir=tmp_path)
    cloud.fail_start = False
    status = runtime.step()

    assert request["target_node_id"] == "node-b"
    assert status["phase"] == "running"
    assert [item[1] for item in cloud.starts] == [operation_id, operation_id]
    assert not (tmp_path / REARM_REQUEST_PATH.name).exists()


def test_rearm_corrupt_checkpoint_fails_closed(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    (tmp_path / REARM_CHECKPOINT_PATH.name).write_text("{}", encoding="utf-8")

    status = runtime.step()
    assert status["phase"] == "blocked"
    assert status["reason"] == "rearm-checkpoint-invalid"
    assert cloud.starts == []


def test_rearm_corrupt_retry_request_fails_closed(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    (tmp_path / REARM_REQUEST_PATH.name).write_text("{}", encoding="utf-8")

    status = runtime.step()

    assert status["phase"] == "blocked"
    assert status["reason"] == "rearm-request-invalid"
    assert cloud.starts == []


def test_rearm_corrupt_operation_journal_fails_closed(tmp_path: Path) -> None:
    runtime, cloud = _runtime(
        tmp_path, local_node_id="node-a", target_state=InstanceCloudState.TRANSITIONAL
    )
    operation_id = _operation_id(NODE_A_RECEIPT_ID, "node-b", "20")
    _write_checkpoint(
        tmp_path / REARM_CHECKPOINT_PATH.name,
        RearmCheckpoint(
            receipt_id=NODE_A_RECEIPT_ID,
            target_node_id="node-b",
            stopped_revision="20",
            operation_id=operation_id,
            phase="starting",
            attempted_at=90.0,
        ),
    )
    cloud.invalid_journal = True

    status = runtime.step()

    assert status["phase"] == "blocked"
    assert status["reason"] == "cloud-operation-journal-invalid"
    assert cloud.starts == []


def test_rearm_corrupt_promotion_receipt_fails_closed(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    (tmp_path / PROMOTION_RECEIPT_PATH.name).write_text("{}", encoding="utf-8")

    status = runtime.step()
    assert status["phase"] == "blocked"
    assert status["reason"] == "promotion-receipt-invalid"
    assert cloud.starts == []


@pytest.mark.parametrize("checkpoint_phase", ["starting", "running"])
def test_rearm_clears_successful_accepted_journal_before_adopting_running(
    tmp_path: Path,
    checkpoint_phase: str,
) -> None:
    runtime, cloud = _runtime(
        tmp_path,
        local_node_id="node-a",
        target_state=InstanceCloudState.RUNNING,
    )
    operation_id = _operation_id(NODE_A_RECEIPT_ID, "node-b", "20")
    checkpoint = RearmCheckpoint(
        receipt_id=NODE_A_RECEIPT_ID,
        target_node_id="node-b",
        stopped_revision="20",
        operation_id=operation_id,
        phase=checkpoint_phase,
        attempted_at=90.0,
    )
    _write_checkpoint(tmp_path / REARM_CHECKPOINT_PATH.name, checkpoint)
    cloud.accepted = AcceptedCloudOperation(
        action_operation_id=operation_id,
        kind="start-instance",
        cloud_operation_id="cloud-operation-a",
    )

    status = runtime.step()

    assert status["phase"] == "running"
    assert cloud.starts == []
    assert cloud.accepted is None
    assert cloud.finalized


def test_rearm_clears_bound_old_receipt_journal_before_next_promotion_start(
    tmp_path: Path,
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    old_operation_id = _operation_id("receipt-old", "node-b", "19")
    _write_checkpoint(
        tmp_path / REARM_CHECKPOINT_PATH.name,
        RearmCheckpoint(
            receipt_id="receipt-old",
            target_node_id="node-b",
            stopped_revision="19",
            operation_id=old_operation_id,
            phase="starting",
            attempted_at=80.0,
        ),
    )
    cloud.accepted = AcceptedCloudOperation(
        action_operation_id=old_operation_id,
        kind="start-instance",
        cloud_operation_id="cloud-operation-old",
    )
    cloud.finalize_target_state = None

    status = runtime.step()

    assert status["phase"] == "running"
    assert cloud.finalized[0].action_operation_id == old_operation_id
    assert cloud.starts == [("compute-b", _operation_id(NODE_A_RECEIPT_ID, "node-b", "20"))]


def test_rearm_blocks_unbound_or_unavailable_accepted_operation(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    cloud.accepted = AcceptedCloudOperation(
        action_operation_id="foreign-operation",
        kind="start-instance",
        cloud_operation_id="cloud-operation-a",
    )

    status = runtime.step()

    assert status["phase"] == "blocked"
    assert status["reason"] == "cloud-operation-journal-unbound"
    assert cloud.starts == []

    operation_id = _operation_id(NODE_A_RECEIPT_ID, "node-b", "20")
    _write_checkpoint(
        tmp_path / REARM_CHECKPOINT_PATH.name,
        RearmCheckpoint(
            receipt_id=NODE_A_RECEIPT_ID,
            target_node_id="node-b",
            stopped_revision="20",
            operation_id=operation_id,
            phase="starting",
            attempted_at=90.0,
        ),
    )
    cloud.accepted = AcceptedCloudOperation(
        action_operation_id=operation_id,
        kind="start-instance",
        cloud_operation_id="cloud-operation-a",
    )
    cloud.fail_finalize = True
    status = runtime.step()
    assert status["reason"] == "cloud-operation-finalization-failed"
    assert cloud.accepted is not None
    assert cloud.starts == []


def test_explicit_retry_is_consumed_before_failure_and_never_replays_on_restart(
    tmp_path: Path,
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    cloud.fail_start = True
    assert runtime.step()["reason"] == "compute-start-failed"
    config_path = tmp_path / "config-resolved.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "vm_ha": {
                    "enabled": True,
                    "node": {"node_id": "node-a"},
                    "runtime_binding": _binding().model_dump(mode="json"),
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    first_request = request_rearm_retry(config_path=config_path, state_dir=tmp_path)
    assert request_rearm_retry(config_path=config_path, state_dir=tmp_path) == first_request

    assert runtime.step()["reason"] == "compute-start-failed"
    assert not (tmp_path / REARM_REQUEST_PATH.name).exists()
    status = runtime.step()
    assert status["reason"] == "explicit-retry-required"
    assert len(cloud.starts) == 2

    second_request = request_rearm_retry(config_path=config_path, state_dir=tmp_path)
    assert second_request["request_id"] != first_request["request_id"]
    cloud.fail_start = False
    assert runtime.step()["phase"] == "running"
    assert len(cloud.starts) == 3


def test_rearm_rechecks_inhibition_immediately_before_start(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    original = runtime._inhibition_reason
    calls = 0

    def inhibition() -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            (tmp_path / APPLY_LOCK_PATH.name).write_text("locked", encoding="utf-8")
        return original()

    runtime._inhibition_reason = inhibition  # type: ignore[method-assign]

    status = runtime.step()

    assert status["phase"] == "inhibited"
    assert status["reason"] == "apply-lock-held"
    assert cloud.starts == []


def test_rearm_rechecks_auto_healing_policy_immediately_before_start(
    tmp_path: Path,
) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    calls = 0

    def policy_inhibition() -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            return "standby-auto-healing-policy-disabled"
        return None

    runtime.auto_healing_inhibition_provider = policy_inhibition

    status = runtime.step()

    assert status["phase"] == "inhibited"
    assert status["reason"] == "standby-auto-healing-policy-disabled"
    assert cloud.starts == []


def test_rearm_finalizes_an_accepted_start_after_policy_is_disabled(
    tmp_path: Path,
) -> None:
    runtime, cloud = _runtime(
        tmp_path,
        local_node_id="node-a",
        target_state=InstanceCloudState.TRANSITIONAL,
    )
    operation_id = _operation_id(NODE_A_RECEIPT_ID, "node-b", "20")
    _write_checkpoint(
        tmp_path / REARM_CHECKPOINT_PATH.name,
        RearmCheckpoint(
            receipt_id=NODE_A_RECEIPT_ID,
            target_node_id="node-b",
            stopped_revision="20",
            operation_id=operation_id,
            phase="starting",
            attempted_at=90.0,
        ),
    )
    cloud.accepted = AcceptedCloudOperation(
        action_operation_id=operation_id,
        kind="start-instance",
        cloud_operation_id="cloud-operation-a",
    )
    runtime.auto_healing_inhibition_provider = lambda: "standby-auto-healing-policy-disabled"

    status = runtime.step()

    assert status["phase"] == "running"
    assert cloud.starts == []
    assert cloud.accepted is None
    assert len(cloud.finalized) == 1


def test_rearm_and_explicit_retry_are_blocked_by_mtls_rotation(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    operation_id = "d" * 64
    ManagedMTLSStore(tmp_path / "mtls").install_inhibition(
        operation_id=operation_id,
        cluster_id="cluster-a",
        node_id="node-a",
        generation_id="a" * 64,
    )

    status = runtime.step()

    assert status["phase"] == "inhibited"
    assert status["reason"] == "mtls-rotation-active"
    assert cloud.starts == []

    config_path = tmp_path / "config-resolved.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "vm_ha": {
                    "enabled": True,
                    "node": {"node_id": "node-a"},
                    "runtime_binding": _binding().model_dump(mode="json"),
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="inhibited by mTLS rotation"):
        request_rearm_retry(config_path=config_path, state_dir=tmp_path)
