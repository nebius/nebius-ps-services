from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from nebius_vpngw.agent.main import (
    VMHAEffectAdapter,
    VMHASnapshotAdapter,
    VMHASnapshotProviders,
    _run_vm_ha_controller,
    build_vm_ha_controller_runtime,
)
from nebius_vpngw.agent.routing_guard import require_vm_ha_current_boot_ready
from nebius_vpngw.agent.vm_ha.models import DigestSet
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ControllerAction,
    DataPlaneMode,
    LocalReadiness,
)


def _runtime_config() -> dict[str, object]:
    digest = "a" * 64
    credentials = {
        "certificate_authority": "/etc/nebius-vpngw/vm-ha/ca.crt",
        "certificate": "/etc/nebius-vpngw/vm-ha/node.crt",
        "private_key": "/etc/nebius-vpngw/vm-ha/node.key",
    }
    return {
        "cluster_id": "cluster-a",
        "node": {"node_id": "node-a", "role": "active"},
        "generation": {
            "generation_id": digest,
            "digests": {"configuration": digest, "static_routes": "b" * 64, "bgp_policy": "c" * 64},
        },
        "runtime_binding": {
            "cluster_id": "cluster-a",
            "shared_allocation_id": "allocation-a",
            "nodes": [
                {
                    "node_id": "node-a",
                    "role": "active",
                    "compute_id": "compute-a",
                    "network_interface_name": "eth0",
                    "peer_endpoint": "10.0.0.1:9443",
                    "credentials": credentials,
                },
                {
                    "node_id": "node-b",
                    "role": "passive",
                    "compute_id": "compute-b",
                    "network_interface_name": "eth0",
                    "peer_endpoint": "10.0.0.2:9443",
                    "credentials": credentials,
                },
            ],
            "route_runtime_id": "cluster-a:allocation-a",
            "generation_id": digest,
            "configuration_digest": digest,
            "static_routes_digest": "b" * 64,
            "bgp_policy_digest": "c" * 64,
        },
    }


def test_snapshot_adapter_requires_binding_and_observes_current_guard(tmp_path: Path) -> None:
    (tmp_path / "guard.json").write_text(json.dumps({"guard_boot_id": "boot-a"}), encoding="utf-8")
    cloud = CloudObservation(
        True, "allocation-a", "node-a", "node-b", ComputeState.STOPPED, True, True, True, "7"
    )
    adapter = VMHASnapshotAdapter(
        config=_runtime_config(),
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.PASSIVE,
            routes=lambda: None,
        ),
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )
    snapshot = adapter.observe()
    assert (snapshot.local_node_id, snapshot.peer_node_id) == ("node-a", "node-b")
    assert snapshot.guard_boot_id == "boot-a"
    config = _runtime_config()
    config.pop("runtime_binding")
    with pytest.raises(ValueError, match="runtime binding"):
        VMHASnapshotAdapter(config=config, providers=adapter.providers)


def test_effect_adapter_deduplicates_completed_exact_operation(tmp_path: Path) -> None:
    calls: list[str] = []
    handlers = {kind: (lambda action: calls.append(action.operation_id)) for kind in ActionKind}
    adapter = VMHAEffectAdapter(handlers, receipt_path=tmp_path / "effect.json")
    action = ControllerAction(
        ActionKind.ENTER_PASSIVE,
        "boot-a:1:enter-passive:node-a",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    adapter.apply(action)
    adapter.apply(action)
    assert calls == [action.operation_id]
    assert json.loads((tmp_path / "effect.json").read_text())["operation_id"] == action.operation_id


def test_current_boot_gate_rejects_stale_status_and_preserves_non_ha(tmp_path: Path) -> None:
    boot = tmp_path / "boot-id"
    status = tmp_path / "status.json"
    guard = tmp_path / "guard.json"
    boot.write_text("boot-a\n", encoding="utf-8")
    guard.write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "active"}),
        encoding="utf-8",
    )
    payload = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "controller_ready_boot_id": "boot-old",
        "guard_boot_id": "boot-a",
        "promotion_ready": True,
        "data_plane_mode": "active",
        "state": "active",
    }
    status.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="current boot"):
        require_vm_ha_current_boot_ready({"vm_ha": {}}, status_path=status, boot_id_path=boot)
    payload["controller_ready_boot_id"] = "boot-a"
    status.write_text(json.dumps(payload), encoding="utf-8")
    require_vm_ha_current_boot_ready({"vm_ha": {}}, status_path=status, boot_id_path=boot)
    guard.write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "blocked"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="current boot"):
        require_vm_ha_current_boot_ready({"vm_ha": {}}, status_path=status, boot_id_path=boot)
    require_vm_ha_current_boot_ready({}, status_path=tmp_path / "missing", boot_id_path=boot)


def test_service_executes_verified_controller_from_authoritative_binding(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")
    cloud = CloudObservation(
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
    calls: list[str] = []
    runtime = build_vm_ha_controller_runtime(
        config_path=config_path,
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.BLOCKED,
            routes=lambda: None,
        ),
        handlers={kind: (lambda action: calls.append(action.operation_id)) for kind in ActionKind},
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )

    _run_vm_ha_controller(runtime, once=True, config_path=config_path)

    status = json.loads((tmp_path / "status.json").read_text())
    assert status["schema"] == "nebius-vpngw/vm-ha-status-v1"
    assert status["promotion_ready"] is False
    assert len(calls) == 1


def test_service_fails_closed_without_bound_runtime(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")

    def blocked(**_kwargs: object) -> None:
        raise RuntimeError("VM-HA activation BLOCKED: runtime adapters unavailable")

    monkeypatch.setattr("nebius_vpngw.agent.main.require_vm_ha_runtime_prerequisites", blocked)
    with pytest.raises(RuntimeError, match="activation BLOCKED"):
        _run_vm_ha_controller(config_path=config_path)
