"""Owner origination cannot weaken forwarding, ownership, or remote-route gates."""

import fcntl
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.unit.test_vm_ha_controller import DIGEST, DIGESTS, _owned_cloud, _peer, _snapshot

from nebius_vpngw.agent.vm_ha.runtime import CommandResult, SystemDataPlaneRuntime
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    ConfiguredRole,
    ControllerAction,
    ControllerCheckpoint,
    DataPlaneMode,
    LocalReadiness,
    VMHAController,
)


def owner(**changes):
    snapshot = _snapshot(
        configured_role=ConfiguredRole.ACTIVE,
        cloud=_owned_cloud(),
        peer_heartbeat=_peer(configured_role="passive"),
        readiness=LocalReadiness(
            True,
            True,
            False,
            False,
            protection_ready=True,
            origination_required=True,
        ),
    )
    return replace(snapshot, **changes)


@pytest.mark.parametrize("protected", [False, True])
def test_owner_originates_before_remote_routes_but_never_enables_forwarding(protected):
    controller = VMHAController(peer_timeout_seconds=10, suspicion_seconds=10)
    snapshot = owner(readiness=replace(owner().readiness, protection_ready=protected))
    decision = controller.decide(snapshot, ControllerCheckpoint())
    assert decision.action.kind is ActionKind.PREPARE_OWNER_ORIGINATION
    assert not decision.forwarding_enabled
    replay = controller.decide(snapshot, decision.checkpoint)
    assert replay.action == decision.action  # The same fenced install owns protection.
    assert decision.action.takeover_fence_required is False  # Initial configured owner.
    originating = owner(
        data_plane_mode=DataPlaneMode.ORIGINATING,
        readiness=replace(owner().readiness, origination_ready=True),
    )
    waiting = controller.decide(originating, decision.checkpoint)
    assert waiting.action is None
    assert "bgp-not-ready" in waiting.reasons
    assert not waiting.forwarding_enabled
    ready = replace(
        originating, readiness=replace(originating.readiness, bgp_ready=True, xfrm_ready=True)
    )
    reconcile = controller.decide(ready, waiting.checkpoint)
    assert reconcile.action.kind is ActionKind.RECONCILE_ROUTES
    assert not reconcile.forwarding_enabled


@pytest.mark.parametrize(
    "fault", ["inhibition", "lock", "generation", "unknown-cloud", "lost-owner", "policy"]
)
def test_origination_revokes_on_authority_or_policy_loss(fault):
    snapshot = owner(
        data_plane_mode=DataPlaneMode.ORIGINATING,
        readiness=replace(owner().readiness, origination_ready=True),
    )
    if fault == "inhibition":
        snapshot = replace(snapshot, transfer_inhibited=True)
    elif fault == "lock":
        snapshot = replace(snapshot, apply_locked=True)
    elif fault == "generation":
        snapshot = replace(snapshot, peer_heartbeat=_peer(generation_id="f" * 64))
    elif fault == "unknown-cloud":
        snapshot = replace(snapshot, cloud=replace(snapshot.cloud, authoritative=False))
    elif fault == "lost-owner":
        snapshot = replace(
            snapshot,
            cloud=replace(
                snapshot.cloud, observed_owner_node_id="node-a", candidate_attachment_exact=False
            ),
        )
    else:
        snapshot = replace(snapshot, readiness=replace(snapshot.readiness, origination_ready=False))
    result = VMHAController(peer_timeout_seconds=10, suspicion_seconds=10).decide(
        snapshot, ControllerCheckpoint()
    )
    assert result.action.kind is ActionKind.DISABLE_ACTIVE
    assert not result.forwarding_enabled


@pytest.mark.parametrize(
    "fault", ["lock", "inhibition", "generation", "cloud", "service", "sample", "hygiene", "defect"]
)
def test_unprotected_origination_keeps_initial_and_replay_authority_gates(fault):
    controller = VMHAController(peer_timeout_seconds=10, suspicion_seconds=10)
    snapshot = owner(readiness=replace(owner().readiness, protection_ready=False))
    pending = controller.decide(snapshot, ControllerCheckpoint())
    assert pending.action.kind is ActionKind.PREPARE_OWNER_ORIGINATION
    if fault == "lock":
        snapshot = replace(snapshot, apply_locked=True)
    elif fault == "inhibition":
        snapshot = replace(snapshot, transfer_inhibited=True)
    elif fault == "generation":
        snapshot = replace(snapshot, peer_heartbeat=_peer(generation_id="f" * 64))
    elif fault == "cloud":
        snapshot = replace(snapshot, cloud=replace(snapshot.cloud, authoritative=False))
    else:
        change = {
            "service": {"service_healthy": False},
            "sample": {"sample_complete": False},
            "hygiene": {"routing_hygiene_ready": False},
            "defect": {"local_defects": ("daemon-failed:strongswan",)},
        }[fault]
        snapshot = replace(snapshot, readiness=replace(snapshot.readiness, **change))
    for checkpoint in (ControllerCheckpoint(), pending.checkpoint):
        result = controller.decide(snapshot, checkpoint)
        assert (
            result.action is None or result.action.kind is not ActionKind.PREPARE_OWNER_ORIGINATION
        )
        assert not result.forwarding_enabled


@pytest.mark.parametrize("failure", [None, "protection", "policy", "authority", "persistence"])
def test_runtime_origination_stays_fenced_and_rolls_back(tmp_path, failure):
    calls = []
    forwarding = "0"
    exports = False
    authority_reads = 0

    def run(argv, timeout):
        nonlocal forwarding
        name = Path(argv[0]).name
        if name == "sysctl":
            if "-w" in argv:
                forwarding = argv[-1].split("=")[-1]
            return CommandResult(0, forwarding)
        if name == "swanctl":
            return CommandResult(0, "")
        raise AssertionError(argv)

    def prepare():
        nonlocal exports
        with (tmp_path / "lock").open("r+") as lock, pytest.raises(BlockingIOError):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert forwarding == "0"
        assert "protect" in calls
        exports = True
        calls.append("prepare")

    def withdraw():
        nonlocal exports
        exports = False
        calls.append("withdraw")

    def revalidate():
        nonlocal authority_reads
        authority_reads += 1
        if failure == "authority" and authority_reads == 3:
            raise RuntimeError("ownership changed")

    def install_protection():
        assert forwarding == "0"
        if failure == "protection":
            raise RuntimeError("protection installation failed")
        calls.append("protect")

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "state",
        guard_path=tmp_path / "guard",
        routing_lock_path=tmp_path / "lock",
        configured_bgp_sessions=("169.254.1.2",),
        expected_bgp_policy_digest="c" * 64,
        active_preparer=prepare,
        origination_verifier=lambda: exports and failure != "policy",
        runner=run,
        destination_protection=SimpleNamespace(install=install_protection, verify=lambda: None),
    )
    runtime._withdraw_bgp_exports_locked = withdraw
    action = ControllerAction(
        ActionKind.PREPARE_OWNER_ORIGINATION,
        "op",
        "boot",
        "node",
        "allocation",
        "42",
        DIGEST,
        DIGESTS,
    )
    runtime._write_state(action, DataPlaneMode.PASSIVE)
    runtime._write_guard(action, DataPlaneMode.PASSIVE)
    write_guard = runtime._write_guard

    def guard(action, mode):
        if mode is DataPlaneMode.ORIGINATING and failure == "persistence":
            raise OSError("write failed")
        write_guard(action, mode)

    runtime._write_guard = guard
    if failure:
        with pytest.raises((RuntimeError, OSError)):
            runtime.prepare_owner_origination(action, revalidate)
        assert runtime.mode() is DataPlaneMode.BLOCKED
        assert not exports
        if failure == "protection":
            assert "prepare" not in calls
        assert json.loads((tmp_path / "guard").read_text())["data_plane_mode"] == "blocked"
    else:
        runtime.prepare_owner_origination(action, revalidate)
        assert runtime.mode() is DataPlaneMode.ORIGINATING
        assert exports
        assert authority_reads == 3
        assert runtime._expected_exports_for_mode(DataPlaneMode.PASSIVE) == {
            "169.254.1.2": frozenset()
        }
    assert forwarding == "0"
