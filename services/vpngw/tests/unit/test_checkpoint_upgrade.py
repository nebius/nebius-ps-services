from __future__ import annotations

import json
from dataclasses import replace

import pytest

from nebius_vpngw import checkpoint_upgrade as upgrade
from nebius_vpngw import ha_repair as repair
from nebius_vpngw.agent.vm_ha_checkpoint import (
    controller_checkpoint_from_dict,
    controller_checkpoint_to_dict,
)
from nebius_vpngw.agent.vm_ha_controller import ControllerCheckpoint, HAState


@pytest.mark.parametrize("version", [5, 6])
def test_predecessor_active_repair_gains_explicit_mode_without_renewing_budget(version):
    from tests.unit.test_vm_ha_controller import _owned_cloud, _snapshot

    from nebius_vpngw.agent.vm_ha_controller import DataPlaneMode, VMHAController

    snapshot = _snapshot(cloud=_owned_cloud(), data_plane_mode=DataPlaneMode.ACTIVE)
    snapshot = replace(snapshot, readiness=replace(snapshot.readiness, service_healthy=False))
    checkpoint = (
        VMHAController(peer_timeout_seconds=10, suspicion_seconds=5)
        ._repair_action(snapshot, ControllerCheckpoint())
        .checkpoint
    )
    if version == 6:
        checkpoint = replace(checkpoint, path_transfer_destinations=("192.0.2.0/24",))
    raw = controller_checkpoint_to_dict(checkpoint)
    raw["schema"] = upgrade.PREFIX + str(version)
    raw["pending_action"].pop("repair_mode")
    raw["repair_attempt"].pop("repair_mode")
    if version == 5:
        raw.pop("path_transfer_destinations")
    with pytest.raises(ValueError, match="unsupported"):
        controller_checkpoint_from_dict(raw)
    assert upgrade.inspect_checkpoint(raw) == checkpoint
    assert "repair_mode" not in raw["repair_attempt"]


def predecessor(version=4):
    value = controller_checkpoint_to_dict(ControllerCheckpoint(sequence=7, state=HAState.SUSPECT))
    value.pop("suspicion_boot_id")
    value.pop("path_transfer_destinations")
    value.update(schema=upgrade.PREFIX + str(version), suspect_since=999999.0)
    if version < 4:
        value.pop("repair_attempt")
    if version < 2:
        value.pop("transfer_continuity")
    return value


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_legacy_reading_is_deployment_only_and_clears_only_suspicion(version):
    raw = predecessor(version)
    with pytest.raises(ValueError, match="unsupported"):
        controller_checkpoint_from_dict(raw)
    checkpoint = upgrade.inspect_checkpoint(raw)
    assert checkpoint == ControllerCheckpoint(sequence=7, state=HAState.SUSPECT)
    assert raw["suspect_since"] == 999999.0


@pytest.mark.parametrize("state", [HAState.OWNERSHIP_TRANSFER, HAState.PROMOTING])
def test_lossy_v1_conversion_refused(state):
    raw = predecessor(1)
    raw["state"] = state.value
    with pytest.raises(ValueError, match="lacks transfer"):
        upgrade.inspect_checkpoint(raw)


@pytest.mark.parametrize(
    "time,boot",
    [(1.0, None), (None, "boot"), (-1, "boot"), (True, "boot"), (float("inf"), "boot"), (1.0, "")],
)
def test_runtime_suspicion_pair_is_strict(time, boot):
    with pytest.raises(ValueError):
        replace(ControllerCheckpoint(), suspect_since=time, suspicion_boot_id=boot)


@pytest.fixture
def conversion(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    root = tmp_path.resolve()
    # Production runs as root under root-owned ancestors. Model that directory
    # ownership here without replacing the atomic writer or regular-file checks.
    lstat = Path.lstat
    ancestors = set(root.parents)

    def metadata(path, *args, **kwargs):
        value = lstat(path, *args, **kwargs)
        if path in ancestors:
            fields = list(value)
            fields[0] &= ~0o022
            fields[4] = os.geteuid()
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(Path, "lstat", metadata)
    boot = root / "boot"
    forwarding = root / "forwarding"
    boot.write_text("boot-a")
    forwarding.write_text("0")
    monkeypatch.setattr(repair, "BOOT", boot)
    monkeypatch.setattr(repair, "FORWARDING", forwarding)
    expected = dict(
        cluster_id="cluster",
        node_id="node",
        generation_id="d" * 64,
        operation_id="a" * 64,
        host_id="host",
    )
    repair.publish(
        root / "apply.lock",
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
                "apply_locked": True,
                **{k: v for k, v in expected.items() if k != "host_id"},
            }
        ).encode(),
    )
    repair.publish(
        root / "guard.json",
        json.dumps(
            {
                "schema": repair.STATUS_SCHEMA,
                "guard_boot_id": "boot-a",
                "data_plane_mode": "blocked",
            }
        ).encode(),
    )
    repair.publish(root / "controller-checkpoint.json", json.dumps(predecessor()).encode())
    return dict(
        expected=expected, artifact="b" * 64, state_dir=root, boot_id="boot-a", repair=repair
    )


def test_conversion_replays_before_and_after_atomic_checkpoint_publish(conversion, monkeypatch):
    root = conversion["state_dir"]
    publish = repair.publish

    def interrupt(path, raw, mode=0o600):
        if path.name == "controller-checkpoint.json":
            raise OSError("simulated crash")
        publish(path, raw, mode)

    monkeypatch.setattr(repair, "publish", interrupt)
    with pytest.raises(OSError):
        upgrade.convert_checkpoint(**conversion)
    receipt = repair.read_json(root / "checkpoint-conversion.json")
    assert repair.read_json(root / "controller-checkpoint.json")["schema"].endswith("v4")
    monkeypatch.setattr(repair, "publish", publish)
    assert upgrade.convert_checkpoint(**conversion) == receipt
    assert upgrade.convert_checkpoint(**conversion) == receipt
    current = controller_checkpoint_from_dict(repair.read_json(root / "controller-checkpoint.json"))
    assert current == ControllerCheckpoint(sequence=7, state=HAState.SUSPECT)
    with pytest.raises(RuntimeError, match="binding changed"):
        upgrade.convert_checkpoint(**{**conversion, "artifact": "c" * 64})


def test_conversion_rejects_unknown_state_after_receipt(conversion, monkeypatch):
    root = conversion["state_dir"]
    upgrade.convert_checkpoint(**conversion)
    value = repair.read_json(root / "controller-checkpoint.json")
    value["sequence"] += 1
    repair.publish(root / "controller-checkpoint.json", json.dumps(value).encode())
    with pytest.raises(RuntimeError, match="hash changed"):
        upgrade.convert_checkpoint(**conversion)


def test_conversion_rejects_missing_started_checkpoint_or_wrong_boot(conversion):
    with pytest.raises(RuntimeError, match="forwarding exclusion"):
        upgrade.convert_checkpoint(**{**conversion, "boot_id": "different"})
    (conversion["state_dir"] / "controller-checkpoint.json").unlink()
    with pytest.raises(RuntimeError, match="first-start authority"):
        upgrade.convert_checkpoint(**conversion)


def test_private_bootstrap_keeps_installed_package_untouched():
    import sys

    import nebius_vpngw
    from nebius_vpngw.deploy.checkpoint_conversion import source

    namespace = {}
    exec(source(), namespace)
    assert sys.modules["nebius_vpngw"] is nebius_vpngw
    assert namespace["checkpoint_upgrade"].inspect_checkpoint(predecessor()).sequence == 7


def test_boolean_sequence_is_not_lossless_integer():
    value = predecessor()
    value["sequence"] = True
    with pytest.raises(ValueError, match="losslessly"):
        upgrade.inspect_checkpoint(value)


def test_runtime_write_completes_conversion_but_read_does_not(conversion, monkeypatch):
    from nebius_vpngw.agent import main

    root = conversion["state_dir"]
    receipt = upgrade.convert_checkpoint(**conversion)
    store = main.VMHACheckpointFileStore(root / "controller-checkpoint.json")
    checkpoint = store.load()
    assert repair.read_json(root / "checkpoint-conversion.json") == receipt
    monkeypatch.setattr(main, "_boot_id", lambda: "boot-a")
    store.save(replace(checkpoint, sequence=checkpoint.sequence + 1))
    assert repair.read_json(root / "checkpoint-conversion.json")["phase"] == "completed"
    # A later same-config apply can change artifact/boot after real activation.
    conversion["repair"].BOOT.write_text("boot-b")
    guard = repair.read_json(root / "guard.json")
    guard["guard_boot_id"] = "boot-b"
    repair.publish(root / "guard.json", json.dumps(guard).encode())
    completed = upgrade.convert_checkpoint(
        **{**conversion, "boot_id": "boot-b", "artifact": "e" * 64}
    )
    assert completed["phase"] == "completed"
    assert store.load().sequence == 8


def test_peer_evidence_accepts_only_exact_conversion_receipt(conversion):
    from nebius_vpngw.ha_repair import peer_checkpoint_transition

    root = conversion["state_dir"]
    before = repair.fencing_evidence(conversion["expected"], state_dir=root)
    upgrade.convert_checkpoint(**conversion)
    after = repair.fencing_evidence(conversion["expected"], state_dir=root)
    assert peer_checkpoint_transition(before, after, conversion["artifact"])
    assert not peer_checkpoint_transition(before, after, "f" * 64)
    assert not peer_checkpoint_transition(
        before, {**after, "boot_id": "foreign"}, conversion["artifact"]
    )
    assert not peer_checkpoint_transition(
        before, {**after, "checkpoint_sha256": "f" * 64}, conversion["artifact"]
    )


def test_started_runtime_missing_checkpoint_fails_closed(tmp_path):
    from nebius_vpngw.agent.main import VMHACheckpointFileStore

    (tmp_path / "status.json").write_text("{}")
    with pytest.raises(ValueError, match="checkpoint is missing"):
        VMHACheckpointFileStore(tmp_path / "controller-checkpoint.json").load()


def test_pending_v5_cannot_be_adopted_by_foreign_operation(conversion):
    upgrade.convert_checkpoint(**conversion)
    expected = {**conversion["expected"], "operation_id": "f" * 64}
    path = conversion["state_dir"] / "apply.lock"
    lock = repair.read_json(path)
    lock["operation_id"] = expected["operation_id"]
    repair.publish(path, json.dumps(lock).encode())
    with pytest.raises(RuntimeError, match="binding changed"):
        upgrade.convert_checkpoint(**{**conversion, "expected": expected})


def test_preinstall_rejects_completed_receipt_with_legacy_state(conversion):
    root = conversion["state_dir"]
    receipt = upgrade.convert_checkpoint(**conversion)
    repair.publish(
        root / "checkpoint-conversion.json", json.dumps({**receipt, "phase": "completed"}).encode()
    )
    repair.publish(root / "controller-checkpoint.json", json.dumps(predecessor()).encode())
    with pytest.raises(ValueError, match="unsupported"):
        upgrade.conversion_preflight(**conversion)


def test_completed_previous_release_can_upgrade_again_under_new_authority(conversion):
    root = conversion["state_dir"]
    receipt = upgrade.convert_checkpoint(**conversion)
    prior = json.dumps({**receipt, "phase": "completed"}).encode()
    repair.publish(root / "checkpoint-conversion.json", prior)
    repair.publish(root / "controller-checkpoint.json", json.dumps(predecessor()).encode())
    next_release = {**conversion, "artifact": "e" * 64}
    assert upgrade.conversion_preflight(**next_release)
    result = upgrade.convert_checkpoint(**next_release)
    assert result["phase"] == "pending" and result["artifact"] == "e" * 64
    assert repair.read_json(root / "controller-checkpoint.json")["schema"] == upgrade.SCHEMA
    archive = root / f"checkpoint-conversion-{repair.sha(prior)}.completed.json"
    assert archive.read_bytes() == prior
    assert upgrade.convert_checkpoint(**next_release) == result


def test_preinstall_rejects_pending_artifact_change(conversion):
    upgrade.convert_checkpoint(**conversion)
    with pytest.raises(RuntimeError, match="binding changed"):
        upgrade.conversion_preflight(**{**conversion, "artifact": "e" * 64})


@pytest.mark.parametrize("writers_stopped", [False, True])
def test_package_transport_excludes_writers_before_conversion(
    conversion, monkeypatch, writers_stopped
):
    import contextlib
    from types import SimpleNamespace

    from nebius_vpngw import ordinary_operations as ops
    from nebius_vpngw.deploy import checkpoint_conversion as transport

    root = conversion["state_dir"]
    checkpoint = root / "controller-checkpoint.json"
    before = checkpoint.read_bytes()
    events = []

    @contextlib.contextmanager
    def locks(**_kwargs):
        events.append("locked")
        try:
            yield
        finally:
            events.append("unlocked")

    class Manager:
        def __init__(self, _deadline):
            pass

        def stopped(self, unit):
            assert events[-1] != "unlocked" and events[0] == "locked"
            events.append(unit)
            return writers_stopped

    original_source, bootstrap = transport.source, ops.streamed_bootstrap
    monkeypatch.setattr(
        transport,
        "source",
        lambda: (
            original_source()
            + "checkpoint_repair.STATE=_test_state\n"
            + "checkpoint_repair.BOOT=_test_state/'boot'\n"
            + "checkpoint_repair.FORWARDING=_test_state/'forwarding'\n"
            + "checkpoint_repair.writer_locks=_test_locks\n"
        ),
    )
    monkeypatch.setattr(
        ops,
        "streamed_bootstrap",
        lambda: (
            bootstrap()
            + "import _vpngw_ordinary_operations\n"
            + "_vpngw_ordinary_operations.Manager=_test_manager\n"
        ),
    )

    def inspect(_target, _hostname, _local, *, script):
        exec(
            script,
            {"_test_state": root, "_test_locks": locks, "_test_manager": Manager},
        )

    def invoke(publish):
        transport.guard_package(
            SimpleNamespace(_inspect_vm_ha_host=inspect),
            "example",
            SimpleNamespace(hostname="host"),
            {},
            artifact=conversion["artifact"],
            boot_id=conversion["boot_id"],
            expected=conversion["expected"],
            publish=publish,
        )

    if not writers_stopped:
        with pytest.raises(RuntimeError, match="writers are active"):
            invoke(True)
        assert checkpoint.read_bytes() == before
        assert not (root / "checkpoint-conversion.json").exists()
        return
    invoke(False)
    assert checkpoint.read_bytes() == before
    assert not (root / "checkpoint-conversion.json").exists()
    events.clear()
    invoke(True)
    assert set((*ops.MANAGEMENT, *ops.HA_WRITERS)).issubset(events)
    assert events[-1] == "unlocked"
    assert repair.read_json(checkpoint)["schema"] == upgrade.SCHEMA

    # The activation barrier verifies a prepared package without converting it
    # again. Retain its lock authority until activation completes the receipt.
    from nebius_vpngw.deploy import ordinary_apply, vm_ha_package

    manifest = {
        "wheel_sha256": conversion["artifact"],
        "files": {},
        "assets": {},
        "version": "1",
        "expected_dependencies": {},
        "expected_requirements": [],
    }
    observation = {
        "boot_id": conversion["boot_id"],
        "config_sha256": "c" * 64,
        "ha_config_digest": "d" * 64,
        "files": {},
        "assets": {},
        "asset_modes": {},
        "package_version": "1",
        "dependencies": {},
        "requirements": [],
    }
    monkeypatch.setattr(ordinary_apply, "remote", lambda *a: {"observation": observation})
    plan = SimpleNamespace(
        target="example",
        instance=SimpleNamespace(hostname="host"),
        manifest=manifest,
        observation=observation,
    )
    prepared = checkpoint.read_bytes()
    vm_ha_package.verify_prepared(
        SimpleNamespace(_inspect_vm_ha_host=inspect),
        plan,
        {},
        checkpoint_conversion=conversion["expected"],
        convert=False,
    )
    assert checkpoint.read_bytes() == prepared
    assert repair.read_json(root / "checkpoint-conversion.json")["phase"] == "pending"


@pytest.mark.parametrize("obstacle", [None, "authority", "marker", "status", "writer", "boot"])
def test_fresh_activation_initializes_before_marker_and_rejects_started_state(
    conversion, monkeypatch, obstacle
):
    import contextlib
    from types import SimpleNamespace

    from nebius_vpngw import ordinary_operations as ops

    root = conversion["state_dir"]
    checkpoint = root / "controller-checkpoint.json"
    checkpoint.unlink()
    marker = root / "vm-ha-enabled"
    events = []
    if obstacle == "marker":
        marker.touch()
    if obstacle == "status":
        repair.publish(root / "status.json", b"{}")
    if obstacle == "boot":
        repair.BOOT.write_text("foreign")

    @contextlib.contextmanager
    def locks(**_kwargs):
        events.append("locked")
        yield
        events.append("unlocked")

    monkeypatch.setattr(repair, "writer_locks", locks)
    monkeypatch.setattr(
        ops, "Manager", lambda _: SimpleNamespace(stopped=lambda _: obstacle != "writer")
    )
    cold_guard = repair.cold_guard

    def guard(**kwargs):
        assert events == ["locked"] and not marker.exists() and not checkpoint.exists()
        events.append("guard")
        cold_guard(**kwargs, runner=lambda *a, **k: SimpleNamespace(returncode=0))

    monkeypatch.setattr(repair, "cold_guard", guard)
    if obstacle:
        with pytest.raises(RuntimeError):
            upgrade.admit_activation(**conversion, unstarted=obstacle != "authority", marker=marker)
        assert not checkpoint.exists()
        assert "guard" not in events
        return
    upgrade.admit_activation(**conversion, unstarted=True, marker=marker)
    assert events == ["locked", "guard", "unlocked"]
    assert controller_checkpoint_from_dict(repair.read_json(checkpoint)) == ControllerCheckpoint()
    initial = checkpoint.read_bytes()
    repair.BOOT.write_text("boot-b")
    events.clear()

    def retry_guard(**kwargs):
        assert events == ["locked"] and not marker.exists()
        events.append("guard")
        cold_guard(**kwargs, runner=lambda *a, **k: SimpleNamespace(returncode=0))

    monkeypatch.setattr(repair, "cold_guard", retry_guard)
    conversion = {**conversion, "boot_id": "boot-b"}
    upgrade.admit_activation(**conversion, unstarted=True, marker=marker)
    assert checkpoint.read_bytes() == initial
    assert repair.read_json(root / "guard.json")["guard_boot_id"] == "boot-b"
    assert events == ["locked", "guard", "unlocked"]
    marker.touch()
    events.clear()
    upgrade.admit_activation(**conversion, unstarted=False, marker=marker)
    assert not events  # Existing v5 is admitted without reseeding or restarting the guard.


@pytest.mark.parametrize("replacement", [None, "failed", "missing"])
def test_first_start_requires_current_creation_and_never_reuses_historical_authority(replacement):
    from types import SimpleNamespace as NS

    from nebius_vpngw.deploy.checkpoint_conversion import first_start_authorized
    from nebius_vpngw.deploy.vm_ha_lifecycle import (
        VMHALifecycleStatus,
        vm_ha_missing_standby_replacement_effect,
        vm_ha_passive_replacement_binding_key,
        vm_ha_passive_replacement_effect,
    )

    artifact = "a" * 64
    instance = NS(
        hostname="gateway", vm_ha_node=NS(node_id="node"), vm_ha_generation=NS(generation_id="gen")
    )
    binding = NS(
        cluster_id="cluster", generation_id="gen", nodes=[NS(node_id="node", compute_id="current")]
    )
    resources = {"heartbeat-initial-artifact": artifact, "compute:gateway": "current"}
    effects = ["provision-gateway-compute"]
    if replacement:
        resources["compute:gateway"] = "retired"
        resources[vm_ha_passive_replacement_binding_key("approval", "gateway", 2)] = "b" * 64
        resources[vm_ha_passive_replacement_binding_key("retired-compute", "gateway", 2)] = (
            "retired"
        )
        resources[vm_ha_passive_replacement_binding_key("compute", "gateway", 2)] = "current"
        effect = (
            vm_ha_passive_replacement_effect
            if replacement == "failed"
            else vm_ha_missing_standby_replacement_effect
        )
        effects.append(effect("gateway", 2, "create-compute"))
    tx = NS(resource_bindings=resources, completed_effects=effects)
    state = NS(
        status=VMHALifecycleStatus.ACTIVATING,
        transaction=tx,
        cluster_id="cluster",
        members=[NS(node_id="node", instance_name="gateway")],
    )
    assert first_start_authorized(state, instance, binding, artifact)
    assert not first_start_authorized(state, instance, binding, "c" * 64)
    effects.append("activate-node")
    assert not first_start_authorized(state, instance, binding, artifact)
    effects.pop()
    effects.pop()  # Latest exact creation receipt is mandatory, including on resume.
    assert not first_start_authorized(state, instance, binding, artifact)


@pytest.mark.parametrize("checkpoint_exists", [False, True])
def test_first_start_retry_quiesces_reboot_started_management_before_writer_locks(
    conversion, monkeypatch, checkpoint_exists
):
    import contextlib

    from nebius_vpngw import ordinary_operations as ops

    root = conversion["state_dir"]
    checkpoint = root / "controller-checkpoint.json"
    if checkpoint_exists:
        initial = controller_checkpoint_to_dict(ControllerCheckpoint(sequence=4))
        repair.publish(checkpoint, json.dumps(initial).encode())
    else:
        checkpoint.unlink()
    events = []
    running = {"nebius-vpngw-agent.service"}

    class Manager:
        def __init__(self, _deadline):
            pass

        def stopped(self, unit):
            return unit not in running

        def command(self, argv, *, limit):
            assert argv == ["systemctl", "--job-mode=replace-irreversibly", "stop", *ops.MANAGEMENT]
            assert limit <= 20
            assert events == []  # Stopping must not wait behind its own routing lock.
            events.append("stop-management")
            running.clear()
            return ""

    @contextlib.contextmanager
    def locks(**_kwargs):
        assert not running
        events.append("locked")
        yield

    def guard(**kwargs):
        assert events == ["stop-management", "locked"]
        events.append("cold-guard")

    monkeypatch.setattr(ops, "Manager", Manager)
    monkeypatch.setattr(repair, "writer_locks", locks)
    monkeypatch.setattr(repair, "cold_guard", guard)
    upgrade.admit_activation(**conversion, unstarted=True, marker=root / "vm-ha-enabled")
    assert events == ["stop-management", "locked", "cold-guard"]
    current = controller_checkpoint_from_dict(repair.read_json(checkpoint))
    assert current.sequence == (4 if checkpoint_exists else 0)
