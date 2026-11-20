from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest
from test_vm_ha_upgrade_recovery import ARTIFACT, checkpoint, evidence_fixture

from nebius_vpngw.deploy import vm_ha_corrective as corrective
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
    _validate_successor,
    vm_ha_missing_standby_disk_name,
)
from nebius_vpngw.runtime_migration import release as real_runtime_release


def failed_release():
    state = checkpoint()
    return replace(
        state,
        transaction=replace(
            state.transaction,
            pending_effect="verify-active-forwarding-and-routes",
            completed_effects=tuple(
                sorted((*state.transaction.completed_effects, "activate-node-0"))
            ),
        ),
    )


def record(state):
    prior = dict(state.transaction.resource_bindings)[ARTIFACT]
    child = {
        "runtime": {
            "phase": "complete",
            "parent": state.transaction.operation_id,
            "artifact": prior,
            "operation": "1" * 32,
            "digest": "2" * 64,
        },
        "ordinary": {"state": "complete", "id": "1" * 32, "digest": "3" * 64},
        "conversion": {"phase": "completed", "digest": "4" * 64},
    }
    return {
        "prior_record": state.record_sha256,
        "prior_parent": state.transaction.operation_id,
        "prior_artifact": prior,
        "artifact": "c" * 64,
        "owner": "node-0",
        "hosts": "d" * 64,
        "children": {m.node_id: copy.deepcopy(child) for m in state.members},
        "locks": {m.node_id: {"operation_id": "7" * 64} for m in state.members},
        "operation": corrective.operation_id(state, "c" * 64),
        "ssh_trust": "e" * 64,
        "cloud": "f" * 64,
        "approval": "b" * 64,
    }


def test_corrective_cycle_retains_identity_and_archives_failed_release(tmp_path):
    state = failed_release()
    new = corrective.successor(state, record(state), bindings={})
    store = VMHALifecycleStore(tmp_path / "lifecycle.json")
    # Seed an authentic unfinished checkpoint, as the prior apply would have done.
    store.path.write_text(json.dumps(state.to_dict()))
    store.path.chmod(0o600)
    store.write_verified(new, predecessor_sha256=state.record_sha256)
    history = store.path.parent / (store.path.name + ".history") / (state.record_sha256 + ".json")
    assert json.loads(history.read_bytes()) == state.to_dict()
    assert new.has_same_identity(state)
    assert new.transaction.completed_effects == ()
    assert state.transaction.pending_effect == "verify-active-forwarding-and-routes"
    assert new.transaction.operation_id != state.transaction.operation_id
    store.write_verified(new, predecessor_sha256=state.record_sha256)
    corrective.archive(state, store.path)


@pytest.mark.parametrize("phase", ["installed", "activating"])
def test_corrective_cycle_admits_completed_packages_before_owner_release(phase):
    state = checkpoint()
    state = replace(state, transaction=replace(state.transaction, pending_effect="activate-node-1"))
    value = record(state)
    for child in value["children"].values():
        child["runtime"]["phase"] = phase
    new = corrective.successor(state, value, bindings={})
    assert new.has_same_identity(state)
    assert new.transaction.operation_id != state.transaction.operation_id
    assert new.transaction.completed_effects == ()
    assert corrective.authority(new)["children"] == value["children"]
    assert state.transaction.pending_effect == "activate-node-1"


@pytest.mark.parametrize("completed", [False, True])
def test_destroy_after_corrective_release_retains_history_and_exact_authority(tmp_path, completed):
    failed = failed_release()
    corrected = corrective.successor(failed, record(failed), bindings={})
    state = replace(
        corrected,
        status=VMHALifecycleStatus.ACTIVE if completed else VMHALifecycleStatus.ACTIVATING,
        transaction=replace(
            corrected.transaction,
            checkpoint="activation-complete" if completed else "owner-release-pending",
            pending_effect=None if completed else "verify-active-forwarding-and-routes",
            completed_effects=("activate-node-0", "activate-node-1"),
        ),
    )
    store = VMHALifecycleStore(tmp_path / "gateway.yaml")
    store.path.write_text(json.dumps(state.to_dict()))
    store.path.chmod(0o600)
    destruction = VMHALifecycleState.start_destruction(
        state,
        operation_id="destroy-exact-pair",
        approval_digest="1" * 64,
        desired_state_digest="2" * 64,
        current_state_digest="3" * 64,
        destroy_plan_json='{"fixture":"exact-destroy-plan"}',
        current_observation={},
    )

    store.write_verified(destruction, predecessor_sha256=state.record_sha256)
    observed = store.read_hardened(
        expected_project_id=state.project_id, expected_gateway_name=state.gateway_name
    ).state
    assert observed == destruction and observed.has_same_identity(state)
    assert observed.transaction.approval_kind == "destruction"
    assert observed.status is VMHALifecycleStatus.REMOVAL_IN_PROGRESS
    before = dict(state.transaction.resource_bindings)
    after = dict(observed.transaction.resource_bindings)
    assert all(after[key] == value for key, value in before.items())
    assert after[corrective.AUTHORITY_KEY] == before[corrective.AUTHORITY_KEY]
    if not completed:
        assert after["destroy-superseded-host-effect"] == state.transaction.pending_effect

    # Retaining corrective history grants no authority to rebind or drop it.
    for resources in (
        {key: value for key, value in after.items() if key != corrective.AUTHORITY_KEY},
        {**after, corrective.AUTHORITY_KEY: "{}"},
    ):
        invalid = replace(
            destruction,
            transaction=replace(
                destruction.transaction, resource_bindings=tuple(sorted(resources.items()))
            ),
        )
        with pytest.raises(ValueError, match="destruction successor is not exact"):
            _validate_successor(state, invalid)


def test_missing_standby_after_corrective_release_preserves_history(tmp_path):
    failed = failed_release()
    corrected = corrective.successor(failed, record(failed), bindings={})
    state = replace(
        corrected,
        status=VMHALifecycleStatus.ACTIVE,
        transaction=replace(
            corrected.transaction,
            checkpoint="activation-complete",
            pending_effect=None,
            completed_effects=("activate-node-0", "activate-node-1"),
        ),
    )
    owner, target = state.members
    replacement = VMHALifecycleState.start_missing_standby_replacement(
        state,
        target_instance_name=target.instance_name,
        replacement_cycle=1,
        replacement_disk_name=vm_ha_missing_standby_disk_name(
            gateway_name=state.gateway_name,
            instance_name=target.instance_name,
            predecessor_sha256=state.record_sha256,
            cycle=1,
        ),
        operation_id="4" * 64,
        approval_digest="5" * 64,
        desired_state_digest=state.transaction.desired_state_digest,
        current_state_digest="6" * 64,
        current_observation={
            "members": [
                {
                    "instance_name": owner.instance_name,
                    "present": True,
                    "state": "running",
                    "compute_id": owner.compute_id,
                    "network_interface_name": owner.network_interface_name,
                },
                {"instance_name": target.instance_name, "present": False},
            ],
            "shared_allocation": {
                "owner": {
                    "compute_id": owner.compute_id,
                    "network_interface_name": owner.network_interface_name,
                },
            },
        },
        artifact_sha256="c" * 64,
    )
    store = VMHALifecycleStore(tmp_path / "gateway.yaml")
    store.path.write_text(json.dumps(state.to_dict()))
    store.path.chmod(0o600)
    original = store.path.read_bytes()
    bindings = dict(replacement.transaction.resource_bindings)
    assert (
        bindings[corrective.AUTHORITY_KEY]
        == dict(state.transaction.resource_bindings)[corrective.AUTHORITY_KEY]
    )
    for resources in (
        {key: value for key, value in bindings.items() if key != corrective.AUTHORITY_KEY},
        {**bindings, corrective.AUTHORITY_KEY: "{}"},
        {**bindings, "compute:gateway-0": "foreign-owner"},
    ):
        invalid = replace(
            replacement,
            transaction=replace(
                replacement.transaction, resource_bindings=tuple(sorted(resources.items()))
            ),
        )
        with pytest.raises(ValueError):
            store.write_verified(invalid, predecessor_sha256=state.record_sha256)
        assert store.path.read_bytes() == original

    store.write_verified(replacement, predecessor_sha256=state.record_sha256)
    restored = store.read_hardened(
        expected_project_id=state.project_id, expected_gateway_name=state.gateway_name
    ).state
    assert restored == replacement
    assert restored.members[0] == owner
    assert not restored.members[1].compute_id and not restored.members[1].disk_id
    store.write_verified(replacement, predecessor_sha256=state.record_sha256)


@pytest.mark.parametrize(
    "field,value",
    [
        ("phase", "packages"),
        ("phase", "prepared"),
        ("phase", "inhibited"),
        ("phase", "releasing"),
        ("parent", "9" * 64),
        ("artifact", "9" * 64),
        ("operation", "9" * 32),
    ],
)
def test_incomplete_or_foreign_native_child_cannot_be_superseded(field, value):
    state = failed_release()
    value_record = record(state)
    value_record["children"]["node-0"]["runtime"][field] = value
    with pytest.raises(ValueError, match="completed exact predecessor"):
        corrective.successor(state, value_record, bindings={})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(owner="node-1"),
        lambda r: r.update(prior_record="0" * 64),
        lambda r: r.update(prior_parent="0" * 64),
        lambda r: r["children"]["node-1"]["ordinary"].update(state="failed"),
        lambda r: r["children"]["node-1"]["conversion"].update(phase="pending"),
    ],
)
def test_corrective_authority_cannot_rebind_or_discard_unfinished_work(mutation):
    state = failed_release()
    value = record(state)
    mutation(value)
    with pytest.raises(ValueError):
        corrective.successor(state, value, bindings={})


def test_new_cycle_rejects_cloud_effects_or_fabricated_completion():
    state = failed_release()
    with pytest.raises(ValueError):
        corrective.operation_id(
            replace(
                state,
                transaction=replace(state.transaction, accepted_cloud_operation_id="accepted"),
            ),
            "c" * 64,
        )
    new = corrective.successor(state, record(state), bindings={})
    with pytest.raises(ValueError):
        _validate_successor(
            state,
            replace(
                new, transaction=replace(new.transaction, completed_effects=("activate-node-0",))
            ),
        )


def test_archive_rejects_symlink_and_changed_history(tmp_path):
    state = failed_release()
    path = tmp_path / "state.json"
    corrective.archive(state, path)
    history = tmp_path / "state.json.history" / (state.record_sha256 + ".json")
    history.write_text("{}")
    with pytest.raises(ValueError, match="history differs"):
        corrective.archive(state, path)
    history.unlink()
    history.symlink_to(tmp_path / "outside")
    with pytest.raises(OSError):
        corrective.archive(state, path)
    assert not (tmp_path / "outside").exists()


def test_shared_host_proof_rejects_stale_lock_after_new_cycle(tmp_path):
    from nebius_vpngw.deploy.vm_ha_upgrade_recovery import validate_host_evidence

    state, evidence, generations = evidence_fixture(tmp_path)
    operations = {node: "9" * 64 for node in evidence}
    with pytest.raises(ValueError, match="exact apply lock"):
        validate_host_evidence(state, evidence, generations, lock_operations=operations)
    for item in evidence.values():
        item["apply_lock"]["operation_id"] = "9" * 64
    validate_host_evidence(
        state, evidence, generations, lock_operations=operations, require_stopped=True
    )
    evidence["node-1"]["units"]["frr.service"]["MainPID"] = "101"
    with pytest.raises(ValueError, match="writer or maintenance"):
        validate_host_evidence(
            state, evidence, generations, lock_operations=operations, require_stopped=True
        )


@pytest.fixture
def reservation_guest(monkeypatch, tmp_path, ordinary_operation_guest, runtime_migration_guest):
    import time
    from contextlib import nullcontext

    from nebius_vpngw import ordinary_operations as ops
    from nebius_vpngw import package_worker
    from nebius_vpngw.deploy import handoff_remote as guest

    monkeypatch.setattr(package_worker, "native_lock", lambda _path, **kwargs: nullcontext())
    state_dir = tmp_path / "ha"
    state_dir.mkdir(mode=0o700)
    forwarding = tmp_path / "forwarding"
    forwarding.write_text("0")
    monkeypatch.setattr(guest.repair, "STATE", state_dir)
    monkeypatch.setattr(guest.repair, "FORWARDING", forwarding)
    monkeypatch.setattr(guest.remote, "LOCK", tmp_path / "ordinary.lock")
    monkeypatch.setattr(ops, "alive", lambda identity: identity["pid"] == 111)
    monkeypatch.setattr(
        ops, "identity", lambda pid=None: {"pid": 111, "start": "1", "boot": ops.BOOT.read_text()}
    )
    monkeypatch.setattr(ops, "install_startup_guard", lambda **kwargs: None)
    monkeypatch.setattr(guest.repair, "writer_locks", lambda **kwargs: nullcontext())

    def publish(path, raw, mode=0o600):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(mode)

    monkeypatch.setattr(guest.repair, "publish", publish)

    canonical_exact = guest.repair.exact_lock

    def exact(expected, state_dir=state_dir):
        return canonical_exact(expected, state_dir)

    monkeypatch.setattr(guest.repair, "exact_lock", exact)
    monkeypatch.setattr(guest.repair, "cold_guard", lambda **kw: None)
    monkeypatch.setattr(guest, "corrective_fence", lambda *args, **kw: None)
    manager = ops.Manager(time.monotonic() + 60)
    old = ops.Operation.begin(
        request="1" * 32,
        config="2" * 64,
        artifact="3" * 64,
        predecessor="4" * 64,
        manager=manager,
        network=manager.network(),
        purpose="ha-handoff",
    )
    old.complete()
    prior_manifest = {
        "runtime_plan": runtime_migration_guest,
        "ha_parent": "a" * 64,
        "wheel_sha256": "b" * 64,
        "assets": {},
    }
    migration = guest.remote.migration
    migration.stage(runtime_migration_guest)
    migration.begin(runtime_migration_guest, "1" * 32, prior_manifest)
    migration.release()
    new_manifest = {
        **prior_manifest,
        "ha_parent": "c" * 64,
        "wheel_sha256": "d" * 64,
        "config_sha256": "2" * 64,
        "package_identity": "5" * 64,
    }
    payload = migration.verify_staged(runtime_migration_guest) / "payload"
    publish(payload / "manifest.json", json.dumps(new_manifest).encode())
    prior_lock = {
        "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
        "apply_locked": True,
        "cluster_id": "cluster",
        "node_id": "node",
        "generation_id": "6" * 64,
        "operation_id": "7" * 64,
    }
    publish(state_dir / "apply.lock", json.dumps(prior_lock).encode())
    monkeypatch.setattr(guest, "package_observation", lambda manifest: {"observed": "before"})
    request = {
        "action": "corrective-reserve",
        "operation": "c" * 64,
        "previous_lock": prior_lock,
        "children": guest.corrective_children(),
        "package": {"desired": new_manifest, "predecessor": {"observed": "before"}},
    }
    return guest, request


@pytest.mark.parametrize("boundary", ["reservation", "runtime", "inhibition", "lock", "receipt"])
@pytest.mark.parametrize("phase", ["installed", "activating", "complete"])
def test_guest_reservation_resumes_every_durable_boundary(
    reservation_guest, monkeypatch, boundary, phase
):
    guest, request = reservation_guest
    predecessor = guest.remote.migration.state()
    predecessor["phase"] = phase
    guest.remote.migration.save(predecessor)
    request["children"] = guest.corrective_children()
    ops = guest.ops
    publish = guest.repair.publish
    begin = guest.remote.migration.begin
    inhibit = guest.remote.migration.inhibit
    fired = False

    def crash():
        nonlocal fired
        if not fired:
            fired = True
            raise RuntimeError("injected interruption")

    def write(path, raw, mode=0o600):
        publish(path, raw, mode)
        if path.name == request["operation"] + ".json":
            value = json.loads(raw)
            if boundary == "reservation" and value["phase"] == "reserved":
                crash()
            if boundary == "receipt" and value["phase"] == "quiescent":
                crash()
        if boundary == "lock" and path.name == "apply.lock":
            crash()

    def native(*args, **kwargs):
        begin(*args, **kwargs)
        if boundary == "runtime":
            crash()

    def stop_startup():
        inhibit()
        if boundary == "inhibition":
            crash()

    monkeypatch.setattr(guest.repair, "publish", write)
    monkeypatch.setattr(guest.remote.migration, "begin", native)
    monkeypatch.setattr(guest.remote.migration, "inhibit", stop_startup)
    with pytest.raises(RuntimeError, match="injected"):
        guest.reserve_corrective(request)
    # The next invocation has a different process, and proves all old writers dead.
    monkeypatch.setattr(
        ops, "identity", lambda pid=None: {"pid": 222, "start": "2", "boot": ops.BOOT.read_text()}
    )
    monkeypatch.setattr(ops, "alive", lambda identity: identity["pid"] == 222)
    result = guest.reserve_corrective(request)
    assert result["phase"] == "quiescent"
    assert (
        json.loads((guest.repair.STATE / "apply.lock").read_text())["operation_id"]
        == request["operation"]
    )
    assert guest.remote.migration.state()["phase"] == "inhibited"
    from nebius_vpngw.deploy.ssh_push import SSHPush

    payload = json.loads((guest.repair.STATE / "apply.lock").read_text())
    canonical, _receipt = SSHPush._vm_ha_apply_lock_payload(
        **{key: payload[key] for key in ("cluster_id", "node_id", "generation_id", "operation_id")}
    )
    assert (guest.repair.STATE / "apply.lock").read_bytes() == canonical
    assert ops.Journal().read()["state"] == "unresolved"
    for name in ("ordinary", "runtime"):
        assert (
            guest.repair.STATE / "corrective" / (request["operation"] + "-" + name + ".json")
        ).exists()
    assert (
        guest.repair.read_json(
            guest.repair.STATE / "corrective" / (request["operation"] + "-runtime.json")
        )
        == predecessor
    )
    changed = copy.deepcopy(request)
    changed["package"]["desired"]["wheel_sha256"] = "e" * 64
    with pytest.raises(RuntimeError, match="authority changed"):
        guest.reserve_corrective(changed)


@pytest.mark.parametrize("phase", ["prepared", "inhibited", "packages", "releasing"])
def test_corrective_reservation_refuses_unfinished_package_or_release_work(
    reservation_guest, phase
):
    guest, request = reservation_guest
    state = guest.remote.migration.state()
    state["phase"] = phase
    guest.remote.migration.save(state)
    request["children"] = guest.corrective_children()
    with pytest.raises(RuntimeError, match="package work is incomplete"):
        guest.reserve_corrective(request)
    assert guest.remote.migration.state() == state
    assert not (guest.repair.STATE / "corrective").exists()


def test_corrective_proposal_is_read_only_and_uses_current_native_inventory(
    reservation_guest, monkeypatch
):
    guest, request = reservation_guest
    state = guest.remote.migration.state()
    state["phase"] = "activating"
    guest.remote.migration.save(state)
    request["children"] = guest.corrective_children()
    manifest = request["package"]["desired"]
    seen = []
    fresh = copy.deepcopy(state["plan"])
    fresh["before"]["another-package"] = ["1.0", "installed"]
    monkeypatch.setattr(guest.remote.migration, "plan_current", lambda *, deadline=None: fresh)
    monkeypatch.setattr(
        guest.remote,
        "inspect",
        lambda proposed, **kw: (
            seen.append((proposed, kw)) or {"runtime_plan": proposed["runtime_plan"]}
        ),
    )
    assert guest.corrective_plan({**request, "manifest": manifest}) == {
        "dependency_inventory": {},
        "observation": {"runtime_plan": fresh}
    }
    assert seen == [({**manifest, "runtime_plan": fresh,
                     "asset_retirement": {"predecessor": None, "removals": {}}},
                    {"runtime_admission": False, "dependency_inventory": {}})]
    assert guest.remote.migration.state() == state
    assert guest.corrective_children() == request["children"]
    assert not (guest.repair.STATE / "corrective").exists()


@pytest.mark.parametrize("phase", ["installed", "activating"])
def test_corrective_successor_preserves_exact_asset_retirement(
    reservation_guest, monkeypatch, phase
):
    guest, request = reservation_guest
    migration = guest.remote.migration
    state = migration.state()
    state["phase"] = phase
    old = "/etc/systemd/system/nebius-vpngw-old.service"
    pending = "/etc/systemd/system/nebius-vpngw-earlier.service"
    retained = "/etc/systemd/system/nebius-vpngw-retained.service"

    def asset(name):
        return {"member": "nebius_vpngw/systemd/" + name.rsplit("/", 1)[-1],
                "mode": 0o644, "sha256": "f" * 64}

    state["manifest"]["assets"] = {old: asset(old), retained: asset(retained)}
    state["manifest"]["asset_retirement"] = {
        "predecessor": {"operation": "9" * 32}, "removals": {pending: asset(pending)},
    }
    migration.save(state)
    request["children"] = guest.corrective_children()
    manifest = {**request["package"]["desired"], "assets": {retained: asset(retained)}}
    monkeypatch.setattr(migration, "plan_current", lambda *, deadline=None: state["plan"])
    monkeypatch.setattr(guest.remote, "inspect", lambda proposed, **_: {
        "asset_retirement": migration.asset_retirement(
            proposed, migration.completed_asset_predecessor()),
    })
    result = guest.corrective_plan({**request, "manifest": manifest})
    retirement = result["observation"]["asset_retirement"]
    assert retirement["removals"] == {old: asset(old), pending: asset(pending)}
    assert migration.state() == state
    selected = {**manifest, "asset_retirement": retirement}
    operation = "8" * 32
    # Filesystem ownership validation has its own real-file regression suite;
    # this boundary verifies exact journal authority before that validator runs.
    verified = []
    monkeypatch.setattr(migration, "verify_asset_retirement", verified.append)
    with pytest.raises(RuntimeError, match="corrective runtime predecessor differs"):
        migration.begin(state["plan"], operation, selected, superseded_sha256="0" * 64)
    assert migration.state() == state
    for invalid in (manifest, {**selected, "asset_retirement": {
        **retirement, "removals": {old: asset(old)}
    }}):
        with pytest.raises(RuntimeError, match="asset_retirement_unproven"):
            migration.begin(state["plan"], operation, invalid,
                            superseded_sha256=migration.digest(state))
        assert migration.state() == state
        assert verified == []
    migration.begin(state["plan"], operation, selected, superseded_sha256=migration.digest(state))
    assert verified == [retirement]
    assert migration.state()["manifest"]["asset_retirement"] == retirement
    assert migration.state()["operation"] == operation


def test_corrective_native_package_exclusion_precedes_any_reservation(
    reservation_guest, monkeypatch
):
    from contextlib import contextmanager

    from nebius_vpngw import package_worker

    guest, request = reservation_guest
    before = guest.corrective_children()

    @contextmanager
    def unavailable(_path, *, deadline):
        assert deadline == guest.remote.DEADLINE
        raise RuntimeError("native package lock unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(package_worker, "native_lock", unavailable)
    with pytest.raises(RuntimeError, match="native package lock unavailable"):
        guest.reserve_corrective(request)
    assert guest.corrective_children() == before
    assert not (guest.repair.STATE / "corrective").exists()


@pytest.mark.parametrize(
    "change",
    [
        None,
        "boot",
        "node",
        "allocation",
        "generation",
        "digests",
        "operation",
        "incarnation",
        "kind",
    ],
)
def test_corrective_guest_retains_only_exact_local_passive_intent(tmp_path, monkeypatch, change):
    from types import SimpleNamespace

    from nebius_vpngw.agent.vm_ha.models import DigestSet
    from nebius_vpngw.agent.vm_ha_checkpoint import controller_checkpoint_to_dict
    from nebius_vpngw.agent.vm_ha_controller import (
        ActionKind,
        ControllerAction,
        ControllerCheckpoint,
    )
    from nebius_vpngw.deploy import handoff_remote as guest

    def write(path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    state = tmp_path / "state"
    state.mkdir()
    boot = tmp_path / "boot"
    boot.write_text("boot-current")
    forwarding = tmp_path / "forwarding"
    forwarding.write_text("0")
    lock = {
        "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
        "apply_locked": True,
        "cluster_id": "cluster",
        "node_id": "node",
        "generation_id": "a" * 64,
        "operation_id": "b" * 64,
    }
    write(state / "apply.lock", lock)
    write(
        state / "guard.json",
        {
            "schema": guest.repair.STATUS_SCHEMA,
            "guard_boot_id": "boot-current",
            "data_plane_mode": "blocked",
        },
    )
    digests = {"configuration": "a" * 64, "static_routes": "c" * 64, "bgp_policy": "d" * 64}
    configuration = tmp_path / "configuration"
    write(
        configuration,
        {
            "vm_ha": {
                "cluster_id": "cluster",
                "node": {"node_id": "node"},
                "generation": {"generation_id": "a" * 64, "digests": digests},
                "runtime_binding": {"shared_allocation_id": "allocation"},
            }
        },
    )
    action = ControllerAction(
        kind=ActionKind.ENTER_PASSIVE,
        operation_id="boot-current:1:enter-passive:node",
        boot_id="boot-current",
        target_node_id="node",
        allocation_id="allocation",
        ownership_epoch="1",
        generation_id="a" * 64,
        digests=DigestSet(**digests),
    )
    payload = controller_checkpoint_to_dict(ControllerCheckpoint(sequence=1, pending_action=action))
    mutations = {
        "boot": ("boot_id", "old"),
        "node": ("target_node_id", "foreign"),
        "allocation": ("allocation_id", "foreign"),
        "generation": ("generation_id", "e" * 64),
        "digests": ("digests", dict(digests, configuration="e" * 64)),
        "operation": ("operation_id", "foreign"),
        "incarnation": ("ownership_incarnation", 1),
        "kind": ("kind", "enable-active"),
    }
    if change is not None:
        key, value = mutations[change]
        payload["pending_action"][key] = value
    checkpoint = state / "controller-checkpoint.json"
    write(checkpoint, payload)
    original = checkpoint.read_bytes()
    monkeypatch.setattr(guest.repair, "STATE", state)
    monkeypatch.setattr(guest.repair, "FORWARDING", forwarding)
    monkeypatch.setattr(guest.ops, "BOOT", boot)
    monkeypatch.setattr(guest.remote, "CONFIG", configuration)
    exact = guest.repair.exact_lock
    monkeypatch.setattr(guest.repair, "exact_lock", lambda expected: exact(expected, state))
    monkeypatch.setattr(guest.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    if change is None:
        guest.corrective_fence(lock)
    else:
        with pytest.raises((RuntimeError, ValueError)):
            guest.corrective_fence(lock)
    assert checkpoint.read_bytes() == original
    assert json.loads((state / "apply.lock").read_text()) == lock
    assert forwarding.read_text() == "0"


@pytest.mark.parametrize(
    "operator_policy", [None, {"content": "IyEvYmluL3NoCmV4aXQgMTAxCg==", "mode": 0o755}]
)
def test_corrective_runtime_preserves_original_policy_after_inhibited_activation(
    reservation_guest, monkeypatch, operator_policy
):
    import base64

    guest, request = reservation_guest
    migration = guest.remote.migration
    # The unit fixture runs without root; retain its explicit OS ownership
    # boundary while exercising actual begin/release and policy bytes.
    monkeypatch.setattr(
        migration,
        "policy_payload",
        lambda: (
            None
            if not migration.POLICY.exists()
            else {
                "content": base64.b64encode(migration.POLICY.read_bytes()).decode(),
                "mode": migration.POLICY.stat().st_mode & 0o777,
            }
        ),
    )
    monkeypatch.setattr(migration, "policy_snapshot", lambda: migration.policy_identity(migration.policy_payload()))
    previous = migration.state()
    previous.update(phase="activating", policy=operator_policy)
    migration.save(previous)
    migration.write(migration.POLICY, migration.POLICY_BYTES, 0o755)
    approved = copy.deepcopy(previous["plan"])
    approved["startup_policy"] = migration.policy_snapshot()
    migration.stage(approved)
    manifest = {**request["package"]["desired"], "runtime_plan": approved}
    migration.begin(approved, "e" * 32, manifest, superseded_sha256=migration.digest(previous))
    assert migration.state()["policy"] == operator_policy
    real_runtime_release()
    assert migration.state()["phase"] == "complete"
    if operator_policy is None:
        assert not migration.POLICY.exists()
    else:
        assert migration.POLICY.read_bytes() == base64.b64decode(operator_policy["content"])


def test_corrective_native_plan_refuses_changed_operator_boot_policy(reservation_guest):
    guest, _request = reservation_guest
    migration = guest.remote.migration
    previous = migration.state()
    previous["phase"] = "activating"
    previous["plan"]["unit_policy"] = {"strongswan.service": {"UnitFileState": "disabled"}}
    approved = copy.deepcopy(previous["plan"])
    approved["unit_policy"]["strongswan.service"]["UnitFileState"] = "enabled"
    with pytest.raises(RuntimeError, match="operator boot policy changed"):
        migration.corrective_policy(previous, approved)


@pytest.mark.parametrize("change", ["digest", "parent", "phase", "packages"])
def test_runtime_successor_requires_exact_completed_native_predecessor(
    reservation_guest, monkeypatch, change
):
    guest, request = reservation_guest
    migration = guest.remote.migration
    before = migration.state()
    before["phase"] = "activating" if change != "phase" else "packages"
    migration.save(before)
    manifest = copy.deepcopy(request["package"]["desired"])
    if change == "parent":
        manifest["ha_parent"] = before["manifest"]["ha_parent"]
    if change == "packages":
        monkeypatch.setattr(migration, "packages", lambda: {})
    with pytest.raises(RuntimeError, match="predecessor differs|postconditions are incomplete"):
        migration.begin(
            manifest["runtime_plan"],
            "f" * 32,
            manifest,
            superseded_sha256="0" * 64 if change == "digest" else migration.digest(before),
        )
    assert migration.state() == before


def test_inhibited_quiescence_recovery_retains_writer_and_effect_proofs(
    monkeypatch, ordinary_operation_guest
):
    from types import SimpleNamespace

    from nebius_vpngw import ordinary_operations as ops

    value = {
        "purpose": "ha-handoff",
        "owner": {"boot": ops.BOOT.read_text().strip()},
        "phase": "quiesce",
        "effects": [{"kind": "unit", "action": "stop", "state": "intent", "writer": {}}],
    }
    monkeypatch.setattr(ops, "alive", lambda _: False)
    manager = SimpleNamespace(quiet=lambda _: True, unit_finished=lambda _: True)
    assert not ops.handoff_recoverable(value, manager)
    assert ops.handoff_recoverable(value, manager, inhibited_quiescence=True)
    manager.unit_finished = lambda _: False
    assert not ops.handoff_recoverable(value, manager, inhibited_quiescence=True)
    manager.unit_finished = lambda _: True
    manager.quiet = lambda _: False
    assert not ops.handoff_recoverable(value, manager, inhibited_quiescence=True)
    manager.quiet = lambda _: True
    value["effects"][0]["action"] = "start"
    assert not ops.handoff_recoverable(value, manager, inhibited_quiescence=True)
    value["effects"][0]["action"] = "stop"
    value["phase"] = "install"
    assert not ops.handoff_recoverable(value, manager, inhibited_quiescence=True)
    value["phase"] = "quiesce"
    monkeypatch.setattr(ops, "alive", lambda _: True)
    assert not ops.handoff_recoverable(value, manager, inhibited_quiescence=True)


@pytest.mark.parametrize("unused", [False, True])
@pytest.mark.parametrize("interrupt_write", range(1, 10))
@pytest.mark.parametrize("passive_pending", [False, True], ids=["terminal", "pending-passive"])
def test_cli_corrective_resumes_without_losing_old_release_or_replacing_resources(
    tmp_path, monkeypatch, interrupt_write, unused, passive_pending
):
    from types import SimpleNamespace

    from nebius_vpngw import cli
    from nebius_vpngw.deploy import ordinary_handoff
    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleJournal

    initial, evidence, generations = evidence_fixture(tmp_path)
    if passive_pending:
        item = evidence["node-0"]
        item["controller"]["pending_action"] = dict(
            kind="enter-passive",
            boot_id=item["boot_id"],
            target_node_id="node-0",
            allocation_id=initial.allocation_id,
            generation_id=generations["node-0"][0],
            digests=generations["node-0"][1],
            ownership_incarnation=item["controller"]["ownership_incarnation"],
            operation_id=f"{item['boot_id']}:{item['controller']['sequence']}:enter-passive:node-0",
        )
    initial = replace(
        initial,
        transaction=replace(
            initial.transaction,
            desired_state_digest=cli._canonical_digest({}),
            pending_effect="verify-active-forwarding-and-routes",
        ),
    )
    store = VMHALifecycleStore(tmp_path / "lifecycle.json")
    store.write_verified(initial)
    original = record(initial)
    if unused:
        from nebius_vpngw.deploy.vm_ha_upgrade_recovery import host_digest

        unused_record = {
            **original,
            "artifact": "8" * 64,
            "operation": corrective.operation_id(initial, "8" * 64),
            "hosts": host_digest(evidence),
            "locks": {node: item["apply_lock"] for node, item in evidence.items()},
        }
        unused_state = corrective.successor(initial, unused_record, bindings={})
        store.write_verified(unused_state, predecessor_sha256=initial.record_sha256)
        initial = unused_state.begin_effect("reserve-corrective-node-1")
        store.write_verified(initial, predecessor_sha256=unused_state.record_sha256)
    instances = tuple(
        SimpleNamespace(
            hostname=m.instance_name,
            vm_ha_node=SimpleNamespace(node_id=m.node_id),
            vm_ha_generation=SimpleNamespace(
                generation_id=generations[m.node_id][0],
                digests=SimpleNamespace(**generations[m.node_id][1]),
            ),
        )
        for m in reversed(initial.members)
    )
    artifact = SimpleNamespace(
        sha256="c" * 64,
        dependency_plans=tuple(
            (
                i.hostname,
                SimpleNamespace(
                    manifest={"wheel_sha256": "c" * 64},
                    envelope={"desired": {"wheel_sha256": "c" * 64}},
                ),
            )
            for i in instances
        ),
    )
    approved = SimpleNamespace(artifact=artifact, digest="b" * 64)
    cloud = {"members": [{"present": True, "state": "running"} for _ in instances]}
    credentials = SimpleNamespace(resource_bindings=lambda: {})
    ssh_policy = SimpleNamespace(known_hosts_sha256="e" * 64)
    targets = {i.hostname: "fixture" for i in instances}
    monkeypatch.setattr(cli, "_vm_ha_desired_approval_state", lambda _: {})
    monkeypatch.setattr(cli, "_vm_ha_observation_matches_bindings", lambda *a, **kw: True)
    monkeypatch.setattr(cli, "validate_retained_route_scope", lambda *a: None)
    monkeypatch.setattr(cli, "_vm_ha_apply_order_for_owner", lambda *a: instances)
    monkeypatch.setattr(cli, "SSHPush", lambda **kw: object())
    monkeypatch.setattr(cli, "_upgrade_recovery_host_evidence", lambda *a: copy.deepcopy(evidence))
    reservations = {}
    trace = []

    def remote(ssh, target, instance, local, request):
        node = instance.vm_ha_node.node_id
        if request["action"] == "corrective-inspect":
            return {"children": original["children"][node], "reservation": reservations.get(node)}
        assert request["action"] == "corrective-reserve"
        assert (
            corrective.authority(
                store.read(
                    expected_project_id=initial.project_id,
                    expected_gateway_name=initial.gateway_name,
                )
            )
            is not None
        )
        assert (
            store.path.parent / (store.path.name + ".history") / (initial.record_sha256 + ".json")
        ).exists()
        result = {
            "phase": "quiescent",
            "key": {k: request[k] for k in ("operation", "previous_lock", "children")},
        }
        reservations[node] = result
        evidence[node]["apply_lock"] = {
            **request["previous_lock"],
            "operation_id": request["operation"],
        }
        trace.append(node)
        return result

    monkeypatch.setattr(ordinary_handoff, "corrective_action", remote)
    real_write = store.write_verified
    writes = 0

    def write(state, **kw):
        nonlocal writes
        real_write(state, **kw)
        writes += 1
        if writes == interrupt_write:
            raise RuntimeError("lost local acknowledgement")

    monkeypatch.setattr(store, "write_verified", write)

    def plan(state):
        return cli._plan_vm_ha_corrective(
            state=state,
            artifact=artifact,
            plan=object(),
            observation=cloud,
            credentials=credentials,
            local={},
            targets=targets,
            ssh_policy=ssh_policy,
        )

    state = initial
    for _attempt in range(2):
        context = plan(state)
        if context is None:
            break
        journal = VMHALifecycleJournal(store, state)
        try:
            state = cli._execute_vm_ha_corrective(
                context,
                approved=approved,
                journal=journal,
                plan=object(),
                local={},
                targets=targets,
                credentials=credentials,
                ssh_policy=ssh_policy,
                observe=lambda: cloud,
                command_scope={},
            )
        except RuntimeError as error:
            assert str(error) == "lost local acknowledgement"
            state = store.read(
                expected_project_id=initial.project_id, expected_gateway_name=initial.gateway_name
            )
    assert (
        dict(state.transaction.resource_bindings)[corrective.RESERVED_KEY]
        == state.transaction.operation_id
    )
    assert state.has_same_identity(initial)
    assert corrective.authority(state)["prior_record"] == initial.record_sha256
    assert set(trace) == {"node-0", "node-1"}
    assert trace[0] == "node-1"
    from nebius_vpngw.deploy.vm_ha_lifecycle import normalize_vm_ha_observation

    assert state.transaction.observation == normalize_vm_ha_observation(cloud)
    assert corrective.OBSERVED_EFFECT in state.transaction.completed_effects
    assert plan(state) is None


@pytest.mark.parametrize("change", ["none", "before", "during"])
def test_corrective_cloud_binding_rejects_drift_and_feeds_real_activation(tmp_path, change):
    from unittest.mock import patch

    from test_vm_manager_allocations import _ha_spec, _install_vm_ha_activating_journal

    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleJournal
    from nebius_vpngw.deploy.vm_manager import VMManager

    manager = VMManager(project_id="project-1", region="eu-north1")
    journal, cloud, binding = _install_vm_ha_activating_journal(
        manager, tmp_path, pending_effect="verify-active-forwarding-and-routes"
    )
    state = journal.state
    state = replace(
        state,
        transaction=replace(
            state.transaction,
            resource_bindings=tuple(
                sorted({**dict(state.transaction.resource_bindings), ARTIFACT: "a" * 64}.items())
            ),
        ),
    )
    approval = {**record(state), "owner": "node-a", "cloud": corrective.digest(cloud)}
    state = corrective.successor(state, approval, bindings={})
    state = state.begin_effect("reserve-corrective-pair").complete_effect(
        "reserve-corrective-pair", resource_updates={corrective.RESERVED_KEY: approval["operation"]}
    )
    store = VMHALifecycleStore(tmp_path / "corrective.json")
    store.path.write_text(json.dumps(state.to_dict()))
    store.path.chmod(0o600)
    journal = VMHALifecycleJournal(store, state)
    manager._vm_ha_journal = journal
    changed = {**cloud, "unexpected": True}
    observations = iter(
        [changed if change == "before" else cloud, changed if change == "during" else cloud]
    )
    if change != "none":
        with pytest.raises(ValueError, match="snapshot changed|unapproved observation"):
            corrective.bind_cloud_observation(journal, lambda: next(observations))
        assert corrective.OBSERVED_EFFECT not in journal.state.transaction.completed_effects
        if change == "before":
            assert journal.state == state
        return
    corrective.bind_cloud_observation(journal, lambda: next(observations))
    with (
        patch.object(manager, "observe_vm_ha_migration_state", return_value=cloud),
        patch.object(manager, "_build_sdk_client", return_value=object()),
        patch.object(manager, "_build_vm_ha_runtime_binding", return_value=binding),
    ):
        result = manager.resume_vm_ha_activation(_ha_spec(), ["10.0.0.0/8"])
    assert result.vm_ha_runtime_binding is binding


def test_unused_corrective_prelude_retains_original_complete_child_lineage(tmp_path):
    original = failed_release()
    first = corrective.successor(original, record(original), bindings={})
    first = first.begin_effect("reserve-corrective-node-1")
    assert corrective.unused_prelude(first)
    old = corrective.authority(first)
    value = record(first)
    value.update(
        artifact="9" * 64,
        operation=corrective.operation_id(first, "9" * 64),
        children=old["children"],
        locks=old["locks"],
        hosts=old["hosts"],
    )
    next_cycle = corrective.successor(first, value, bindings={})
    assert corrective.authority(next_cycle)["prior_record"] == first.record_sha256
    assert corrective.authority(next_cycle)["children"] == old["children"]
    assert next_cycle.has_same_identity(original)
    changed = copy.deepcopy(value)
    changed["children"]["node-1"]["ordinary"]["digest"] = "0" * 64
    with pytest.raises(ValueError, match="original guest authority"):
        corrective.successor(first, changed, bindings={})
    first_done = first.complete_effect("reserve-corrective-node-1")
    assert not corrective.unused_prelude(first_done)
    with pytest.raises(ValueError, match="host-only failed release"):
        corrective.operation_id(first_done, "9" * 64)


def test_streamed_corrective_admission_uses_bounded_installed_entrypoint():
    import subprocess
    import sys

    from nebius_vpngw.handoff_bootstrap import streamed_source

    script = (
        streamed_source()
        + """
from types import SimpleNamespace
from pathlib import Path
import _vpngw_handoff_remote as h
import nebius_vpngw.checkpoint_upgrade as checkpoint
h.ops.BOOT=SimpleNamespace(read_text=lambda:'boot')
h.repair.STATE=Path('/fixture')
h.repair.FORWARDING=SimpleNamespace(read_text=lambda:'0')
h.repair.exact_lock=lambda expected:expected
h.repair.read_json=lambda p: {'schema':h.repair.STATUS_SCHEMA,'guard_boot_id':'boot','data_plane_mode':'blocked'} if p.name=='guard.json' else {}
checkpoint.inspect_checkpoint=lambda value:SimpleNamespace(pending_action=None,transfer_continuity=None,repair_attempt=None)
seen=[]
h.subprocess.run=lambda args,**kwargs: seen.append((args,kwargs['timeout'])) or SimpleNamespace(returncode=0)
h.corrective_fence({})
assert seen[0][0][-2:]==['--vm-ha-apply-lock-quiescent','/fixture/apply.lock']
assert seen[0][1]==30
print('bounded installed admission passed')
"""
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import sys;exec(sys.stdin.read())"],
        input=script,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "bounded installed admission passed"


@pytest.mark.parametrize(
    "canonical,names,allowed",
    [
        ("strongswan.service", ["strongswan.service", "strongswan-swanctl.service"], True),
        ("strongswan.service", ["strongswan.service"], False),
        (
            "systemd-networkd.service",
            ["systemd-networkd.service", "strongswan-swanctl.service"],
            False,
        ),
        ("strongswan.service", "strongswan-swanctl.service", False),
    ],
)
def test_service_alias_is_proved_before_intent_and_only_within_daemon(
    monkeypatch, ordinary_operation_guest, canonical, names, allowed
):
    import time
    from unittest.mock import Mock

    from nebius_vpngw import ordinary_operations as ops

    manager = ops.Manager(time.monotonic() + 60)
    monkeypatch.setattr(manager, "unit", lambda _: {"Id": canonical, "Names": names})
    operation = ops.Operation({"phase": "quiesce", "purpose": "ha-handoff"}, manager, Mock())
    operation.intent = Mock(return_value=0)
    operation.update = Mock()
    operation.check = Mock()
    operation.value["effects"] = [{"kind": "unit"}]
    system = Mock(
        return_value={
            "type": "uososa(uosos)",
            "data": [1, "/job/1", "strongswan.service", "/unit/1", "stop", []],
        }
    )
    monkeypatch.setattr(manager, "system", system)
    if allowed:
        operation.service("stop", "strongswan-swanctl.service")
        operation.intent.assert_called_once_with("unit", unit="strongswan.service", action="stop")
        assert system.call_args.args[2] == "strongswan.service"
    else:
        with pytest.raises(ops.OperationBlocked, match="alias_unproven"):
            operation.service("stop", "strongswan-swanctl.service")
        operation.intent.assert_not_called()
        system.assert_not_called()
