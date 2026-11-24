from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from tests.unit.test_vm_ha_controller import _snapshot
from tests.unit.test_vm_ha_rearm import DIGESTS, _commit_restoration_authority, _runtime

from nebius_vpngw import restoration_upgrade
from nebius_vpngw.agent.vm_ha.models import DigestSet, canonical_json
from nebius_vpngw.agent.vm_ha.restoration import StandbyRestorationError, StandbyRestorationStore
from nebius_vpngw.agent.vm_ha.restoration_revalidation import publish, reconciliation_authority
from nebius_vpngw.agent.vm_ha.retry_window import RetryWindow
from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
from nebius_vpngw.agent.vm_ha_controller import (
    CloudObservation,
    ComputeState,
    ConfiguredRole,
    ControllerCheckpoint,
    DataPlaneMode,
    HAState,
    RouteReconciliationContext,
)


def setup_revalidation(tmp_path: Path, owner: str = "node-a"):
    runtime, cloud = _runtime(tmp_path, local_node_id=owner)
    runtime.boot_id = lambda: "boot-new"
    _commit_restoration_authority(tmp_path, owner_node_id=owner)
    store = StandbyRestorationStore(tmp_path)
    record = store.load()
    assert record is not None
    store._write(replace(record, ownership_incarnation=2, baseline_epoch="10"))
    cloud.instances[runtime.local.compute_id] = replace(
        cloud.instances[runtime.local.compute_id], resource_version="12"
    )
    route = RouteReconciliationContext(
        owner,
        "allocation-a",
        "12",
        "a" * 64,
        DigestSet.from_mapping(DIGESTS),
        runtime.binding.route_runtime_id,
        ownership_incarnation=2,
        operation_id="new-route",
    )
    snapshot = replace(
        _snapshot(),
        cluster_id="cluster-a",
        local_node_id=owner,
        peer_node_id=runtime.peer.node_id,
        configured_role=ConfiguredRole(runtime.local.role.value),
        boot_id="boot-new",
        guard_boot_id="boot-new",
        data_plane_mode=DataPlaneMode.ACTIVE,
        local_generation_id="a" * 64,
        local_digests=DigestSet.from_mapping(DIGESTS),
        cloud=CloudObservation(
            authoritative=True,
            allocation_id="allocation-a",
            observed_owner_node_id=owner,
            former_owner_node_id=runtime.peer.node_id,
            former_owner_compute_state=ComputeState.STOPPED,
            former_attachment_absent=True,
            candidate_attachment_exact=True,
            ownership_re_read_exact=True,
            ownership_epoch="12",
        ),
        routes_reconciled_context=route,
        route_runtime_id=runtime.binding.route_runtime_id,
    )
    checkpoint = ControllerCheckpoint(
        sequence=10,
        state=HAState.ACTIVE,
        ownership_incarnation=2,
        established_ownership_context=snapshot.ownership_context,
    )
    (tmp_path / "controller-checkpoint.json").write_text(
        json.dumps(controller_checkpoint_to_dict(checkpoint))
    )
    (tmp_path / "guard.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "guard_boot_id": "boot-new",
                "data_plane_mode": "active",
                "generation_id": "a" * 64,
                "allocation_id": "allocation-a",
                "node_id": owner,
                "digests": DIGESTS,
            }
        )
    )
    directory = tmp_path / "generation-store"
    directory.mkdir(exist_ok=True)
    (directory / "route-reconciliation-receipt.json").write_text(json.dumps({"route": "new-route"}))
    return runtime, cloud, store, snapshot, checkpoint


@pytest.mark.parametrize("owner", ["node-a", "node-b"])
def test_revision_revalidation_restores_peer_and_preserves_promotion(tmp_path, owner):
    runtime, cloud, store, snapshot, _ = setup_revalidation(tmp_path, owner)
    original = store.receipt_path.read_bytes()
    assert runtime.step()["reason"] == "ownership-or-alias-drift"
    assert cloud.starts == []
    assert publish(tmp_path, lambda: snapshot)
    assert runtime.step()["phase"] == "running"
    assert len(cloud.starts) == 1
    assert store.receipt_path.read_bytes() == original


@pytest.mark.parametrize("changed", ["guard", "route", "incarnation", "missing-baseline"])
def test_changed_authority_never_starts(tmp_path, changed):
    runtime, cloud, store, snapshot, checkpoint = setup_revalidation(tmp_path)
    assert publish(tmp_path, lambda: snapshot)
    if changed == "guard":
        path = tmp_path / "guard.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "data_plane_mode": "passive"}))
    elif changed == "route":
        (tmp_path / "generation-store/route-reconciliation-receipt.json").write_text("{}")
    elif changed == "incarnation":
        (tmp_path / "controller-checkpoint.json").write_text(
            json.dumps(controller_checkpoint_to_dict(replace(checkpoint, ownership_incarnation=3)))
        )
    else:
        store._write(replace(store.load(), ownership_incarnation=None, baseline_epoch=None))
    assert runtime.step()["phase"] == "blocked"
    assert cloud.starts == []


def test_last_moment_ownership_loss_prevents_submission(tmp_path, monkeypatch):
    runtime, cloud, _store, snapshot, checkpoint = setup_revalidation(tmp_path)
    assert publish(tmp_path, lambda: snapshot)
    original = StandbyRestorationStore.record_submission

    def invalidate(self, **kwargs):
        value = original(self, **kwargs)
        (tmp_path / "controller-checkpoint.json").write_text(
            json.dumps(
                controller_checkpoint_to_dict(
                    replace(
                        checkpoint,
                        ownership_incarnation=3,
                        ownership_continuity_invalidated=True,
                    )
                )
            )
        )
        return value

    monkeypatch.setattr(StandbyRestorationStore, "record_submission", invalidate)
    assert runtime.step()["phase"] == "blocked"
    assert cloud.starts == []


def test_corrupt_restoration_cannot_interrupt_snapshot_collection(tmp_path):
    _runtime_value, _cloud, store, snapshot, _checkpoint = setup_revalidation(tmp_path)
    store.path.write_text("{}")
    assert reconciliation_authority(tmp_path, snapshot) is None


@pytest.mark.parametrize("value", [None, [], "invalid", 1])
@pytest.mark.parametrize("evidence", ["guard.json", "restoration-revalidation.json"])
def test_nonobject_revalidation_evidence_denies_start(tmp_path, value, evidence):
    from nebius_vpngw.agent.vm_ha.restoration_revalidation import FILENAME

    runtime, cloud, _store, snapshot, _ = setup_revalidation(tmp_path)
    assert publish(tmp_path, lambda: snapshot)
    path = tmp_path / (FILENAME if evidence == "restoration-revalidation.json" else evidence)
    path.write_text(json.dumps(value))
    assert runtime.step()["phase"] == "blocked"
    assert cloud.starts == []


@pytest.mark.parametrize("value", [None, [], "invalid", 1])
def test_nonobject_optional_diagnostics_cannot_interrupt_rearm(tmp_path, value):
    runtime, cloud, _store, snapshot, _ = setup_revalidation(tmp_path)
    assert publish(tmp_path, lambda: snapshot)
    path = tmp_path / "restoration-diagnostics.json"
    path.write_text(json.dumps(value))
    assert runtime.step()["phase"] == "running"
    assert len(cloud.starts) == 1


def test_operator_recovery_requires_canonical_request_and_is_idempotent(tmp_path):
    runtime, cloud, store, snapshot, _ = setup_revalidation(tmp_path)
    record = store.load()
    store._write(replace(record, ownership_incarnation=None, baseline_epoch=None))
    assert not publish(tmp_path, lambda: snapshot)
    request = {
        "schema": "nebius-vpngw/vm-ha-rearm-request-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "owner_node_id": "node-a",
        "target_node_id": "node-b",
        "generation_id": "a" * 64,
        "promotion_receipt_id": record.promotion_receipt_id,
        "request_id": "f" * 64,
        "requested_at": 200.0,
    }
    path = tmp_path / "rearm-request.json"
    path.write_text(json.dumps({**request, "schema": "wrong"}))
    with pytest.raises(ValueError):
        publish(tmp_path, lambda: snapshot)
    assert cloud.starts == []
    path.write_text(json.dumps(request))
    assert publish(tmp_path, lambda: snapshot)
    renewed = store.load()
    assert renewed.ownership_incarnation == 2
    assert renewed.authorization_id != record.authorization_id
    assert publish(tmp_path, lambda: snapshot)
    assert store.load() == renewed
    assert runtime.step()["phase"] == "running"
    assert len(cloud.starts) == 1


@pytest.mark.parametrize("crash_after", ["before-request", "request", "unblock"])
@pytest.mark.parametrize("previous_operator", [False, True])
def test_operator_retry_crash_preserves_budget_until_controller_proof(
    tmp_path, monkeypatch, crash_after, previous_operator
):
    from nebius_vpngw.agent import vm_ha_rearm

    runtime, cloud, store, snapshot, _ = setup_revalidation(tmp_path)
    # Even the original-epoch fast path must wait for this request's proof.
    cloud.instances[runtime.local.compute_id] = replace(
        cloud.instances[runtime.local.compute_id], resource_version="10"
    )
    if previous_operator:
        store.authorize_operator(request_id="e" * 64, incarnation=2, epoch="10", updated_at=99.0)
    receipt_id = store.load().promotion_receipt_id
    store.accept_start(
        receipt_id=receipt_id,
        stopped_revision="20",
        rearm_operation_id=vm_ha_rearm._operation_id(receipt_id, "node-b", "20"),
        updated_at=100.0,
    )
    store.record_submission(receipt_id=receipt_id, updated_at=100.0)
    blocked = store.block(
        receipt_id=receipt_id, reason="automatic-retry-exhausted", updated_at=400.0
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "vm_ha": {
                    "enabled": True,
                    "node": {"node_id": "node-a"},
                    "runtime_binding": runtime.binding.model_dump(mode="json"),
                }
            }
        )
    )
    original_write = vm_ha_rearm._atomic_write_json
    original_restart = StandbyRestorationStore.restart_blocked

    def write(path, value):
        if path.name == "rearm-request.json" and crash_after == "before-request":
            raise RuntimeError("simulated crash")
        original_write(path, value)
        if path.name == "rearm-request.json" and crash_after == "request":
            raise RuntimeError("simulated crash")

    def restart(self, **kwargs):
        result = original_restart(self, **kwargs)
        if crash_after == "unblock":
            raise RuntimeError("simulated crash")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(vm_ha_rearm, "_atomic_write_json", write)
        patch.setattr(StandbyRestorationStore, "restart_blocked", restart)
        with pytest.raises(RuntimeError, match="simulated crash"):
            vm_ha_rearm.request_rearm_retry(config_path=config, state_dir=tmp_path)

    request_path = tmp_path / "rearm-request.json"
    if crash_after == "before-request":
        assert not request_path.exists()
        assert store.load() == blocked
        assert runtime.step()["phase"] == "inhibited"
        assert cloud.starts == []
        return
    assert request_path.exists()
    request = json.loads(request_path.read_text())
    assert store.load().attempt_count == blocked.attempt_count
    assert store.load().retry_window == blocked.retry_window
    assert store.load().authorization_id == blocked.authorization_id
    assert runtime.step()["phase"] == "blocked"
    assert cloud.starts == []
    assert vm_ha_rearm.request_rearm_retry(config_path=config, state_dir=tmp_path) == request
    assert publish(tmp_path, lambda: snapshot)
    assert store.load().attempt_count == 0
    assert store.load().retry_window is None
    cloud.instances[runtime.local.compute_id] = replace(
        cloud.instances[runtime.local.compute_id], resource_version="12"
    )
    assert runtime.step()["phase"] == "running"
    assert len(cloud.starts) == 1


def legacy_record(store):
    record = store.load()
    raw = record.to_dict()
    for key in ("ownership_incarnation", "baseline_epoch", "retry_window"):
        raw.pop(key)
    raw["schema"] = restoration_upgrade.OLD_SCHEMA
    identity = {**record.identity_payload(), "schema": restoration_upgrade.OLD_SCHEMA}
    raw["authorization_id"] = hashlib.sha256(canonical_json(identity).encode("ascii")).hexdigest()
    store.path.write_text(json.dumps(raw))
    return raw


def test_conversion_preserves_receipt_and_unknown_baseline(tmp_path):
    _, _, store, _, _ = setup_revalidation(tmp_path)
    legacy = legacy_record(store)
    receipt = store.receipt_path.read_bytes()
    assert restoration_upgrade.convert(tmp_path, artifact="f" * 64, boot="old", publish=True)
    record = store.load()
    assert record.ownership_incarnation is None
    assert record.retry_window is None
    assert json.loads((tmp_path / restoration_upgrade.JOURNAL).read_text())["predecessor"] == legacy
    assert store.receipt_path.read_bytes() == receipt
    assert not restoration_upgrade.convert(tmp_path, artifact="f" * 64, boot="new", publish=True)


def test_conversion_resumes_after_journal_crash_and_reboot(tmp_path, monkeypatch):
    _, _, store, _, _ = setup_revalidation(tmp_path)
    legacy_record(store)
    original = restoration_upgrade.atomic_write_json

    def fail_authorization(path, value):
        if path == store.path:
            raise OSError("simulated power loss")
        original(path, value)

    monkeypatch.setattr(restoration_upgrade, "atomic_write_json", fail_authorization)
    with pytest.raises(OSError):
        restoration_upgrade.convert(tmp_path, artifact="f" * 64, boot="old", publish=True)
    monkeypatch.setattr(restoration_upgrade, "atomic_write_json", original)
    assert restoration_upgrade.convert(tmp_path, artifact="f" * 64, boot="new", publish=True)
    assert store.load() is not None


def test_submitted_state_cannot_omit_retry_window(tmp_path):
    _, _, store, _, _ = setup_revalidation(tmp_path)
    with pytest.raises(StandbyRestorationError, match="window"):
        replace(store.load(), attempt_count=1)


def test_retry_clock_survives_process_restart_and_reboot():
    started = RetryWindow.observe(None, now=100, monotonic=10, boot="a")
    same_boot = RetryWindow.observe(started, now=50, monotonic=120, boot="a")
    assert same_boot.elapsed == 110  # Wall-clock rollback cannot refill this boot's allowance.
    restarted = RetryWindow.observe(
        RetryWindow.from_mapping(same_boot.to_dict()), now=60, monotonic=130, boot="a"
    )
    assert restarted.elapsed == 120
    rebooted = RetryWindow.observe(restarted, now=80, monotonic=2, boot="b")
    assert rebooted.elapsed == 140
    with pytest.raises(ValueError):
        RetryWindow.observe(rebooted, now=79, monotonic=3, boot="c")


def test_apply_prepares_legacy_recovery_before_starting_writers_and_never_refills(tmp_path):
    runtime, cloud, store, snapshot, _ = setup_revalidation(tmp_path)
    legacy_record(store)
    restoration_upgrade.convert(tmp_path, artifact="a" * 64, boot="boot-new", publish=True)
    expected = {
        "cluster_id": "cluster-a",
        "owner_node_id": "node-a",
        "peer_node_id": "node-b",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": DIGESTS,
    }
    restoration_upgrade.prepare_recovery(tmp_path, expected, operation_id="apply-a")
    assert cloud.starts == []
    assert publish(tmp_path, lambda: snapshot)
    assert runtime.step()["phase"] == "running"
    record = store.load()
    assert record.attempt_count == 1
    restoration_upgrade.prepare_recovery(tmp_path, expected, operation_id="apply-a")
    assert store.load() == record
    assert not (tmp_path / "rearm-request.json").exists()


def test_support_bundle_exports_recovery_evidence_without_free_text(tmp_path):
    from nebius_vpngw.support_bundle.probe import STATE_FILES
    from nebius_vpngw.support_bundle.projection import project

    assert "standby-restoration-revalidation.json" in STATE_FILES
    projected = project(
        {
            "attempt_count": 12,
            "original_epoch": "246",
            "current_epoch": "248",
            "retry_window": {"elapsed": 110.0},
            "first_failure": {"reason": "ownership-or-alias-drift", "updated_at": 100.0},
            "private_detail": "private-customer-data",
        }
    )
    assert projected["attempt_count"] == 12
    assert projected["retry_window"]["elapsed"] == 110.0
    assert projected["first_failure"]["reason"] == "ownership-or-alias-drift"
    assert "private_detail" not in projected


def test_support_bundle_retains_restoration_bindings_and_closed_block_reason(tmp_path):
    from nebius_vpngw.support_bundle.projection import project

    _runtime, _cloud, store, snapshot, _ = setup_revalidation(tmp_path)
    assert publish(tmp_path, lambda: snapshot)
    record = store.load().to_dict()
    record.update(request_fingerprint="d" * 64, blocked_reason="automatic-retry-exhausted")
    projected = project({**record, "private_detail": "PRIVATE_SENTINEL"})
    for key in (
        "digests",
        "former_owner_node_id",
        "policy_operation_id",
        "route_operation_id",
        "request_fingerprint",
        "blocked_reason",
    ):
        assert projected[key] == record[key]
    proof = json.loads((tmp_path / "standby-restoration-revalidation.json").read_text())
    assert project(proof)["route_operation_id"] == proof["route_operation_id"]
    assert "PRIVATE_SENTINEL" not in json.dumps(projected)
    assert "blocked_reason" not in project({"blocked_reason": "PRIVATE_SENTINEL"})
    assert "request_fingerprint" not in project({"request_fingerprint": "PRIVATE_SENTINEL"})


def test_diagnostics_keep_first_transient_failure_after_later_recovery(tmp_path):
    from nebius_vpngw.deploy.vm_ha_cloud import RetryableHACloudError

    runtime, cloud, _store, snapshot, _ = setup_revalidation(tmp_path)
    assert publish(tmp_path, lambda: snapshot)
    cloud.start_errors = [RetryableHACloudError("PRIVATE_SENTINEL")]
    assert runtime.step()["reason"] == "compute-start-retry-scheduled"
    path = tmp_path / "restoration-diagnostics.json"
    first = json.loads(path.read_text())["first_failure"]
    assert first["reason"] == "compute-start-retry-scheduled"
    runtime.clock = lambda: 106.0
    runtime.monotonic = lambda: 106.0
    assert runtime.step()["phase"] == "running"
    value = json.loads(path.read_text())
    assert value["first_failure"] == first
    assert "PRIVATE_SENTINEL" not in json.dumps(value)


def test_original_epoch_cannot_bypass_observed_ownership_loss(tmp_path):
    runtime, cloud, _store, _snapshot_value, checkpoint = setup_revalidation(tmp_path)
    cloud.instances[runtime.local.compute_id] = replace(
        cloud.instances[runtime.local.compute_id], resource_version="10"
    )
    changed = replace(
        checkpoint,
        ownership_incarnation=3,
        ownership_continuity_invalidated=True,
        established_ownership_context=None,
    )
    (tmp_path / "controller-checkpoint.json").write_text(
        json.dumps(controller_checkpoint_to_dict(changed))
    )
    assert runtime.step()["phase"] == "blocked"
    assert cloud.starts == []


def test_exact_apply_lock_allows_only_passive_materialization_on_stale_owner(tmp_path):
    from nebius_vpngw.agent.vm_ha_controller import ActionKind, VMHAController

    _, _, _, snapshot, checkpoint = setup_revalidation(tmp_path, "node-b")
    snapshot = replace(snapshot, apply_locked=True, data_plane_mode=DataPlaneMode.BLOCKED)
    checkpoint = replace(checkpoint, established_ownership_context=None)
    result = VMHAController(peer_timeout_seconds=10, suspicion_seconds=5).decide(
        snapshot, checkpoint
    )
    assert result.action.kind is ActionKind.ENTER_PASSIVE
    assert result.forwarding_enabled is False


def test_missing_legacy_baseline_waits_for_controller_even_with_unchanged_epoch(tmp_path):
    runtime, cloud, store, snapshot, checkpoint = setup_revalidation(tmp_path)
    cloud.instances[runtime.local.compute_id] = replace(
        cloud.instances[runtime.local.compute_id], resource_version="10"
    )
    snapshot = replace(
        snapshot,
        cloud=replace(snapshot.cloud, ownership_epoch="10"),
        routes_reconciled_context=replace(snapshot.routes_reconciled_context, ownership_epoch="10"),
    )
    checkpoint = replace(checkpoint, established_ownership_context=snapshot.ownership_context)
    (tmp_path / "controller-checkpoint.json").write_text(
        json.dumps(controller_checkpoint_to_dict(checkpoint))
    )
    legacy_record(store)
    restoration_upgrade.convert(tmp_path, artifact="f" * 64, boot="boot-new", publish=True)
    expected = {
        "cluster_id": "cluster-a",
        "owner_node_id": "node-a",
        "peer_node_id": "node-b",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": DIGESTS,
    }
    restoration_upgrade.prepare_recovery(tmp_path, expected, operation_id="apply-a")
    assert runtime.step()["phase"] == "blocked"
    assert cloud.starts == [] and (tmp_path / "rearm-request.json").exists()
    assert publish(tmp_path, lambda: snapshot)
    assert runtime.step()["phase"] == "running"
    assert len(cloud.starts) == 1


def test_completed_start_observation_tolerates_later_owner_revision(tmp_path):
    runtime, cloud, _, snapshot, _ = setup_revalidation(tmp_path)
    assert publish(tmp_path, lambda: snapshot)
    start = cloud.start_instance

    def change_revision(instance, operation):
        start(instance, operation)
        cloud.instances[runtime.local.compute_id] = replace(
            cloud.instances[runtime.local.compute_id], resource_version="13"
        )

    cloud.start_instance = change_revision
    assert runtime.step()["phase"] == "running"
    assert len(cloud.starts) == 1
