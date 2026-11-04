from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from tests.unit.test_vm_ha_agent_runtime import _runtime_config
from tests.unit.test_vm_ha_controller import _snapshot

from nebius_vpngw.agent.main import (
    DefaultVMHAControllerRuntime,
    VMHACheckpointFileStore,
    VMHACloudObservationUnavailable,
    VMHAControllerRuntime,
    VMHAEffectAdapter,
    VMHASnapshotAdapter,
    VMHASnapshotProviders,
    _run_vm_ha_controller,
)
from nebius_vpngw.agent.vm_ha.heartbeat import HealthSample, HeartbeatSnapshots, OwnershipSample
from nebius_vpngw.agent.vm_ha.runtime import VMHARuntimePorts
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ControllerAction,
    ControllerCheckpoint,
    DataPlaneMode,
    HAState,
    LocalReadiness,
    VMHAController,
)
from nebius_vpngw.deploy.vm_ha_cloud import (
    AmbiguousHACloudError,
    PermanentHACloudError,
    RetryableHACloudError,
)
from nebius_vpngw.nebius_pagination import NebiusPaginationError
from nebius_vpngw.schema import VMHARuntimeBinding


@pytest.mark.parametrize("mode", list(DataPlaneMode))
@pytest.mark.parametrize("previous_observation", [False, True])
@pytest.mark.parametrize("failure_provider", ["cloud", "routes"])
@pytest.mark.parametrize("error_kind", ["classified", "raw-timeout", "paginated-timeout"])
def test_read_retries_preserve_guard_forwarding_and_checkpoint(
    tmp_path: Path,
    mode: DataPlaneMode,
    previous_observation: bool,
    failure_provider: str,
    error_kind: str,
) -> None:
    guard = {"guard_boot_id": "boot-a", "data_plane_mode": mode.value}
    guard_path = tmp_path / "guard.json"
    guard_path.write_text(json.dumps(guard), encoding="utf-8")
    healthy_cloud = CloudObservation(
        True,
        "allocation-a",
        "node-a",
        "node-b",
        ComputeState.STOPPED,
        True,
        True,
        True,
        "7",
    )
    now = [10.0]
    unavailable = [False]
    cloud_calls = []
    effects = []
    routes = [None]
    error: Exception = RetryableHACloudError("SDK deadline exceeded")
    if error_kind == "raw-timeout":
        error = TimeoutError("SDK deadline exceeded")
    elif error_kind == "paginated-timeout":
        error = NebiusPaginationError("VM HA route inventory is unavailable")
        error.__cause__ = TimeoutError("SDK deadline exceeded")

    def observe_cloud() -> CloudObservation:
        cloud_calls.append(now[0])
        if unavailable[0] and failure_provider == "cloud":
            raise error
        return healthy_cloud

    def observe_routes():
        if unavailable[0] and failure_provider == "routes":
            raise error
        return routes[0]

    snapshots = VMHASnapshotAdapter(
        config=_runtime_config(),
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=observe_cloud,
            data_plane=lambda: mode,
            routes=observe_routes,
        ),
        state_dir=tmp_path,
        clock=lambda: now[0],
        boot_id=lambda: "boot-a",
    )
    store = VMHACheckpointFileStore(tmp_path / "checkpoint.json")
    checkpoint = ControllerCheckpoint(state=HAState.ACTIVE)
    store.save(checkpoint)

    def unexpected_guard() -> dict[str, object]:
        pytest.fail("retryable read must not install a forwarding guard")

    runtime = VMHAControllerRuntime(
        snapshots=snapshots,
        effects=VMHAEffectAdapter(
            {kind: effects.append for kind in ActionKind},
            receipt_path=tmp_path / "effect.json",
        ),
        checkpoints=store,
        status_path=tmp_path / "status.json",
        guard=unexpected_guard,
    )
    if previous_observation:
        snapshot = snapshots.observe()
        routes[0] = replace(snapshot.route_reconciliation_context, operation_id="reconciled")
    last_snapshot = snapshots.last_snapshot
    unavailable[0] = True
    calls_before = len(cloud_calls)
    heartbeat_snapshots = HeartbeatSnapshots()
    heartbeat_snapshots.publish_ownership(OwnershipSample(10.0, "node-a"))
    binding = VMHARuntimeBinding.model_validate(_runtime_config()["runtime_binding"])
    heartbeat_ports = SimpleNamespace(
        binding=binding,
        local=binding.nodes[0],
        heartbeat_snapshots=heartbeat_snapshots,
        monotonic_clock=lambda: now[0],
        mtls_snapshot_provider=lambda: SimpleNamespace(epoch=1, certificate_fingerprint="d" * 64),
        auto_healing_policy_provider=lambda: ("enabled", "e" * 64),
    )
    standby = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
    for failures, elapsed in enumerate((1.0, 60.0, 3600.0), start=1):
        now[0] = 10.0 + elapsed
        with pytest.raises(VMHACloudObservationUnavailable):
            runtime.step()
        assert snapshots.cloud_observation_grace.consecutive_failures == failures
        assert json.loads(guard_path.read_text()) == guard
        assert store.load() == checkpoint
        assert snapshots.last_snapshot is last_snapshot
        assert not effects
        assert not (tmp_path / "status.json").exists()
        if mode is DataPlaneMode.ACTIVE:
            # Sample current local health independently and build the real wire
            # payload. Old advisory ownership must not make the peer suspect.
            heartbeat_snapshots.publish_health(HealthSample(now[0], True, True, True, "active"), 0)
            heartbeat = VMHARuntimePorts.heartbeat(
                heartbeat_ports, boot_id="boot-a", sequence=int(elapsed), clock=lambda: now[0]
            )
            decision = standby.decide(
                _snapshot(
                    cluster_id=binding.cluster_id,
                    now=now[0],
                    peer_heartbeat=heartbeat,
                    peer_received_at=now[0],
                ),
                ControllerCheckpoint(),
            )
            assert decision.state is HAState.NORMAL
            assert decision.action is None
            assert decision.checkpoint.suspect_since is None
    # The error path must not perform a second, potentially blocking cloud read.
    assert len(cloud_calls) == calls_before + 3

    if mode is DataPlaneMode.ACTIVE and previous_observation:
        unavailable[0] = False
        recovered = runtime.step()
        assert recovered["state"] in {"active", "degraded"}
        assert recovered["promotion_ready"] is True
        assert not effects


@pytest.mark.parametrize(
    "error",
    [
        PermanentHACloudError("identity conflict"),
        AmbiguousHACloudError("ambiguous mutation"),
        RuntimeError("VM-HA route target membership changed"),
        NebiusPaginationError("VM HA route inventory is unavailable"),
    ],
)
@pytest.mark.parametrize("failure_provider", ["cloud", "routes"])
def test_non_retryable_observation_errors_still_guard(
    tmp_path: Path, error: Exception, failure_provider: str
) -> None:
    def observe_cloud() -> CloudObservation:
        if failure_provider == "cloud":
            raise error
        return CloudObservation(
            True,
            "allocation-a",
            "node-a",
            "node-b",
            ComputeState.STOPPED,
            True,
            True,
            True,
            "7",
        )

    def observe_routes():
        raise error

    snapshots = VMHASnapshotAdapter(
        config=_runtime_config(),
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=observe_cloud,
            data_plane=lambda: DataPlaneMode.ACTIVE,
            routes=observe_routes,
        ),
        state_dir=tmp_path,
    )
    guards = []
    runtime = VMHAControllerRuntime(
        snapshots=snapshots,
        effects=VMHAEffectAdapter(
            {kind: (lambda _action: None) for kind in ActionKind},
            receipt_path=tmp_path / "effect.json",
        ),
        checkpoints=VMHACheckpointFileStore(tmp_path / "checkpoint.json"),
        status_path=tmp_path / "status.json",
        guard=lambda: guards.append("blocked") or {},
    )
    with pytest.raises(type(error)):
        runtime.step()
    assert guards == ["blocked"]


def test_retryable_error_from_effect_still_guards(tmp_path: Path) -> None:
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "active"}),
        encoding="utf-8",
    )
    mode = [DataPlaneMode.ACTIVE]
    snapshots = VMHASnapshotAdapter(
        config=_runtime_config(),
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: CloudObservation(
                True,
                "allocation-a",
                "node-a",
                "node-b",
                ComputeState.STOPPED,
                True,
                True,
                True,
                "7",
            ),
            data_plane=lambda: mode[0],
            routes=lambda: None,
        ),
        state_dir=tmp_path,
        boot_id=lambda: "boot-a",
    )

    def fail_effect(_action: ControllerAction) -> None:
        raise RetryableHACloudError("mutation result unavailable")

    def guard() -> dict[str, object]:
        mode[0] = DataPlaneMode.BLOCKED
        return {}

    runtime = VMHAControllerRuntime(
        snapshots=snapshots,
        effects=VMHAEffectAdapter(
            {kind: fail_effect for kind in ActionKind},
            receipt_path=tmp_path / "effect.json",
        ),
        checkpoints=VMHACheckpointFileStore(tmp_path / "checkpoint.json"),
        status_path=tmp_path / "status.json",
        guard=guard,
    )
    with pytest.raises(RetryableHACloudError):
        runtime.step()
    assert mode[0] is DataPlaneMode.BLOCKED
    assert runtime.controller.checkpoints.load().pending_action is not None


def test_service_retries_observation_failure_without_guarded_error_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")
    delays = []

    class Runtime(DefaultVMHAControllerRuntime):
        def __init__(self) -> None:
            self.calls = 0
            self.closed = False

        def step(self) -> dict[str, object]:
            self.calls += 1
            if self.calls == 1:
                raise VMHACloudObservationUnavailable("observation timed out")
            raise SystemExit(0)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setenv("NOTIFY_SOCKET", "fixture")
    monkeypatch.setattr("nebius_vpngw.agent.main.subprocess.run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("nebius_vpngw.agent.main.time.sleep", delays.append)
    runtime = Runtime()
    with pytest.raises(SystemExit):
        _run_vm_ha_controller(runtime, config_path=config_path, state_dir=tmp_path)
    assert runtime.calls == 2
    assert runtime.closed
    assert delays == [5]
    output = capsys.readouterr().out
    assert "Cloud observation unavailable; retrying in 5 seconds" in output
    assert "Controller step remains guarded" not in output
