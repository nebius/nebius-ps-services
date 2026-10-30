"""Reproduce the main-branch peer-observation race without cloud or OS effects.

This baseline reproducer expects the bug. It also checks a control that refreshes
peer state after the simulated cloud read. No controller effects are executed.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path

from tests.unit.test_vm_ha_agent_runtime import _runtime_config
from tests.unit.test_vm_ha_controller import _peer

from nebius_vpngw.agent.main import VMHASnapshotAdapter, VMHASnapshotProviders
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ControllerCheckpoint,
    DataPlaneMode,
    HAState,
    LocalReadiness,
    VMHAController,
)


def run_scenario(*, refresh_peer_after_reads: bool) -> list[dict[str, object]]:
    current_time = 200.0 if refresh_peer_after_reads else 100.0
    latest_peer = replace(
        _peer(node_id="node-b", configured_role="passive"),
        cluster_id="cluster-a",
    )
    latest_received_at = current_time - 1.0
    cloud = CloudObservation(
        authoritative=True,
        allocation_id="allocation-a",
        observed_owner_node_id="node-b",
        former_owner_node_id="node-b",
        former_owner_compute_state=ComputeState.RUNNING,
        former_attachment_absent=False,
        candidate_attachment_exact=False,
        ownership_re_read_exact=False,
        ownership_epoch="7",
        former_attachment_exact=True,
        candidate_attachment_absent=True,
    )

    def blocking_cloud_read() -> CloudObservation:
        nonlocal current_time, latest_peer, latest_received_at

        # Simulate a slow API read while the independent listener receives a
        # fresh, healthy heartbeat. Advance a fake clock instead of sleeping.
        current_time += 12.0
        latest_peer = replace(latest_peer, sequence=latest_peer.sequence + 1)
        latest_received_at = current_time - 1.0
        return cloud

    records: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="vpngw-stale-peer-") as directory:
        state = Path(directory)
        (state / "guard.json").write_text(
            json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "passive"}),
            encoding="utf-8",
        )
        adapter = VMHASnapshotAdapter(
            config=_runtime_config(),
            providers=VMHASnapshotProviders(
                peer=lambda: (latest_peer, latest_received_at),
                cloud=blocking_cloud_read,
                readiness=lambda: LocalReadiness(True, True, True, True),
                data_plane=lambda: DataPlaneMode.PASSIVE,
                routes=lambda: None,
            ),
            state_dir=state,
            clock=lambda: current_time,
            boot_id=lambda: "boot-a",
        )
        controller = VMHAController(peer_timeout_seconds=10.0, suspicion_seconds=5.0)
        checkpoint = ControllerCheckpoint()

        for step in range(2):
            snapshot = adapter.observe()
            if refresh_peer_after_reads:
                snapshot = replace(
                    snapshot,
                    peer_heartbeat=latest_peer,
                    peer_received_at=latest_received_at,
                )
            decision = controller.decide(snapshot, checkpoint)
            checkpoint = decision.checkpoint

            if refresh_peer_after_reads:
                assert decision.action is None
                assert decision.state is HAState.NORMAL
            elif step == 0:
                assert decision.action is None
                assert decision.state is HAState.SUSPECT
            else:
                assert decision.action is not None
                assert decision.action.kind is ActionKind.STOP_FORMER_OWNER

            assert snapshot.peer_received_at is not None
            records.append(
                {
                    "scenario": "refreshed-peer" if refresh_peer_after_reads else "baseline",
                    "step": step + 1,
                    "snapshot_time": snapshot.now,
                    "sampled_peer_age": snapshot.now - snapshot.peer_received_at,
                    "latest_available_peer_age": snapshot.now - latest_received_at,
                    "peer_healthy": (
                        latest_peer.service_healthy
                        and latest_peer.route_ready
                        and latest_peer.promotion_ready
                    ),
                    "decision": decision.state.value,
                    "reasons": decision.reasons,
                    "action": decision.action.kind.value if decision.action else None,
                }
            )

    return records


def main() -> None:
    for refresh in (False, True):
        for record in run_scenario(refresh_peer_after_reads=refresh):
            print(json.dumps(record))


if __name__ == "__main__":
    main()
