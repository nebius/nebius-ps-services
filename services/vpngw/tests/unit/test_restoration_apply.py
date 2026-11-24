from __future__ import annotations

import contextlib
import io
import json
from types import SimpleNamespace

import pytest
from tests.unit import test_ha_package_contract as package_contract
from tests.unit.test_vm_ha_restoration_revalidation import legacy_record, setup_revalidation

from nebius_vpngw import restoration_upgrade, runtime_migration
from nebius_vpngw.agent.vm_ha.restoration import RestorationPhase
from nebius_vpngw.agent.vm_ha.store import atomic_write_json
from nebius_vpngw.deploy import checkpoint_conversion, handoff_remote, restoration_apply

Admitted = package_contract.Admitted


@pytest.fixture
def package_admission(tmp_path, monkeypatch):
    return package_contract.admission.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    runtime, cloud, store, snapshot, _ = setup_revalidation(tmp_path)
    legacy = legacy_record(store)
    owner = SimpleNamespace(
        hostname="owner",
        vm_ha_node=runtime.local,
        vm_ha_generation=SimpleNamespace(generation_id=runtime.binding.generation_id),
    )
    peer = SimpleNamespace(vm_ha_node=runtime.peer)
    expected = restoration_apply.identity(runtime.binding, owner, peer)
    original_source = checkpoint_conversion.source
    (tmp_path / "boot").write_text("boot-new")
    (tmp_path / "forwarding").write_text("1")
    monkeypatch.setattr(
        checkpoint_conversion,
        "source",
        lambda: (
            original_source()
            + """
checkpoint_repair.STATE=_test_state
checkpoint_repair.BOOT=_test_state/'boot'
checkpoint_repair.FORWARDING=_test_state/'forwarding'
_original_exact_lock=checkpoint_repair.exact_lock
checkpoint_repair.exact_lock=lambda expected:_original_exact_lock(expected,_test_state)
"""
        ),
    )
    events = []

    class SSH:
        def _inspect_vm_ha_host(self, _target, _name, _local, *, script):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exec(script, {"_test_state": tmp_path})
            return json.loads(output.getvalue())

        def install_vm_ha_apply_lock(
            self, _target, _inst, _local, *, runtime_binding, operation_id
        ):
            # The installed v1 writer sees a quiescent record, without a v2 reader.
            assert json.loads(store.path.read_text())["phase"] == "blocked"
            atomic_write_json(
                tmp_path / "apply.lock",
                {
                    "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
                    "apply_locked": True,
                    "cluster_id": runtime_binding.cluster_id,
                    "node_id": runtime.local.node_id,
                    "generation_id": runtime_binding.generation_id,
                    "operation_id": operation_id,
                },
            )
            events.append("lock")

        def verify_vm_ha_apply_inhibition_quiescent(self, *_args, **_kwargs):
            (tmp_path / "forwarding").write_text("0")
            atomic_write_json(
                tmp_path / "guard.json",
                {
                    "schema": "nebius-vpngw/vm-ha-status-v1",
                    "guard_boot_id": "boot-new",
                    "data_plane_mode": "passive",
                },
            )
            events.append("fence")

        def stop_vm_ha_mutation_services(self, *_args):
            events.append("stop")

        def refresh_vm_ha_control_services(self, *_args):
            assert events[-1] == "activate"
            events.append("refresh")

    def prepare(_ssh, _target, _instance, _local, identity, operation):
        assert events[-1] == "package"
        restoration_upgrade.prepare_recovery(tmp_path, identity, operation_id=operation)

    monkeypatch.setattr(checkpoint_conversion, "prepare_restoration_recovery", prepare)
    monkeypatch.setattr(
        runtime_migration, "activate_ha", lambda _expected: events.append("activate")
    )
    monkeypatch.setattr(
        restoration_apply.ordinary_apply, "remote", lambda *_args: events.append("runtime-release")
    )
    return SimpleNamespace(
        root=tmp_path,
        store=store,
        legacy=legacy,
        runtime=runtime,
        owner=owner,
        peer=peer,
        expected=expected,
        events=events,
        ssh=SSH(),
        operation="d" * 64,
        artifact=SimpleNamespace(sha256="f" * 64),
    )


def test_bootstrap_reaches_real_guarded_package_admission_and_replays(
    recovery, package_admission, monkeypatch
):
    item = recovery
    request, _ = package_admission
    handoff_remote.MARKER.touch()
    monkeypatch.setattr(handoff_remote.repair, "FORWARDING", item.root / "forwarding")
    original_exact = handoff_remote.repair.exact_lock
    original_read = handoff_remote.repair.read_json
    monkeypatch.setattr(
        handoff_remote.repair,
        "read_json",
        lambda path: (
            json.loads(path.read_text()) if path.name == "apply.lock" else original_read(path)
        ),
    )
    monkeypatch.setattr(
        handoff_remote.repair, "exact_lock", lambda expected: original_exact(expected, item.root)
    )
    receipt = item.store.receipt_path.read_bytes()

    def package(expected):
        assert item.events[-3:] == ["lock", "fence", "stop"]
        request["expected"] = expected
        # Exercise actual retained-host lock and forwarding guards before
        # intercepting the first package mutation in this filesystem fixture.
        with pytest.raises(Admitted):
            handoff_remote.prepare_guarded(request)
        restoration_upgrade.convert(
            item.root, artifact=item.artifact.sha256, boot="boot-new", publish=True
        )
        item.events.append("package")

    kwargs = dict(
        binding=item.runtime.binding,
        peer=item.peer,
        artifact=item.artifact,
        operation=item.operation,
        prepare_package=package,
        verify_fenced=lambda: item.events.append("verify-fenced"),
        observe_start=lambda *_: pytest.fail("no accepted operation in this fixture"),
    )
    restoration_apply.bootstrap(item.ssh, "target", item.owner, {}, **kwargs)
    assert item.events == [
        "lock",
        "fence",
        "stop",
        "package",
        "activate",
        "refresh",
        "verify-fenced",
        "runtime-release",
    ]
    assert not (item.root / "apply.lock").exists()
    assert item.store.receipt_path.read_bytes() == receipt
    assert item.store.load().phase is RestorationPhase.COMMITTED
    before = item.store.path.read_bytes()
    restoration_apply.bootstrap(item.ssh, "target", item.owner, {}, **kwargs)
    assert item.store.path.read_bytes() == before
    assert item.events.count("package") == 1


def test_parking_history_recovers_crash_before_write_and_rejects_foreign_apply(
    recovery, monkeypatch
):
    item = recovery
    original = restoration_upgrade.atomic_write_json

    def interrupted(path, value):
        if path == item.store.path:
            raise OSError("simulated crash")
        original(path, value)

    monkeypatch.setattr(restoration_upgrade, "atomic_write_json", interrupted)
    with pytest.raises(OSError):
        restoration_upgrade.park_for_upgrade(
            item.root, item.expected, operation_id=item.operation, artifact=item.artifact.sha256
        )
    monkeypatch.setattr(restoration_upgrade, "atomic_write_json", original)
    restoration_upgrade.park_for_upgrade(
        item.root, item.expected, operation_id=item.operation, artifact=item.artifact.sha256
    )
    journal = json.loads((item.root / restoration_upgrade.RECOVERY).read_text())
    assert journal["predecessor"] == item.legacy
    assert json.loads(item.store.path.read_text())["phase"] == "blocked"
    with pytest.raises(ValueError):
        restoration_upgrade.park_for_upgrade(
            item.root, item.expected, operation_id="foreign", artifact=item.artifact.sha256
        )


def test_installed_lock_is_recognized_before_phase_acknowledgement(recovery):
    item = recovery
    restoration_upgrade.park_for_upgrade(
        item.root, item.expected, operation_id=item.operation, artifact=item.artifact.sha256
    )
    item.ssh.install_vm_ha_apply_lock(
        "target", item.owner, {}, runtime_binding=item.runtime.binding, operation_id=item.operation
    )
    request = {
        "action": "inspect",
        "identity": item.expected,
        "operation": item.operation,
        "artifact": item.artifact.sha256,
        "lock": checkpoint_conversion.context(
            item.owner, item.operation, cluster_id=item.runtime.binding.cluster_id
        ),
    }
    observed = restoration_apply.control(item.ssh, "target", item.owner, {}, request)
    assert observed["phase"] == "prepared" and observed["locked"] is True
    assert (item.root / "forwarding").read_text() == "1"


def test_completed_parent_archives_transaction_and_allows_new_recovery(recovery, monkeypatch):
    import os

    item = recovery
    value = restoration_upgrade.park_for_upgrade(
        item.root, item.expected, operation_id=item.operation, artifact=item.artifact.sha256
    )
    atomic_write_json(item.root / restoration_upgrade.RECOVERY, {**value, "phase": "released"})
    original_sync = os.fsync
    retired_sync = []

    def sync(fd):
        original_sync(fd)
        if not (item.root / restoration_upgrade.RECOVERY).exists():
            retired_sync.append(fd)

    monkeypatch.setattr(os, "fsync", sync)
    restoration_apply.control(
        item.ssh,
        "target",
        item.owner,
        {},
        {
            "action": "complete",
            "identity": item.expected,
            "operation": item.operation,
            "artifact": item.artifact.sha256,
        },
    )
    assert not (item.root / restoration_upgrade.RECOVERY).exists()
    assert retired_sync, "transaction retirement must be durable before reporting completion"
    assert (item.root / ("standby-restoration-apply-" + item.operation + ".json")).exists()
    next_value = restoration_upgrade.park_for_upgrade(
        item.root, item.expected, operation_id="e" * 64, artifact=item.artifact.sha256
    )
    assert next_value["operation_id"] == "e" * 64


@pytest.mark.parametrize("crash_after", [1, 2, 3, 4])
@pytest.mark.parametrize("successful", [False, True])
@pytest.mark.parametrize("legacy_blocked", [False, True])
def test_accepted_legacy_start_settlement_survives_every_write(
    recovery, monkeypatch, crash_after, successful, legacy_blocked
):
    from nebius_vpngw.agent.vm_ha_rearm import RearmCheckpoint, _operation_id, _write_checkpoint
    from nebius_vpngw.deploy.vm_ha_cloud import AcceptedCloudOperation, VMHACloudOperationJournal

    item = recovery
    restoration_upgrade.convert(
        item.root, artifact=item.artifact.sha256, boot="boot-new", publish=True
    )
    receipt = item.store.load().promotion_receipt_id
    operation = _operation_id(receipt, "node-b", "20")
    item.store.accept_start(
        receipt_id=receipt, stopped_revision="20", rearm_operation_id=operation, updated_at=103
    )
    item.store.record_submission(receipt_id=receipt, updated_at=104)
    if legacy_blocked:
        item.store.block(
            receipt_id=receipt, reason="compute-start-permanent-failure", updated_at=105
        )
    legacy_record(item.store)
    _write_checkpoint(
        item.root / "rearm-checkpoint.json",
        RearmCheckpoint(
            receipt_id=receipt,
            target_node_id="node-b",
            stopped_revision="20",
            operation_id=operation,
            phase="blocked" if legacy_blocked else "starting",
            attempted_at=103,
        ),
    )
    journal = VMHACloudOperationJournal(item.root / "rearm-cloud-operation.json")
    accepted = AcceptedCloudOperation(operation, "start-instance", "accepted-a")
    journal.save(accepted)
    raw = json.loads(journal.path.read_text())
    assert not (item.root / "rearm-operation-result.json").exists()
    assert restoration_upgrade.recovery_evidence(item.root, item.expected)
    original = restoration_upgrade.atomic_write_json
    writes = 0

    def write(path, value):
        nonlocal writes
        original(path, value)
        writes += 1
        if writes == crash_after:
            raise OSError("crash after durable write")

    original_unlink = type(journal.path).unlink

    def unlink(path, **kwargs):
        original_unlink(path, **kwargs)
        if path == journal.path and crash_after == 4:
            raise OSError("crash after journal retirement")

    with monkeypatch.context() as patch:
        patch.setattr(restoration_upgrade, "atomic_write_json", write)
        patch.setattr(type(journal.path), "unlink", unlink)
        with pytest.raises(OSError):
            restoration_upgrade.settle_accepted(
                item.root,
                item.expected,
                accepted=raw,
                instance_id="compute-b",
                successful=successful,
            )
    if journal.path.exists():
        restoration_upgrade.settle_accepted(
            item.root, item.expected, accepted=raw, instance_id="compute-b", successful=successful
        )
    assert journal.load() is None
    assert json.loads(item.store.path.read_text())["phase"] == "blocked"
    assert (
        json.loads((item.root / "rearm-operation-result.json").read_text())["successful"]
        is successful
    )


def test_ssh_readiness_does_not_skip_standby_readiness(monkeypatch):
    phases = iter(["awaiting-standby", "awaiting-standby", "completed"])
    monkeypatch.setattr(
        restoration_apply,
        "control",
        lambda *_: {"progress": True, "restoration_phase": next(phases)},
    )
    waits = []
    restoration_apply.wait_ready(
        None, "target", None, {}, request={}, clock=lambda: 0, sleeper=waits.append
    )
    assert waits == [5, 5]


@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize("interrupt_consumption", [False, True])
def test_apply_observes_existing_running_peer_after_request_consumption(
    tmp_path, monkeypatch, accepted, interrupt_consumption
):
    from dataclasses import replace

    from nebius_vpngw.agent import vm_ha_rearm
    from nebius_vpngw.deploy.vm_ha_cloud import (
        AcceptedCloudOperation,
        InstanceCloudState,
        VMHACloudOperationJournal,
    )

    runtime, cloud, store, _, _ = setup_revalidation(tmp_path)
    expected = {
        "cluster_id": runtime.binding.cluster_id,
        "owner_node_id": runtime.local.node_id,
        "peer_node_id": runtime.peer.node_id,
        "allocation_id": runtime.binding.shared_allocation_id,
        "generation_id": runtime.binding.generation_id,
        "digests": dict(store.load().digests),
    }
    receipt = store.load().promotion_receipt_id
    operation = "d" * 64
    if accepted:
        start = vm_ha_rearm._operation_id(receipt, runtime.peer.node_id, "20")
        store.accept_start(
            receipt_id=receipt, stopped_revision="20", rearm_operation_id=start, updated_at=100.0
        )
        store.record_submission(receipt_id=receipt, updated_at=100.0)
        vm_ha_rearm._write_checkpoint(
            tmp_path / "rearm-checkpoint.json",
            vm_ha_rearm.RearmCheckpoint(
                receipt,
                runtime.peer.node_id,
                "20",
                start,
                "starting",
                100.0,
            ),
        )
        journal = VMHACloudOperationJournal(tmp_path / "rearm-cloud-operation.json")
        journal.save(AcceptedCloudOperation(start, "start-instance", "accepted-a"))
    legacy_record(store)
    if accepted:
        restoration_upgrade.settle_accepted(
            tmp_path,
            expected,
            accepted=json.loads(journal.path.read_text()),
            instance_id=runtime.peer.compute_id,
            successful=True,
        )
    transaction = restoration_upgrade.park_for_upgrade(
        tmp_path,
        expected,
        operation_id=operation,
        artifact="f" * 64,
    )
    restoration_upgrade.convert(tmp_path, artifact="f" * 64, boot="boot-new", publish=True)
    restoration_upgrade.prepare_recovery(tmp_path, expected, operation_id=operation)
    atomic_write_json(tmp_path / restoration_upgrade.RECOVERY, {**transaction, "phase": "released"})
    before = store.load()
    cloud.instances[runtime.peer.compute_id] = replace(
        cloud.instances[runtime.peer.compute_id],
        state=InstanceCloudState.RUNNING,
    )
    consume = vm_ha_rearm._consume_request

    def interrupted(path, request):
        consume(path, request)
        raise OSError("interrupted after consumption")

    with monkeypatch.context() as patch:
        if interrupt_consumption:
            patch.setattr(vm_ha_rearm, "_consume_request", interrupted)
        runtime.step()
    assert not (tmp_path / "rearm-request.json").exists()
    assert store.load().phase is RestorationPhase.AWAITING_STANDBY
    assert restoration_upgrade.recovery_progress(tmp_path, expected, operation_id=operation)
    assert runtime.step()["phase"] == "running"
    assert cloud.starts == []
    assert store.load().retry_window == before.retry_window
    assert store.load().attempt_count == before.attempt_count
    assert store.load().request_fingerprint == before.request_fingerprint
    store.complete(receipt_id=receipt, updated_at=110.0)
    assert restoration_upgrade.recovery_progress(tmp_path, expected, operation_id=operation)
    transaction_path = tmp_path / restoration_upgrade.RECOVERY
    released = json.loads(transaction_path.read_text())
    for mutation in (
        {**released, "phase": "installed"},
        {**released, "operation_id": "foreign"},
        {**released, "predecessor": {**released["predecessor"], "authorization_id": "0" * 64}},
    ):
        transaction_path.write_text(json.dumps(mutation))
        assert not restoration_upgrade.recovery_progress(tmp_path, expected, operation_id=operation)
    transaction_path.write_text(json.dumps(released))
    checkpoint_path = tmp_path / "rearm-checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint_path.write_text(json.dumps({**checkpoint, "operation_id": "foreign"}))
    assert not restoration_upgrade.recovery_progress(tmp_path, expected, operation_id=operation)
