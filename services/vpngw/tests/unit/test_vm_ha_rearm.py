from __future__ import annotations

import fcntl
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from nebius_vpngw.agent.vm_ha.mtls import ManagedMTLSStore
from nebius_vpngw.agent.vm_ha_rearm import (
    APPLY_LOCK_PATH,
    PROMOTION_RECEIPT_PATH,
    REARM_CHECKPOINT_PATH,
    REARM_LOCK_PATH,
    REARM_REQUEST_PATH,
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
)
from nebius_vpngw.schema import VMHARouteTarget, VMHARuntimeBinding

DIGESTS = {
    "configuration": "a" * 64,
    "static_routes": "b" * 64,
    "bgp_policy": "c" * 64,
}


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
        self.invalid_journal = False
        self.fail_finalize = False
        self.finalize_target_state: InstanceCloudState | None = InstanceCloudState.RUNNING

    def observe_instance(self, instance_id: str) -> ComputeObservation:
        return self.instances[instance_id]

    def observe_allocation(self, allocation_id: str) -> AllocationObservation:
        assert allocation_id == "allocation-a"
        return AllocationObservation(allocation_id, AllocationOwner(self.owner, "eth0"))

    def start_instance(self, instance_id: str, operation_id: str) -> None:
        self.starts.append((instance_id, operation_id))
        if self.fail_start:
            raise RuntimeError("start failed")
        self.instances[instance_id] = replace(
            self.instances[instance_id],
            state=InstanceCloudState.RUNNING,
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
            target = next(instance_id for instance_id in self.instances if instance_id != self.owner)
            self.instances[target] = replace(
                self.instances[target],
                state=self.finalize_target_state,
                resource_version=str(int(self.instances[target].resource_version) + 1),
            )


def _write_receipt(path: Path, *, owner: str) -> None:
    former = "node-b" if owner == "node-a" else "node-a"
    intent = "planned-failback" if owner == "node-a" else "planned-failover"
    path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-promotion-receipt-v1",
                "receipt_id": f"receipt-{owner}",
                "intent": intent,
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
        ),
        encoding="utf-8",
    )


def _runtime(
    tmp_path: Path,
    *,
    local_node_id: str,
    target_state: InstanceCloudState = InstanceCloudState.STOPPED,
) -> tuple[RearmRuntime, FakeCloud]:
    binding = _binding()
    local = next(node for node in binding.nodes if node.node_id == local_node_id)
    peer = next(node for node in binding.nodes if node.node_id != local_node_id)
    enabled = tmp_path / "enabled"
    enabled.touch()
    cloud = FakeCloud(owner=local.compute_id, target_state=target_state)
    _write_receipt(tmp_path / PROMOTION_RECEIPT_PATH.name, owner=local.node_id)
    return (
        RearmRuntime(
            binding=binding,
            local=local,
            peer=peer,
            cloud=cloud,
            state_dir=tmp_path,
            enabled_path=enabled,
            clock=lambda: 100.0,
        ),
        cloud,
    )


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
    operation_id = _operation_id("receipt-node-a", "node-b", "20")
    checkpoint = RearmCheckpoint(
        receipt_id="receipt-node-a",
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

    with pytest.raises(RuntimeError, match="start failed"):
        runtime.step()
    cloud.fail_start = False
    status = runtime.step()

    assert status["phase"] == "blocked"
    assert status["reason"] == "explicit-retry-required"
    assert len(cloud.starts) == 1


def test_rearm_explicit_retry_reuses_the_stable_logical_operation(tmp_path: Path) -> None:
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    cloud.fail_start = True
    with pytest.raises(RuntimeError, match="start failed"):
        runtime.step()
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
    operation_id = _operation_id("receipt-node-a", "node-b", "20")
    _write_checkpoint(
        tmp_path / REARM_CHECKPOINT_PATH.name,
        RearmCheckpoint(
            receipt_id="receipt-node-a",
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
    operation_id = _operation_id("receipt-node-a", "node-b", "20")
    checkpoint = RearmCheckpoint(
        receipt_id="receipt-node-a",
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
    assert cloud.starts == [
        ("compute-b", _operation_id("receipt-node-a", "node-b", "20"))
    ]


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

    operation_id = _operation_id("receipt-node-a", "node-b", "20")
    _write_checkpoint(
        tmp_path / REARM_CHECKPOINT_PATH.name,
        RearmCheckpoint(
            receipt_id="receipt-node-a",
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
    with pytest.raises(RuntimeError, match="start failed"):
        runtime.step()
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

    with pytest.raises(RuntimeError, match="start failed"):
        runtime.step()
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
