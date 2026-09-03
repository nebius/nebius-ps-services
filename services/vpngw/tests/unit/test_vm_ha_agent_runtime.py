from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nebius_vpngw.agent.main import (
    DefaultVMHAControllerRuntime,
    VMHACheckpointFileStore,
    VMHAEffectAdapter,
    VMHASnapshotAdapter,
    VMHASnapshotProviders,
    _advance_standby_restoration,
    _commit_promotion_receipt,
    _manual_failback_provider,
    _manual_failover_provider,
    _project_auto_healing_status,
    _project_transfer_progress,
    _promotion_committed_for_status,
    _read_vm_ha_config,
    _record_transfer_effect_lineage,
    _run_vm_ha_controller,
    _strict_apply_lock_record,
    _typed_transfer_intent_provider,
    _vm_ha_transfer_effect_guard,
    build_default_vm_ha_controller_runtime,
    build_vm_ha_controller_runtime,
    commit_vm_ha_standby_replacement_peer_binding,
    install_vm_ha_removal_inhibition,
    install_vm_ha_standby_replacement_inhibition,
    release_vm_ha_standby_replacement_inhibition,
    request_manual_failback,
    request_manual_failover,
    verify_vm_ha_removal_quiescent,
    verify_vm_ha_standby_replacement_quiescent,
    vm_ha_status,
)
from nebius_vpngw.agent.routing_guard import (
    _remove_table_220,
    enforce_periodic_routing_invariants,
    enforce_vm_ha_passive_routing_hygiene,
    has_table_220_rule,
    require_vm_ha_current_boot_ready,
    require_vm_ha_route_maintenance_ready,
)
from nebius_vpngw.agent.vm_ha import AtomicGenerationStore
from nebius_vpngw.agent.vm_ha.auto_healing import (
    AutoHealingPolicyPhase,
    AutoHealingPolicyRecord,
    AutoHealingPolicyStore,
    StandbyAutoHealing,
    policy_decision_digest,
)
from nebius_vpngw.agent.vm_ha.inhibition import (
    STANDBY_REPLACEMENT_INHIBITION_FILENAME,
    STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
    STANDBY_REPLACEMENT_RELEASE_FILENAME,
    STANDBY_REPLACEMENT_RELEASE_SCHEMA,
    standby_replacement_inhibition_operation_id,
    standby_replacement_release_operation_id,
)
from nebius_vpngw.agent.vm_ha.models import (
    DigestSet,
    PeerHeartbeat,
    StalePeerStateError,
    StateValidationError,
)
from nebius_vpngw.agent.vm_ha.progress import (
    TransferProgressIdentity,
    TransferProgressStore,
    planned_request_fingerprint,
)
from nebius_vpngw.agent.vm_ha.promotion_receipt import (
    PROMOTION_RECEIPT_SCHEMA,
    promotion_receipt_id_v1,
)
from nebius_vpngw.agent.vm_ha.restoration import (
    RESTORATION_AUTHORIZATION_FILENAME,
    PolicyAgreementStore,
    RestorationPhase,
    RestorationSource,
    StandbyRestorationError,
    StandbyRestorationStore,
)
from nebius_vpngw.agent.vm_ha.runtime import _expected_bgp_exports
from nebius_vpngw.agent.vm_ha.transport import PeerTransportError
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    CloudObservation,
    ComputeState,
    ConfiguredRole,
    ControllerAction,
    ControllerCheckpoint,
    ControllerResult,
    ControllerSnapshot,
    DataPlaneMode,
    HAState,
    LocalReadiness,
    RecoverableController,
    RouteReconciliationContext,
    TransferIntent,
)
from nebius_vpngw.agent.vm_ha_rearm import REARM_LOCK_PATH, _acquire_rearm_lock
from nebius_vpngw.schema import VMHARouteTarget, VMHARuntimeBinding


def _runtime_config() -> dict[str, object]:
    digest = "a" * 64
    credentials_path = "/etc/nebius-vpngw/vm-ha/nebius-credentials.json"
    route_targets = (
        VMHARouteTarget(
            project_id="project-a",
            network_id="network-a",
            workload_subnet_id="subnet-a",
            route_table_id="route-table-a",
        ),
    )
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
            ],
            "route_targets": [target.model_dump(mode="json") for target in route_targets],
            "route_runtime_id": VMHARuntimeBinding.derive_route_runtime_id(
                "cluster-a", "allocation-a", route_targets
            ),
            "generation_id": digest,
            "configuration_digest": digest,
            "static_routes_digest": "b" * 64,
            "bgp_policy_digest": "c" * 64,
        },
    }


def _passive_runtime_config() -> dict[str, object]:
    config = _runtime_config()
    config["node"] = {"node_id": "node-b", "role": "passive"}
    return config


def _promotion_receipt(*, owner: str = "node-b", epoch: str = "9") -> dict[str, object]:
    return {
        "schema": "nebius-vpngw/vm-ha-promotion-receipt-v1",
        "receipt_id": "receipt-a",
        "intent": "planned-failover",
        "cluster_id": "cluster-a",
        "owner_node_id": owner,
        "former_owner_node_id": "node-a",
        "allocation_id": "allocation-a",
        "ownership_epoch": epoch,
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "route_operation_id": "route-operation-a",
        "committed_at": 10.0,
        "common_cutover_seconds": 5.0,
    }


def _durable_restoration_receipt(
    tmp_path: Path,
    digests: object,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema": PROMOTION_RECEIPT_SCHEMA,
        "intent": "planned-failback",
        "cluster_id": "cluster-a",
        "owner_node_id": "node-a",
        "former_owner_node_id": "node-b",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": digests,
        "ownership_epoch": "revision-10",
        "route_operation_id": "route-operation",
        "committed_at": 21.0,
        "common_cutover_seconds": 1.0,
    }
    receipt["receipt_id"] = promotion_receipt_id_v1(
        allocation_id="allocation-a",
        first_operation_id="first-effect",
        generation_id="a" * 64,
        intent="planned-failback",
        owner_node_id="node-a",
        ownership_epoch="revision-10",
        route_operation_id="route-operation",
    )
    (tmp_path / "promotion-receipt.json").write_text(
        json.dumps(receipt),
        encoding="utf-8",
    )
    return receipt


def _automatic_lineage() -> dict[str, object]:
    return {
        "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
        "intent": "automatic-failover",
        "cluster_id": "cluster-a",
        "candidate_node_id": "node-b",
        "former_owner_node_id": "node-a",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "first_operation_id": "post-promotion-prepare",
        "started_at": 20.0,
    }


def _apply_owner_adoption() -> dict[str, object]:
    return {
        "schema": "nebius-vpngw/vm-ha-apply-owner-adoption-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-b",
        "peer_node_id": "node-a",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "operation_id": "f" * 64,
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
    assert (
        snapshot.route_reconciliation_context.route_runtime_id
        == (
            _runtime_config()["runtime_binding"]["route_runtime_id"]  # type: ignore[index]
        )
    )
    config = _runtime_config()
    config.pop("runtime_binding")
    with pytest.raises(ValueError, match="runtime binding"):
        VMHASnapshotAdapter(config=config, providers=adapter.providers)


def test_snapshot_adapter_keeps_mtls_transfer_inhibition_out_of_apply_lock(
    tmp_path: Path,
) -> None:
    (tmp_path / "guard.json").write_text(json.dumps({"guard_boot_id": "boot-a"}), encoding="utf-8")
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

    def providers(*, apply_operation_id: str | None) -> VMHASnapshotProviders:
        return VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.PASSIVE,
            routes=lambda: None,
            apply_lock_operation_id=lambda: apply_operation_id,
            transfer_inhibition_operation_id=lambda: "b" * 64,
        )

    adapter = VMHASnapshotAdapter(
        config=_runtime_config(),
        providers=providers(apply_operation_id=None),
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )

    snapshot = adapter.observe()

    assert snapshot.apply_locked is False
    assert snapshot.transfer_inhibited is True
    assert snapshot.standby_replacement_inhibited is False
    assert adapter.last_transfer_inhibition_operation_id == "b" * 64

    conflicting = VMHASnapshotAdapter(
        config=_runtime_config(),
        providers=providers(apply_operation_id="c" * 64),
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )
    with pytest.raises(ValueError, match="conflicting writer inhibitions"):
        conflicting.observe()


def test_snapshot_adapter_identifies_exact_standby_replacement_inhibition(
    tmp_path: Path,
) -> None:
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a"}),
        encoding="utf-8",
    )
    operation_id = "b" * 64
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
    adapter = VMHASnapshotAdapter(
        config=_runtime_config(),
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.PASSIVE,
            routes=lambda: None,
            transfer_inhibition_operation_id=lambda: operation_id,
            standby_replacement_inhibition_operation_id=lambda: operation_id,
        ),
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )

    snapshot = adapter.observe()

    assert snapshot.transfer_inhibited is True
    assert snapshot.standby_replacement_inhibited is True


def test_controller_status_proves_processed_quiescent_mtls_inhibition(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")
    cloud = CloudObservation(
        True,
        "allocation-a",
        "node-b",
        "node-b",
        ComputeState.RUNNING,
        False,
        False,
        False,
        "7",
        former_attachment_exact=True,
        candidate_attachment_absent=True,
    )
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "passive"}),
        encoding="utf-8",
    )
    operation_id = "b" * 64
    runtime = build_vm_ha_controller_runtime(
        config_path=config_path,
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.PASSIVE,
            routes=lambda: None,
            transfer_inhibition_operation_id=lambda: operation_id,
        ),
        handlers={kind: (lambda _action: None) for kind in ActionKind},
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )

    _run_vm_ha_controller(runtime, once=True, config_path=config_path)
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "blocked"
    assert status["reasons"] == ["mtls-rotation-active"]
    assert status["controller_capabilities"] == [
        "vm-ha-mtls-rotation-quiescence-v1",
        "vm-ha-live-peer-replacement-v4",
        "vm-ha-standby-replacement-inhibition-v2",
        "vm-ha-standby-restoration-v2",
    ]
    assert status["transfer_inhibition_operation_id"] == operation_id
    assert status["transfer_inhibition_quiescent"] is True

    (tmp_path / "accepted-cloud-operation.json").write_text("{}", encoding="utf-8")
    _run_vm_ha_controller(runtime, once=True, config_path=config_path)
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["transfer_inhibition_operation_id"] == operation_id
    assert status["transfer_inhibition_quiescent"] is False


def test_runtime_config_includes_resolved_top_level_bgp_export_policy(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "gateway": {"local_prefixes": ["10.96.0.0/13"]},
                "connections": [
                    {
                        "name": "peer-a",
                        "bgp": {"advertise_local_prefixes": True},
                        "tunnels": [
                            {
                                "ha_role": "active",
                                "inner_remote_ip": "169.254.10.2",
                            }
                        ],
                    }
                ],
                "vm_ha": _runtime_config(),
            }
        ),
        encoding="utf-8",
    )

    config = _read_vm_ha_config(path)

    assert config is not None
    assert config["gateway"] == {"local_prefixes": ["10.96.0.0/13"]}
    assert _expected_bgp_exports(config, [{"connection": "peer-a"}]) == {
        "169.254.10.2": frozenset({"10.96.0.0/13"})
    }


def test_runtime_config_preserves_omitted_and_explicitly_disabled_vm_ha(tmp_path: Path) -> None:
    omitted = tmp_path / "omitted.yaml"
    disabled = tmp_path / "disabled.yaml"
    omitted.write_text("connections: []\n", encoding="utf-8")
    disabled.write_text("vm_ha:\n  enabled: false\n", encoding="utf-8")

    assert _read_vm_ha_config(omitted) is None
    assert _read_vm_ha_config(disabled) is None


def test_default_durable_gates_validate_exact_records(tmp_path: Path) -> None:
    apply_path = tmp_path / "apply.lock.json"
    assert (
        _strict_apply_lock_record(
            apply_path,
            cluster_id="cluster-a",
            node_id="node-a",
            generation_id="a" * 64,
        )
        is None
    )
    apply_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
                "apply_locked": True,
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
                "operation_id": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    assert (
        _strict_apply_lock_record(
            apply_path,
            cluster_id="cluster-a",
            node_id="node-a",
            generation_id="a" * 64,
        )
        == "b" * 64
    )
    assert (
        _strict_apply_lock_record(
            apply_path,
            cluster_id="cluster-a",
            node_id="node-a",
            generation_id="c" * 64,
        )
        == "b" * 64
    )
    with pytest.raises(ValueError, match="another node"):
        _strict_apply_lock_record(
            apply_path,
            cluster_id="cluster-a",
            node_id="node-b",
            generation_id="a" * 64,
        )
    apply_path.write_text('{"apply_locked":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="apply-lock"):
        _strict_apply_lock_record(
            apply_path,
            cluster_id="cluster-a",
            node_id="node-a",
            generation_id="a" * 64,
        )


def test_removal_inhibition_serializes_and_requires_quiescent_controller(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")
    operation_id = "d" * 64

    inhibition = install_vm_ha_removal_inhibition(
        operation_id,
        config_path=config_path,
        state_dir=tmp_path,
    )

    assert inhibition == {
        "schema": "nebius-vpngw/vm-ha-removal-inhibition-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
        "operation_id": operation_id,
    }
    assert (
        install_vm_ha_removal_inhibition(
            operation_id,
            config_path=config_path,
            state_dir=tmp_path,
        )
        == inhibition
    )
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
                "apply_locked": True,
                "apply_operation_id": operation_id,
                "pending_operation_id": None,
                "promotion_ready": False,
                "standby_ready": False,
            }
        ),
        encoding="utf-8",
    )

    assert (
        verify_vm_ha_removal_quiescent(
            operation_id,
            config_path=config_path,
            state_dir=tmp_path,
            boot_id=lambda: "boot-a",
        )["schema"]
        == "nebius-vpngw/vm-ha-removal-quiescent-v1"
    )

    (tmp_path / "accepted-cloud-operation.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="quiescent controller"):
        verify_vm_ha_removal_quiescent(
            operation_id,
            config_path=config_path,
            state_dir=tmp_path,
            boot_id=lambda: "boot-a",
        )


def test_removal_inhibition_never_overwrites_a_foreign_apply_lock(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")
    (tmp_path / "apply.lock").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
                "apply_locked": True,
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
                "operation_id": "e" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="conflicts with another apply operation"):
        install_vm_ha_removal_inhibition(
            "d" * 64,
            config_path=config_path,
            state_dir=tmp_path,
        )


def test_standby_replacement_inhibition_preserves_active_owner_and_releases_idempotently(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")
    operation_id = "d" * 64

    inhibition = install_vm_ha_standby_replacement_inhibition(
        operation_id,
        config_path=config_path,
        state_dir=tmp_path,
    )

    assert inhibition == {
        "schema": "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
        "operation_id": operation_id,
    }
    assert not (tmp_path / "apply.lock").exists()
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
                "state": "degraded",
                "reasons": ["peer-generation-unavailable"],
                "guard_boot_id": "boot-a",
                "data_plane_mode": "active",
                "promotion_ready": True,
                "observed_owner_node_id": "node-a",
                "apply_locked": False,
                "apply_operation_id": None,
                "transfer_inhibition_operation_id": operation_id,
                "transfer_inhibition_quiescent": True,
                "pending_operation_id": None,
                "controller_capabilities": [
                    "vm-ha-live-peer-replacement-v4",
                    "vm-ha-standby-replacement-inhibition-v2",
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "guard.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-guard-v1",
                "guard_boot_id": "boot-a",
                "data_plane_mode": "active",
            }
        ),
        encoding="utf-8",
    )

    assert (
        verify_vm_ha_standby_replacement_quiescent(
            operation_id,
            config_path=config_path,
            state_dir=tmp_path,
            boot_id=lambda: "boot-a",
        )["schema"]
        == "nebius-vpngw/vm-ha-standby-replacement-quiescent-v1"
    )
    released = release_vm_ha_standby_replacement_inhibition(
        operation_id,
        config_path=config_path,
        state_dir=tmp_path,
        boot_id=lambda: "boot-a",
    )
    assert released["schema"] == "nebius-vpngw/vm-ha-standby-replacement-release-v1"
    assert not (tmp_path / "standby-replacement.inhibition.json").exists()
    assert (tmp_path / "standby-replacement.release.json").exists()
    assert (
        release_vm_ha_standby_replacement_inhibition(
            operation_id,
            config_path=config_path,
            state_dir=tmp_path,
            boot_id=lambda: "boot-a",
        )
        == released
    )


@pytest.mark.parametrize(
    ("reader", "filename", "schema", "record_name"),
    (
        (
            standby_replacement_inhibition_operation_id,
            STANDBY_REPLACEMENT_INHIBITION_FILENAME,
            STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
            "inhibition",
        ),
        (
            standby_replacement_release_operation_id,
            STANDBY_REPLACEMENT_RELEASE_FILENAME,
            STANDBY_REPLACEMENT_RELEASE_SCHEMA,
            "release",
        ),
    ),
)
def test_standby_replacement_record_reader_is_strict_and_identity_bound(
    tmp_path: Path,
    reader,
    filename: str,
    schema: str,
    record_name: str,
) -> None:
    identity = {
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
    }
    operation_id = "d" * 64
    path = tmp_path / filename

    assert reader(tmp_path, **identity) is None

    payload = {
        "schema": schema,
        **identity,
        "operation_id": operation_id,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert reader(tmp_path, **identity) == operation_id

    path.unlink()
    target = tmp_path / f"{filename}.target"
    target.write_text(json.dumps(payload), encoding="utf-8")
    path.symlink_to(target)
    with pytest.raises(ValueError, match=rf"{record_name} path is invalid"):
        reader(tmp_path, **identity)

    path.unlink()
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match=rf"{record_name} is malformed"):
        reader(tmp_path, **identity)

    path.write_text(json.dumps({**payload, "unexpected": True}), encoding="utf-8")
    with pytest.raises(ValueError, match=rf"{record_name} is invalid"):
        reader(tmp_path, **identity)

    path.write_text(
        json.dumps({**payload, "generation_id": "b" * 64}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=rf"{record_name} identity is stale"):
        reader(tmp_path, **identity)


def test_standby_replacement_inhibition_retires_exact_superseded_terminal_restoration(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")
    store = _awaiting_restoration(tmp_path)
    awaiting = store.load()
    assert awaiting is not None
    completed = store.complete(
        receipt_id=str(awaiting.promotion_receipt_id),
        updated_at=25.0,
    )
    receipt = json.loads(store.receipt_path.read_text(encoding="utf-8"))
    receipt.update(
        intent="apply-owner-adoption",
        ownership_epoch="revision-11",
        route_operation_id="apply-route-operation",
    )
    receipt["receipt_id"] = promotion_receipt_id_v1(
        allocation_id=completed.allocation_id,
        first_operation_id=str(completed.first_operation_id),
        generation_id=completed.generation_id,
        intent="apply-owner-adoption",
        owner_node_id=completed.owner_node_id,
        ownership_epoch="revision-11",
        route_operation_id="apply-route-operation",
    )
    store.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    inhibition = install_vm_ha_standby_replacement_inhibition(
        "d" * 64,
        config_path=config_path,
        state_dir=tmp_path,
    )

    assert inhibition["operation_id"] == "d" * 64
    assert not store.path.exists()


def test_standby_replacement_peer_binding_commit_changes_only_peer_compute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    current = {"vm_ha": _runtime_config()}
    config_path.write_text(yaml.safe_dump(current, sort_keys=False), encoding="utf-8")
    config_path.chmod(0o600)
    operation_id = "d" * 64
    install_vm_ha_standby_replacement_inhibition(
        operation_id,
        config_path=config_path,
        state_dir=tmp_path,
    )
    staged = yaml.safe_load(yaml.safe_dump(current))
    staged["vm_ha"]["runtime_binding"]["nodes"][1]["compute_id"] = "compute-b-new"
    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    staged_path = staged_dir / f"{'a' * 64}.yaml"
    staged_path.write_text(yaml.safe_dump(staged, sort_keys=False), encoding="utf-8")
    staged_path.chmod(0o600)
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.ManagedMTLSStore",
        lambda *_args, **_kwargs: SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                peers=(SimpleNamespace(node_id="node-b", compute_id="compute-b-new", epoch=2),)
            )
        ),
    )

    receipt = commit_vm_ha_standby_replacement_peer_binding(
        operation_id,
        config_path=config_path,
        state_dir=tmp_path,
        staged_dir=staged_dir,
    )

    installed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert receipt["schema"] == "vm-ha-live-peer-replacement-v4"
    assert installed["vm_ha"]["runtime_binding"]["nodes"][1]["compute_id"] == ("compute-b-new")
    assert (
        installed["vm_ha"]["runtime_binding"]["nodes"][0]
        == (current["vm_ha"]["runtime_binding"]["nodes"][0])
    )


def test_manual_failback_provider_requires_exact_current_identity(tmp_path: Path) -> None:
    failback_path = tmp_path / "manual-failback.json"
    failback_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-manual-failback-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
                "requested_at": 10.0,
            }
        ),
        encoding="utf-8",
    )
    requested = _manual_failback_provider(state_dir=tmp_path, config=_runtime_config())
    assert requested()
    failback_path.write_text(
        failback_path.read_text(encoding="utf-8").replace("cluster-a", "stale-cluster"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid or stale"):
        requested()


def test_manual_failover_provider_requires_exact_current_identity(tmp_path: Path) -> None:
    failover_path = tmp_path / "manual-failover.json"
    failover_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
                "requested_at": 10.0,
            }
        ),
        encoding="utf-8",
    )

    config = _runtime_config()
    config["node"] = {"node_id": "node-a", "role": "passive"}
    requested = _manual_failover_provider(state_dir=tmp_path, config=config)

    assert requested()
    failover_path.write_text(
        failover_path.read_text(encoding="utf-8").replace(
            "vm-ha-manual-failover-v1", "vm-ha-manual-failback-v1"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid or stale"):
        requested()


@pytest.mark.parametrize(
    ("role", "request_fn", "intent"),
    (
        ("active", request_manual_failback, "planned-failback"),
        ("passive", request_manual_failover, "automatic-failover"),
    ),
)
def test_manual_transfer_request_rejects_started_durable_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    request_fn: object,
    intent: str,
) -> None:
    config = _runtime_config()
    config["node"] = {"node_id": "node-a", "role": role}
    binding = config["runtime_binding"]
    generation = config["generation"]
    (tmp_path / "transfer-lineage.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
                "intent": intent,
                "cluster_id": "cluster-a",
                "candidate_node_id": "node-a",
                "former_owner_node_id": "node-b",
                "allocation_id": binding["shared_allocation_id"],  # type: ignore[index]
                "generation_id": generation["generation_id"],  # type: ignore[index]
                "digests": generation["digests"],  # type: ignore[index]
                "first_operation_id": "first-effect",
                "started_at": 10.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("nebius_vpngw.agent.main._read_vm_ha_config", lambda: config)
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.require_vm_ha_runtime_prerequisites",
        lambda **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="durable transfer is in progress"):
        request_fn(state_dir=tmp_path)  # type: ignore[operator]

    assert not (tmp_path / "manual-failback.json").exists()
    assert not (tmp_path / "manual-failover.json").exists()


@pytest.mark.parametrize(
    ("role", "request_fn", "intent", "request_name", "schema"),
    (
        (
            "active",
            request_manual_failback,
            "planned-failback",
            "manual-failback.json",
            "nebius-vpngw/vm-ha-manual-failback-v1",
        ),
        (
            "passive",
            request_manual_failover,
            "planned-failover",
            "manual-failover.json",
            "nebius-vpngw/vm-ha-manual-failover-v1",
        ),
    ),
)
def test_manual_transfer_request_reuses_exact_request_that_started_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    request_fn: object,
    intent: str,
    request_name: str,
    schema: str,
) -> None:
    config = _runtime_config()
    config["node"] = {"node_id": "node-a", "role": role}
    binding = config["runtime_binding"]
    generation = config["generation"]
    request = {
        "schema": schema,
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "generation_id": generation["generation_id"],  # type: ignore[index]
        "requested_at": 9.0,
    }
    request_path = tmp_path / request_name
    request_bytes = json.dumps(request, sort_keys=True)
    request_path.write_text(request_bytes, encoding="utf-8")
    (tmp_path / "transfer-lineage.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
                "intent": intent,
                "cluster_id": "cluster-a",
                "candidate_node_id": "node-a",
                "former_owner_node_id": "node-b",
                "allocation_id": binding["shared_allocation_id"],  # type: ignore[index]
                "generation_id": generation["generation_id"],  # type: ignore[index]
                "digests": generation["digests"],  # type: ignore[index]
                "first_operation_id": "first-effect",
                "started_at": 10.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("nebius_vpngw.agent.main._read_vm_ha_config", lambda: config)
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.require_vm_ha_runtime_prerequisites",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.main._require_planned_transfer_auto_healing_enabled",
        lambda **_kwargs: pytest.fail("resuming a started transfer must not be re-admitted"),
    )

    assert request_fn(state_dir=tmp_path) == request  # type: ignore[operator]

    assert request_path.read_text(encoding="utf-8") == request_bytes


def test_manual_transfer_request_rejects_later_same_direction_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _runtime_config()
    binding = config["runtime_binding"]
    generation = config["generation"]
    (tmp_path / "manual-failback.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-manual-failback-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": generation["generation_id"],  # type: ignore[index]
                "requested_at": 11.0,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "transfer-lineage.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
                "intent": "planned-failback",
                "cluster_id": "cluster-a",
                "candidate_node_id": "node-a",
                "former_owner_node_id": "node-b",
                "allocation_id": binding["shared_allocation_id"],  # type: ignore[index]
                "generation_id": generation["generation_id"],  # type: ignore[index]
                "digests": generation["digests"],  # type: ignore[index]
                "first_operation_id": "first-effect",
                "started_at": 10.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("nebius_vpngw.agent.main._read_vm_ha_config", lambda: config)
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.require_vm_ha_runtime_prerequisites",
        lambda **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="durable transfer is in progress"):
        request_manual_failback(state_dir=tmp_path)


def test_manual_transfer_request_rechecks_lineage_after_writer_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _runtime_config()
    observations = iter(((None, False), (None, True)))
    monkeypatch.setattr("nebius_vpngw.agent.main._read_vm_ha_config", lambda: config)
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.require_vm_ha_runtime_prerequisites",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.main._matching_manual_transfer_request",
        lambda **_kwargs: next(observations),
    )

    with pytest.raises(RuntimeError, match="durable transfer is in progress"):
        request_manual_failback(state_dir=tmp_path)

    assert not (tmp_path / "manual-failback.json").exists()


def test_manual_transfer_request_rejects_disabled_standby_auto_healing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _runtime_config()
    local = AutoHealingPolicyStore(tmp_path)
    peer = AutoHealingPolicyStore(tmp_path / "peer")
    local_initial = local.initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=1.0,
    )
    peer_initial = peer.initialize(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        updated_at=1.0,
    )
    operation_id = "d" * 64
    local_prepared = local.prepare(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=local_initial.decision_digest,
        peer=peer_initial,
        updated_at=2.0,
    )
    peer.prepare(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=peer_initial.decision_digest,
        peer=local_prepared,
        updated_at=2.0,
    )
    peer_committed = peer.commit(
        cluster_id="cluster-a",
        node_id="node-b",
        peer_node_id="node-a",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=peer_initial.decision_digest,
        peer=local_prepared,
        updated_at=3.0,
    )
    local.commit(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=local_initial.decision_digest,
        peer=peer_committed,
        updated_at=3.0,
    )
    monkeypatch.setattr("nebius_vpngw.agent.main._read_vm_ha_config", lambda: config)
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.require_vm_ha_runtime_prerequisites",
        lambda **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="requires enabled two-member"):
        request_manual_failback(state_dir=tmp_path)

    assert not (tmp_path / "manual-failback.json").exists()


def test_typed_transfer_provider_keeps_started_lineage_after_request_consumption(
    tmp_path: Path,
) -> None:
    config = _runtime_config()
    request = tmp_path / "manual-failback.json"
    request.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-manual-failback-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
                "requested_at": 10.0,
            }
        ),
        encoding="utf-8",
    )
    provider = _typed_transfer_intent_provider(state_dir=tmp_path, config=config)
    assert provider() == (TransferIntent.PLANNED_FAILBACK, False)
    binding = config["runtime_binding"]
    generation = config["generation"]
    (tmp_path / "transfer-lineage.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
                "intent": "planned-failback",
                "cluster_id": "cluster-a",
                "candidate_node_id": "node-a",
                "former_owner_node_id": "node-b",
                "allocation_id": binding["shared_allocation_id"],  # type: ignore[index]
                "generation_id": generation["generation_id"],  # type: ignore[index]
                "digests": generation["digests"],  # type: ignore[index]
                "first_operation_id": "first-effect",
                "started_at": 11.0,
            }
        ),
        encoding="utf-8",
    )
    request.unlink()

    assert provider() == (TransferIntent.PLANNED_FAILBACK, True)


def test_typed_transfer_provider_retires_only_later_conflicting_request(
    tmp_path: Path,
) -> None:
    config = _runtime_config()
    config["node"] = {"node_id": "node-a", "role": "passive"}
    binding = config["runtime_binding"]
    generation = config["generation"]
    request_path = tmp_path / "manual-failover.json"
    request_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
                "requested_at": 20.0,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "transfer-lineage.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
                "intent": "automatic-failover",
                "cluster_id": "cluster-a",
                "candidate_node_id": "node-a",
                "former_owner_node_id": "node-b",
                "allocation_id": binding["shared_allocation_id"],  # type: ignore[index]
                "generation_id": generation["generation_id"],  # type: ignore[index]
                "digests": generation["digests"],  # type: ignore[index]
                "first_operation_id": "first-effect",
                "started_at": 10.0,
            }
        ),
        encoding="utf-8",
    )

    provider = _typed_transfer_intent_provider(state_dir=tmp_path, config=config)

    assert provider() == (TransferIntent.AUTOMATIC_FAILOVER, True)
    assert not request_path.exists()


def test_typed_transfer_provider_rejects_earlier_conflicting_request(
    tmp_path: Path,
) -> None:
    config = _runtime_config()
    config["node"] = {"node_id": "node-a", "role": "passive"}
    binding = config["runtime_binding"]
    generation = config["generation"]
    request_path = tmp_path / "manual-failover.json"
    request_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
                "requested_at": 5.0,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "transfer-lineage.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
                "intent": "automatic-failover",
                "cluster_id": "cluster-a",
                "candidate_node_id": "node-a",
                "former_owner_node_id": "node-b",
                "allocation_id": binding["shared_allocation_id"],  # type: ignore[index]
                "generation_id": generation["generation_id"],  # type: ignore[index]
                "digests": generation["digests"],  # type: ignore[index]
                "first_operation_id": "first-effect",
                "started_at": 10.0,
            }
        ),
        encoding="utf-8",
    )

    provider = _typed_transfer_intent_provider(state_dir=tmp_path, config=config)

    with pytest.raises(ValueError, match="conflicts with sticky transfer lineage"):
        provider()
    assert request_path.exists()


@pytest.mark.parametrize(
    "action_kind",
    [
        ActionKind.PREPARE_CANDIDATE_DATAPLANE,
        ActionKind.RECONCILE_ROUTES,
        ActionKind.ENABLE_ACTIVE,
    ],
)
def test_configured_active_owner_reconciliation_does_not_create_transfer_lineage(
    tmp_path: Path, action_kind: ActionKind
) -> None:
    action = ControllerAction(
        action_kind,
        f"boot-a:1:{action_kind.value}:node-a",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    snapshots = SimpleNamespace(
        last_snapshot=SimpleNamespace(
            transfer_intent=None,
            transfer_effect_started=False,
            configured_role=ConfiguredRole.ACTIVE,
            cluster_id="cluster-a",
            local_node_id="node-a",
            peer_node_id="node-b",
            cloud=SimpleNamespace(
                allocation_id="allocation-a",
                local_attachment_exact=lambda node_id: node_id == "node-a",
            ),
            local_generation_id="a" * 64,
            local_digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        )
    )
    lineage_path = tmp_path / "transfer-lineage.json"
    _record_transfer_effect_lineage(
        action=action,
        snapshots=snapshots,
        config=_runtime_config(),
        path=lineage_path,
    )

    assert not lineage_path.exists()


def test_passive_owner_effect_without_request_creates_automatic_lineage(
    tmp_path: Path,
) -> None:
    action = ControllerAction(
        ActionKind.PREPARE_CANDIDATE_DATAPLANE,
        "boot-a:1:prepare-candidate-dataplane:node-a",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    snapshots = SimpleNamespace(
        last_snapshot=SimpleNamespace(
            transfer_intent=None,
            transfer_effect_started=False,
            configured_role=ConfiguredRole.PASSIVE,
            cluster_id="cluster-a",
            local_node_id="node-a",
            peer_node_id="node-b",
            cloud=SimpleNamespace(allocation_id="allocation-a"),
            local_generation_id="a" * 64,
            local_digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        )
    )
    lineage_path = tmp_path / "transfer-lineage.json"

    _record_transfer_effect_lineage(
        action=action,
        snapshots=snapshots,
        config=_runtime_config(),
        path=lineage_path,
    )

    assert json.loads(lineage_path.read_text(encoding="utf-8"))["intent"] == (
        TransferIntent.AUTOMATIC_FAILOVER.value
    )


def test_configured_active_survivor_creates_and_reloads_automatic_lineage(
    tmp_path: Path,
) -> None:
    action = ControllerAction(
        ActionKind.STOP_FORMER_OWNER,
        "boot-a:1:stop-former-owner:node-b",
        "boot-a",
        "node-b",
        "allocation-a",
        "9",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    snapshots = SimpleNamespace(
        last_snapshot=SimpleNamespace(
            transfer_intent=None,
            transfer_effect_started=False,
            configured_role=ConfiguredRole.ACTIVE,
            cluster_id="cluster-a",
            local_node_id="node-a",
            peer_node_id="node-b",
            cloud=_planned_failback_pre_effect_cloud(),
            local_generation_id="a" * 64,
            local_digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        )
    )
    lineage_path = tmp_path / "transfer-lineage.json"

    _record_transfer_effect_lineage(
        action=action,
        snapshots=snapshots,
        config=_runtime_config(),
        path=lineage_path,
        clock=lambda: 20.0,
    )

    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    assert lineage["intent"] == TransferIntent.AUTOMATIC_FAILOVER.value
    assert lineage["candidate_node_id"] == "node-a"
    assert lineage["former_owner_node_id"] == "node-b"
    assert _typed_transfer_intent_provider(
        state_dir=tmp_path,
        config=_runtime_config(),
    )() == (TransferIntent.AUTOMATIC_FAILOVER, True)


@pytest.mark.parametrize(
    "action_kind",
    [
        ActionKind.PREPARE_CANDIDATE_DATAPLANE,
        ActionKind.RECONCILE_ROUTES,
        ActionKind.ENABLE_ACTIVE,
    ],
)
def test_promoted_passive_owner_reconciliation_uses_exact_receipt_without_lineage(
    tmp_path: Path, action_kind: ActionKind
) -> None:
    config = _passive_runtime_config()
    (tmp_path / "promotion-receipt.json").write_text(
        json.dumps(_promotion_receipt()), encoding="utf-8"
    )
    action = ControllerAction(
        action_kind,
        f"boot-a:1:{action_kind.value}:node-b",
        "boot-a",
        "node-b",
        "allocation-a",
        "9",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    cloud = CloudObservation(
        True,
        "allocation-a",
        "node-b",
        "node-a",
        ComputeState.RUNNING,
        True,
        True,
        True,
        "9",
    )
    snapshots = SimpleNamespace(
        last_snapshot=SimpleNamespace(
            transfer_intent=None,
            transfer_effect_started=False,
            configured_role=ConfiguredRole.PASSIVE,
            cluster_id="cluster-a",
            local_node_id="node-b",
            peer_node_id="node-a",
            cloud=cloud,
            local_generation_id="a" * 64,
            local_digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        )
    )
    lineage_path = tmp_path / "transfer-lineage.json"

    _record_transfer_effect_lineage(
        action=action,
        snapshots=snapshots,
        config=config,
        path=lineage_path,
    )

    assert not lineage_path.exists()


@pytest.mark.parametrize(
    ("receipt_epoch", "lineage_started_at", "expected_discarded"),
    [
        ("9", 20.0, True),
        ("8", 20.0, False),
        ("9", 9.0, False),
    ],
)
def test_snapshot_discards_only_redundant_post_promotion_automatic_lineage(
    tmp_path: Path,
    receipt_epoch: str,
    lineage_started_at: float,
    expected_discarded: bool,
) -> None:
    config = _passive_runtime_config()
    (tmp_path / "guard.json").write_text(json.dumps({"guard_boot_id": "boot-a"}), encoding="utf-8")
    (tmp_path / "promotion-receipt.json").write_text(
        json.dumps(_promotion_receipt(epoch=receipt_epoch)), encoding="utf-8"
    )
    lineage_path = tmp_path / "transfer-lineage.json"
    lineage_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
                "intent": "automatic-failover",
                "cluster_id": "cluster-a",
                "candidate_node_id": "node-b",
                "former_owner_node_id": "node-a",
                "allocation_id": "allocation-a",
                "generation_id": "a" * 64,
                "digests": {
                    "configuration": "a" * 64,
                    "static_routes": "b" * 64,
                    "bgp_policy": "c" * 64,
                },
                "first_operation_id": "post-promotion-prepare",
                "started_at": lineage_started_at,
            }
        ),
        encoding="utf-8",
    )
    cloud = CloudObservation(
        True,
        "allocation-a",
        "node-b",
        "node-a",
        ComputeState.RUNNING,
        True,
        True,
        True,
        "9",
    )
    adapter = VMHASnapshotAdapter(
        config=config,
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.PASSIVE,
            routes=lambda: None,
            transfer_intent=_typed_transfer_intent_provider(state_dir=tmp_path, config=config),
        ),
        state_dir=tmp_path,
        clock=lambda: 30.0,
        boot_id=lambda: "boot-a",
    )

    snapshot = adapter.observe()

    if expected_discarded:
        assert snapshot.transfer_intent is None
        assert snapshot.transfer_effect_started is False
        assert not lineage_path.exists()
    else:
        assert snapshot.transfer_intent is TransferIntent.AUTOMATIC_FAILOVER
        assert snapshot.transfer_effect_started is True
        assert lineage_path.exists()


def test_apply_owner_adoption_retires_exact_prior_generation_promotion_receipt(
    tmp_path: Path,
) -> None:
    config = _passive_runtime_config()
    (tmp_path / "guard.json").write_text(json.dumps({"guard_boot_id": "boot-a"}), encoding="utf-8")
    receipt = _promotion_receipt()
    receipt["generation_id"] = "d" * 64
    receipt["digests"] = {
        "configuration": "d" * 64,
        "static_routes": "e" * 64,
        "bgp_policy": "f" * 64,
    }
    receipt_path = tmp_path / "promotion-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    (tmp_path / "apply-owner-adoption.json").write_text(
        json.dumps(_apply_owner_adoption()), encoding="utf-8"
    )
    (tmp_path / "transfer-lineage.json").write_text(
        json.dumps(_automatic_lineage()), encoding="utf-8"
    )
    cloud = CloudObservation(
        True,
        "allocation-a",
        "node-b",
        "node-a",
        ComputeState.RUNNING,
        True,
        True,
        True,
        "9",
    )
    adapter = VMHASnapshotAdapter(
        config=config,
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.PASSIVE,
            routes=lambda: None,
            apply_lock_operation_id=lambda: "f" * 64,
            transfer_intent=_typed_transfer_intent_provider(state_dir=tmp_path, config=config),
        ),
        state_dir=tmp_path,
        clock=lambda: 30.0,
        boot_id=lambda: "boot-a",
    )

    snapshot = adapter.observe()

    assert snapshot.apply_locked is True
    assert snapshot.apply_owner_adoption is True
    assert snapshot.transfer_intent is None
    assert snapshot.transfer_effect_started is False
    assert not receipt_path.exists()
    assert not (tmp_path / "transfer-lineage.json").exists()


@pytest.mark.parametrize("apply_locked", [False, True])
def test_prior_generation_promotion_receipt_retirement_remains_fail_closed(
    tmp_path: Path,
    apply_locked: bool,
) -> None:
    config = _passive_runtime_config()
    (tmp_path / "guard.json").write_text(json.dumps({"guard_boot_id": "boot-a"}), encoding="utf-8")
    receipt = _promotion_receipt()
    receipt["generation_id"] = "d" * 64
    receipt["digests"] = {
        "configuration": "d" * 64,
        "static_routes": "e" * 64,
        "bgp_policy": "f" * 64,
    }
    if apply_locked:
        receipt["cluster_id"] = "foreign-cluster"
    receipt_path = tmp_path / "promotion-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    (tmp_path / "transfer-lineage.json").write_text(
        json.dumps(_automatic_lineage()), encoding="utf-8"
    )
    cloud = CloudObservation(
        True,
        "allocation-a",
        "node-b",
        "node-a",
        ComputeState.RUNNING,
        True,
        True,
        True,
        "9",
    )
    adapter = VMHASnapshotAdapter(
        config=config,
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.PASSIVE,
            routes=lambda: None,
            apply_lock_operation_id=lambda: "f" * 64 if apply_locked else None,
            transfer_intent=_typed_transfer_intent_provider(state_dir=tmp_path, config=config),
        ),
        state_dir=tmp_path,
        clock=lambda: 30.0,
        boot_id=lambda: "boot-a",
    )

    with pytest.raises(ValueError, match="invalid or stale"):
        adapter.observe()

    assert receipt_path.exists()


def _prior_generation_active_receipt() -> dict[str, object]:
    receipt = _promotion_receipt(owner="node-a")
    receipt["intent"] = TransferIntent.PLANNED_FAILBACK.value
    receipt["former_owner_node_id"] = "node-b"
    receipt["generation_id"] = "d" * 64
    receipt["digests"] = {
        "configuration": "d" * 64,
        "static_routes": "e" * 64,
        "bgp_policy": "f" * 64,
    }
    return receipt


def _planned_failback_pre_effect_cloud() -> CloudObservation:
    return CloudObservation(
        authoritative=True,
        allocation_id="allocation-a",
        observed_owner_node_id="node-b",
        former_owner_node_id="node-b",
        former_owner_compute_state=ComputeState.RUNNING,
        former_attachment_absent=False,
        candidate_attachment_exact=False,
        ownership_re_read_exact=False,
        ownership_epoch="9",
        former_attachment_exact=True,
        candidate_attachment_absent=True,
    )


def test_first_planned_failback_effect_retires_exact_prior_generation_receipt(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "promotion-receipt.json"
    receipt_path.write_text(json.dumps(_prior_generation_active_receipt()), encoding="utf-8")
    action = ControllerAction(
        ActionKind.STOP_FORMER_OWNER,
        "boot-a:1:stop-former-owner:node-b",
        "boot-a",
        "node-b",
        "allocation-a",
        "9",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    snapshots = SimpleNamespace(
        last_snapshot=SimpleNamespace(
            transfer_intent=TransferIntent.PLANNED_FAILBACK,
            transfer_effect_started=False,
            configured_role=ConfiguredRole.ACTIVE,
            cluster_id="cluster-a",
            local_node_id="node-a",
            peer_node_id="node-b",
            cloud=_planned_failback_pre_effect_cloud(),
            local_generation_id="a" * 64,
            local_digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        )
    )
    lineage_path = tmp_path / "transfer-lineage.json"
    policy = AutoHealingPolicyStore(tmp_path).initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=10.0,
    )
    heartbeat = PeerHeartbeat(
        cluster_id="cluster-a",
        node_id="node-b",
        boot_id="boot-b",
        sequence=1,
        sent_at="1970-01-01T00:00:20Z",
        configured_role="passive",
        observed_owner_id="node-b",
        generation_id="a" * 64,
        mtls_epoch=1,
        certificate_fingerprint="d" * 64,
        digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        service_healthy=True,
        route_ready=True,
        promotion_ready=False,
        auto_healing_policy_state="enabled",
        auto_healing_policy_digest=policy.decision_digest,
    )
    certificate = PolicyAgreementStore(tmp_path).capture(
        policy=policy,
        heartbeat=heartbeat,
        captured_at=20.0,
    )
    request = {
        "schema": "nebius-vpngw/vm-ha-manual-failback-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
        "requested_at": 19.0,
    }
    (tmp_path / "manual-failback.json").write_text(
        json.dumps(request),
        encoding="utf-8",
    )
    StandbyRestorationStore(tmp_path).arm(
        source=RestorationSource.PLANNED_FAILBACK,
        certificate=certificate,
        owner_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
        generation_id="a" * 64,
        digests=certificate.digests,
        request_fingerprint=planned_request_fingerprint(request),
        first_operation_id=None,
        updated_at=20.0,
    )

    _record_transfer_effect_lineage(
        action=action,
        snapshots=snapshots,
        config=_runtime_config(),
        path=lineage_path,
        clock=lambda: 20.0,
    )

    assert not receipt_path.exists()
    assert json.loads(lineage_path.read_text(encoding="utf-8")) == {
        "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
        "intent": "planned-failback",
        "cluster_id": "cluster-a",
        "candidate_node_id": "node-a",
        "former_owner_node_id": "node-b",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "first_operation_id": "boot-a:1:stop-former-owner:node-b",
        "started_at": 20.0,
    }


@pytest.mark.parametrize(
    ("intent", "effect_started", "owner_node_id"),
    [
        (TransferIntent.PLANNED_FAILBACK, True, "node-b"),
        (TransferIntent.PLANNED_FAILBACK, False, "node-a"),
    ],
)
def test_prior_generation_receipt_retirement_requires_exact_fresh_planned_transfer(
    tmp_path: Path,
    intent: TransferIntent | None,
    effect_started: bool,
    owner_node_id: str,
) -> None:
    receipt_path = tmp_path / "promotion-receipt.json"
    receipt_path.write_text(json.dumps(_prior_generation_active_receipt()), encoding="utf-8")
    cloud = _planned_failback_pre_effect_cloud()
    if owner_node_id != "node-b":
        cloud = CloudObservation(
            **{
                **cloud.__dict__,
                "observed_owner_node_id": owner_node_id,
            }
        )
    action = ControllerAction(
        ActionKind.STOP_FORMER_OWNER,
        "boot-a:1:stop-former-owner:node-b",
        "boot-a",
        "node-b",
        "allocation-a",
        "9",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    snapshots = SimpleNamespace(
        last_snapshot=SimpleNamespace(
            transfer_intent=intent,
            transfer_effect_started=effect_started,
            configured_role=ConfiguredRole.ACTIVE,
            cluster_id="cluster-a",
            local_node_id="node-a",
            peer_node_id="node-b",
            cloud=cloud,
            local_generation_id="a" * 64,
            local_digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        )
    )

    with pytest.raises(ValueError, match="invalid or stale"):
        _record_transfer_effect_lineage(
            action=action,
            snapshots=snapshots,
            config=_runtime_config(),
            path=tmp_path / "transfer-lineage.json",
        )

    assert receipt_path.exists()


@pytest.mark.parametrize(
    ("action_kind", "former_state"),
    [
        (ActionKind.STOP_FORMER_OWNER, ComputeState.RUNNING),
        (ActionKind.DETACH_FORMER_ATTACHMENT, ComputeState.STOPPED),
    ],
)
def test_first_reverse_automatic_effect_retires_exact_prior_generation_receipt(
    tmp_path: Path,
    action_kind: ActionKind,
    former_state: ComputeState,
) -> None:
    receipt_path = tmp_path / "promotion-receipt.json"
    receipt_path.write_text(json.dumps(_prior_generation_active_receipt()), encoding="utf-8")
    action = ControllerAction(
        action_kind,
        f"boot-a:1:{action_kind.value}:node-b",
        "boot-a",
        "node-b",
        "allocation-a",
        "9",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    snapshots = SimpleNamespace(
        last_snapshot=SimpleNamespace(
            transfer_intent=None,
            transfer_effect_started=False,
            configured_role=ConfiguredRole.ACTIVE,
            cluster_id="cluster-a",
            local_node_id="node-a",
            peer_node_id="node-b",
            cloud=CloudObservation(
                **{
                    **_planned_failback_pre_effect_cloud().__dict__,
                    "former_owner_compute_state": former_state,
                }
            ),
            local_generation_id="a" * 64,
            local_digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        )
    )
    lineage_path = tmp_path / "transfer-lineage.json"

    _record_transfer_effect_lineage(
        action=action,
        snapshots=snapshots,
        config=_runtime_config(),
        path=lineage_path,
        clock=lambda: 20.0,
    )

    assert not receipt_path.exists()
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    assert lineage["intent"] == TransferIntent.AUTOMATIC_FAILOVER.value
    assert lineage["candidate_node_id"] == "node-a"
    assert lineage["former_owner_node_id"] == "node-b"


def test_promotion_receipt_requires_consumed_request_and_terminal_postconditions(
    tmp_path: Path,
) -> None:
    config = _runtime_config()
    generation = config["generation"]
    lineage_path = tmp_path / "transfer-lineage.json"
    receipt_path = tmp_path / "promotion-receipt.json"
    request_path = tmp_path / "manual-failback.json"
    lineage_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
                "intent": "planned-failback",
                "cluster_id": "cluster-a",
                "candidate_node_id": "node-a",
                "former_owner_node_id": "node-b",
                "allocation_id": "allocation-a",
                "generation_id": "a" * 64,
                "digests": generation["digests"],  # type: ignore[index]
                "first_operation_id": "first-effect",
                "started_at": 10.0,
            }
        ),
        encoding="utf-8",
    )
    request_path.write_text("{}", encoding="utf-8")
    status = {
        "promotion_ready": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": "node-a",
        "cluster_id": "cluster-a",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": generation["digests"],  # type: ignore[index]
        "former_owner_compute_state": "stopped",
        "former_attachment_absent": True,
        "candidate_attachment_exact": True,
        "ownership_re_read_exact": True,
        "apply_locked": False,
        "pending_operation_id": None,
        "ownership_epoch": "42",
        "route_reconciliation": {
            "operation_id": "route-effect",
            "owner_node_id": "node-a",
            "allocation_id": "allocation-a",
        },
    }

    assert (
        _commit_promotion_receipt(
            status=status,
            config=config,
            lineage_path=lineage_path,
            receipt_path=receipt_path,
            request_paths=(request_path,),
            clock=lambda: 12.0,
        )
        is None
    )
    assert not receipt_path.exists()
    request_path.unlink()

    with pytest.raises(
        StandbyRestorationError,
        match="restoration authorization is unavailable",
    ):
        _commit_promotion_receipt(
            status=status,
            config=config,
            lineage_path=lineage_path,
            receipt_path=receipt_path,
            request_paths=(request_path,),
            clock=lambda: 12.0,
        )

    assert receipt_path.exists()
    assert lineage_path.exists()

    policy = AutoHealingPolicyStore(tmp_path).initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=10.0,
    )
    certificate = PolicyAgreementStore(tmp_path).capture(
        policy=policy,
        heartbeat=PeerHeartbeat(
            cluster_id="cluster-a",
            node_id="node-b",
            boot_id="boot-b",
            sequence=1,
            sent_at="1970-01-01T00:00:20Z",
            configured_role="passive",
            observed_owner_id="node-a",
            generation_id="a" * 64,
            mtls_epoch=1,
            certificate_fingerprint="d" * 64,
            digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
            service_healthy=True,
            route_ready=True,
            promotion_ready=False,
            auto_healing_policy_state="enabled",
            auto_healing_policy_digest=policy.decision_digest,
        ),
        captured_at=20.0,
    )
    StandbyRestorationStore(tmp_path).arm(
        source=RestorationSource.PLANNED_FAILBACK,
        certificate=certificate,
        owner_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
        generation_id="a" * 64,
        digests=certificate.digests,
        request_fingerprint="e" * 64,
        first_operation_id="first-effect",
        updated_at=20.0,
    )
    receipt = _commit_promotion_receipt(
        status=status,
        config=config,
        lineage_path=lineage_path,
        receipt_path=receipt_path,
        request_paths=(request_path,),
        clock=lambda: 12.0,
    )

    assert receipt is not None
    assert receipt["intent"] == "planned-failback"
    assert receipt["common_cutover_seconds"] == 2.0
    assert receipt_path.exists()
    assert not lineage_path.exists()
    committed = StandbyRestorationStore(tmp_path).load()
    assert committed is not None
    assert committed.phase is RestorationPhase.COMMITTED
    assert committed.promotion_receipt_id == receipt["receipt_id"]


def test_automatic_promotion_commit_ignores_unusable_restoration_evidence(
    tmp_path: Path,
) -> None:
    config = _passive_runtime_config()
    generation = config["generation"]
    lineage_path = tmp_path / "transfer-lineage.json"
    lineage_path.write_text(json.dumps(_automatic_lineage()), encoding="utf-8")
    receipt_path = tmp_path / "promotion-receipt.json"
    (tmp_path / RESTORATION_AUTHORIZATION_FILENAME).write_text("{", encoding="utf-8")
    status = {
        "promotion_ready": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": "node-b",
        "cluster_id": "cluster-a",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": generation["digests"],  # type: ignore[index]
        "former_owner_compute_state": "stopped",
        "former_attachment_absent": True,
        "candidate_attachment_exact": True,
        "ownership_re_read_exact": True,
        "apply_locked": False,
        "pending_operation_id": None,
        "ownership_epoch": "42",
        "route_reconciliation": {
            "operation_id": "route-effect",
            "owner_node_id": "node-b",
            "allocation_id": "allocation-a",
        },
    }

    receipt = _commit_promotion_receipt(
        status=status,
        config=config,
        lineage_path=lineage_path,
        receipt_path=receipt_path,
        request_paths=(),
        clock=lambda: 12.0,
    )

    assert receipt is not None
    assert receipt["intent"] == "automatic-failover"
    assert receipt_path.exists()
    assert not lineage_path.exists()
    assert not (tmp_path / RESTORATION_AUTHORIZATION_FILENAME).exists()

    _advance_standby_restoration(status=status, state_dir=tmp_path, clock=lambda: 13.0)


def test_automatic_promotion_runtime_disables_unusable_restoration_across_steps(
    tmp_path: Path,
) -> None:
    config = _passive_runtime_config()
    generation = config["generation"]
    assert isinstance(generation, dict)
    lineage_path = tmp_path / "transfer-lineage.json"
    lineage_path.write_text(json.dumps(_automatic_lineage()), encoding="utf-8")
    receipt_path = tmp_path / "promotion-receipt.json"
    authorization_path = tmp_path / RESTORATION_AUTHORIZATION_FILENAME
    authorization_path.write_text("{", encoding="utf-8")
    status = {
        "promotion_ready": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": "node-b",
        "cluster_id": "cluster-a",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": generation["digests"],
        "former_owner_compute_state": "stopped",
        "former_attachment_absent": True,
        "candidate_attachment_exact": True,
        "ownership_re_read_exact": True,
        "apply_locked": False,
        "pending_operation_id": None,
        "ownership_epoch": "42",
        "route_reconciliation": {
            "operation_id": "route-effect",
            "owner_node_id": "node-b",
            "allocation_id": "allocation-a",
        },
    }

    class Peer:
        def poll(self, *, timeout_seconds: float) -> None:
            raise PeerTransportError(f"offline peer fixture {timeout_seconds}")

        def send(self, _heartbeat: object) -> None:
            return None

    class Ports:
        local = type(
            "Local",
            (),
            {"node_id": "node-b", "role": type("Role", (), {"value": "passive"})()},
        )()
        peer_runtime = Peer()

        def heartbeat(self, **_kwargs: object) -> object:
            return object()

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            return None

        def close(self) -> None:
            return None

    class Controller:
        def step(self) -> dict[str, object]:
            return status

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-b",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
        config=config,
        transfer_lineage_path=lineage_path,
        promotion_receipt_path=receipt_path,
        clock=lambda: 12.0,
    )
    try:
        assert runtime.step() == status
        assert runtime.step() == status
    finally:
        runtime.close(restore_guard=False)

    assert receipt_path.exists()
    assert not lineage_path.exists()
    assert not authorization_path.exists()


def _awaiting_restoration(tmp_path: Path) -> StandbyRestorationStore:
    policy = AutoHealingPolicyStore(tmp_path).initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=10.0,
    )
    certificate = PolicyAgreementStore(tmp_path).capture(
        policy=policy,
        heartbeat=PeerHeartbeat(
            cluster_id="cluster-a",
            node_id="node-b",
            boot_id="boot-b",
            sequence=1,
            sent_at="1970-01-01T00:00:20Z",
            configured_role="passive",
            observed_owner_id="node-a",
            generation_id="a" * 64,
            mtls_epoch=1,
            certificate_fingerprint="d" * 64,
            digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
            service_healthy=True,
            route_ready=True,
            promotion_ready=False,
            auto_healing_policy_state="enabled",
            auto_healing_policy_digest=policy.decision_digest,
        ),
        captured_at=20.0,
    )
    store = StandbyRestorationStore(tmp_path)
    store.arm(
        source=RestorationSource.PLANNED_FAILBACK,
        certificate=certificate,
        owner_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
        generation_id="a" * 64,
        digests=certificate.digests,
        request_fingerprint="e" * 64,
        first_operation_id="first-effect",
        updated_at=20.0,
    )
    committed = store.commit(
        receipt=_durable_restoration_receipt(tmp_path, certificate.digests),
        updated_at=21.0,
    )
    receipt_id = str(committed.promotion_receipt_id)
    store.accept_start(
        receipt_id=receipt_id,
        stopped_revision="revision-9",
        rearm_operation_id="rearm-operation",
        updated_at=22.0,
    )
    store.mark_running(receipt_id=receipt_id, updated_at=23.0)
    store.await_standby(receipt_id=receipt_id, updated_at=24.0)
    return store


def _redundancy_status(**overrides: object) -> dict[str, object]:
    status: dict[str, object] = {
        "redundancy_ready": True,
        "observed_owner_node_id": "node-a",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
    }
    status.update(overrides)
    return status


def test_advance_standby_restoration_completes_only_exact_redundancy(
    tmp_path: Path,
) -> None:
    store = _awaiting_restoration(tmp_path)

    _advance_standby_restoration(
        status=_redundancy_status(),
        state_dir=tmp_path,
        clock=lambda: 25.0,
    )

    record = store.load()
    assert record is not None
    assert record.phase is RestorationPhase.COMPLETED


def test_advance_standby_restoration_ignores_mismatched_redundancy(
    tmp_path: Path,
) -> None:
    store = _awaiting_restoration(tmp_path)

    _advance_standby_restoration(
        status=_redundancy_status(observed_owner_node_id="node-b"),
        state_dir=tmp_path,
        clock=lambda: 25.0,
    )

    record = store.load()
    assert record is not None
    assert record.phase is RestorationPhase.AWAITING_STANDBY


def test_advance_standby_restoration_blocks_after_its_deadline(tmp_path: Path) -> None:
    store = _awaiting_restoration(tmp_path)

    _advance_standby_restoration(
        status=_redundancy_status(redundancy_ready=False),
        state_dir=tmp_path,
        clock=lambda: 324.0,
    )

    record = store.load()
    assert record is not None
    assert record.phase is RestorationPhase.BLOCKED
    assert record.blocked_reason == "standby-readiness-timeout"


def test_default_runtime_defers_restoration_advance_while_writer_lock_is_held(
    tmp_path: Path,
) -> None:
    store = _awaiting_restoration(tmp_path)
    status = _redundancy_status()

    class Peer:
        def poll(self, *, timeout_seconds: float) -> None:
            raise PeerTransportError(f"fixture timeout {timeout_seconds}")

        def send(self, _heartbeat: object) -> None:
            return None

    class Ports:
        peer_runtime = Peer()

        def heartbeat(self, **_kwargs: object) -> object:
            return object()

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            return None

        def close(self) -> None:
            return None

    class Controller:
        def step(self) -> dict[str, object]:
            return status

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
        promotion_receipt_path=tmp_path / "promotion-receipt.json",
        peer_retry_seconds=0.01,
        sleeper=lambda _seconds: None,
        clock=lambda: 25.0,
    )
    writer_lock = _acquire_rearm_lock(
        tmp_path / REARM_LOCK_PATH.name,
        timeout_seconds=0.0,
    )
    assert writer_lock is not None
    try:
        try:
            runtime.step()
            record = store.load()
            assert record is not None
            assert record.phase is RestorationPhase.AWAITING_STANDBY
        finally:
            os.close(writer_lock)

        runtime.step()
        record = store.load()
        assert record is not None
        assert record.phase is RestorationPhase.COMPLETED
    finally:
        runtime.close(restore_guard=False)


@pytest.mark.parametrize(
    ("restoration_phase", "expected_state"),
    (
        (RestorationPhase.COMMITTED, "transitioning"),
        (RestorationPhase.BLOCKED, "enabled"),
    ),
)
def test_auto_healing_projection_uses_exact_restoration_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restoration_phase: RestorationPhase,
    expected_state: str,
) -> None:
    policy = AutoHealingPolicyStore(tmp_path).initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=10.0,
    )
    certificate = PolicyAgreementStore(tmp_path).capture(
        policy=policy,
        heartbeat=PeerHeartbeat(
            cluster_id="cluster-a",
            node_id="node-b",
            boot_id="boot-b",
            sequence=1,
            sent_at="1970-01-01T00:00:20Z",
            configured_role="passive",
            observed_owner_id="node-a",
            generation_id="a" * 64,
            mtls_epoch=1,
            certificate_fingerprint="d" * 64,
            digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
            service_healthy=True,
            route_ready=True,
            promotion_ready=False,
            auto_healing_policy_state="enabled",
            auto_healing_policy_digest=policy.decision_digest,
        ),
        captured_at=20.0,
    )
    store = StandbyRestorationStore(tmp_path)
    store.arm(
        source=RestorationSource.PLANNED_FAILBACK,
        certificate=certificate,
        owner_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
        generation_id="a" * 64,
        digests=certificate.digests,
        request_fingerprint="e" * 64,
        first_operation_id="first-effect",
        updated_at=20.0,
    )
    committed = store.commit(
        receipt=_durable_restoration_receipt(tmp_path, certificate.digests),
        updated_at=21.0,
    )
    if restoration_phase is RestorationPhase.BLOCKED:
        store.block(
            receipt_id=str(committed.promotion_receipt_id),
            reason="automatic-retry-exhausted",
            updated_at=22.0,
        )
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.vm_ha_auto_healing_policy",
        lambda *_args, **_kwargs: {
            "desired": "enabled",
            "phase": "committed",
            "peer_agrees": False,
            "accepted_start": False,
            "recovery_phase": None,
            "record": policy.to_dict(),
        },
    )

    projected = _project_auto_healing_status({}, state_dir=tmp_path)

    assert projected["auto_healing"] == {
        "state": expected_state,
        "peer_agrees": False,
        "accepted_start": False,
    }


def test_auto_healing_projection_blocks_malformed_restoration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = AutoHealingPolicyStore(tmp_path).initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=10.0,
    )
    (tmp_path / RESTORATION_AUTHORIZATION_FILENAME).write_text("{", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.vm_ha_auto_healing_policy",
        lambda *_args, **_kwargs: {
            "desired": "enabled",
            "phase": "committed",
            "peer_agrees": False,
            "accepted_start": False,
            "recovery_phase": None,
            "record": policy.to_dict(),
        },
    )

    projected = _project_auto_healing_status({}, state_dir=tmp_path)

    assert projected["auto_healing"] == {
        "state": "blocked",
        "peer_agrees": False,
        "accepted_start": False,
    }


@pytest.mark.parametrize(
    ("cluster_id", "peer_node_id", "generation_id"),
    (
        ("cluster-a", "node-b", "b" * 64),
        ("cluster-b", "node-b", "a" * 64),
        ("cluster-a", "node-c", "a" * 64),
    ),
)
def test_auto_healing_projection_blocks_foreign_restoration_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cluster_id: str,
    peer_node_id: str,
    generation_id: str,
) -> None:
    local_policy = AutoHealingPolicyStore(tmp_path).initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=10.0,
    )
    certificate = PolicyAgreementStore(tmp_path).capture(
        policy=local_policy,
        heartbeat=PeerHeartbeat(
            cluster_id="cluster-a",
            node_id="node-b",
            boot_id="boot-b",
            sequence=1,
            sent_at="1970-01-01T00:00:20Z",
            configured_role="passive",
            observed_owner_id="node-a",
            generation_id="a" * 64,
            mtls_epoch=1,
            certificate_fingerprint="d" * 64,
            digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
            service_healthy=True,
            route_ready=True,
            promotion_ready=False,
            auto_healing_policy_state="enabled",
            auto_healing_policy_digest=local_policy.decision_digest,
        ),
        captured_at=20.0,
    )
    StandbyRestorationStore(tmp_path).arm(
        source=RestorationSource.PLANNED_FAILBACK,
        certificate=certificate,
        owner_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
        generation_id="a" * 64,
        digests=certificate.digests,
        request_fingerprint="e" * 64,
        first_operation_id="first-effect",
        updated_at=20.0,
    )
    foreign_policy = AutoHealingPolicyStore(tmp_path / "foreign").initialize(
        cluster_id=cluster_id,
        node_id="node-a",
        peer_node_id=peer_node_id,
        generation_id=generation_id,
        updated_at=21.0,
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.vm_ha_auto_healing_policy",
        lambda *_args, **_kwargs: {
            "desired": "enabled",
            "phase": "committed",
            "peer_agrees": False,
            "accepted_start": False,
            "recovery_phase": None,
            "record": foreign_policy.to_dict(),
        },
    )

    projected = _project_auto_healing_status({}, state_dir=tmp_path)

    assert projected["auto_healing"]["state"] == "blocked"


def test_auto_healing_projection_ignores_completed_restoration_after_policy_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = AutoHealingPolicyStore(tmp_path).initialize(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        updated_at=10.0,
    )
    certificate = PolicyAgreementStore(tmp_path).capture(
        policy=policy,
        heartbeat=PeerHeartbeat(
            cluster_id="cluster-a",
            node_id="node-b",
            boot_id="boot-b",
            sequence=1,
            sent_at="1970-01-01T00:00:20Z",
            configured_role="passive",
            observed_owner_id="node-a",
            generation_id="a" * 64,
            mtls_epoch=1,
            certificate_fingerprint="d" * 64,
            digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
            service_healthy=True,
            route_ready=True,
            promotion_ready=False,
            auto_healing_policy_state="enabled",
            auto_healing_policy_digest=policy.decision_digest,
        ),
        captured_at=20.0,
    )
    store = StandbyRestorationStore(tmp_path)
    store.arm(
        source=RestorationSource.PLANNED_FAILBACK,
        certificate=certificate,
        owner_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
        generation_id="a" * 64,
        digests=certificate.digests,
        request_fingerprint="e" * 64,
        first_operation_id="first-effect",
        updated_at=20.0,
    )
    committed = store.commit(
        receipt=_durable_restoration_receipt(tmp_path, certificate.digests),
        updated_at=21.0,
    )
    receipt_id = str(committed.promotion_receipt_id)
    store.accept_start(
        receipt_id=receipt_id,
        stopped_revision="revision-9",
        rearm_operation_id="rearm-operation",
        updated_at=22.0,
    )
    store.mark_running(receipt_id=receipt_id, updated_at=23.0)
    store.await_standby(receipt_id=receipt_id, updated_at=24.0)
    store.complete(receipt_id=receipt_id, updated_at=25.0)

    operation_id = "f" * 64
    decision_digest = policy_decision_digest(
        cluster_id="cluster-a",
        member_node_ids=("node-a", "node-b"),
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=policy.decision_digest,
    )
    disabled_policy = AutoHealingPolicyRecord(
        cluster_id="cluster-a",
        node_id="node-a",
        peer_node_id="node-b",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id=operation_id,
        coordinator_node_id="node-a",
        predecessor_digest=policy.decision_digest,
        phase=AutoHealingPolicyPhase.COMMITTED,
        decision_digest=decision_digest,
        peer_ack_digest=decision_digest,
        updated_at=26.0,
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.vm_ha_auto_healing_policy",
        lambda *_args, **_kwargs: {
            "desired": "disabled",
            "phase": "committed",
            "peer_agrees": True,
            "accepted_start": False,
            "recovery_phase": None,
            "record": disabled_policy.to_dict(),
        },
    )

    projected = _project_auto_healing_status({}, state_dir=tmp_path)

    assert projected["auto_healing"] == {
        "state": "disabled",
        "peer_agrees": True,
        "accepted_start": False,
    }


@pytest.mark.parametrize(
    "intent",
    [TransferIntent.PLANNED_FAILBACK, TransferIntent.AUTOMATIC_FAILOVER],
)
def test_status_projects_only_current_exact_committed_promotion(
    tmp_path: Path,
    intent: TransferIntent,
) -> None:
    config = _runtime_config()
    receipt = _promotion_receipt(owner="node-a", epoch="7")
    receipt["intent"] = intent.value
    receipt["former_owner_node_id"] = "node-b"
    receipt_path = tmp_path / "promotion-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
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
    route_context = RouteReconciliationContext(
        owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        generation_id="a" * 64,
        digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        route_runtime_id=str(config["runtime_binding"]["route_runtime_id"]),  # type: ignore[index]
        operation_id="route-operation-a",
    )
    snapshot = SimpleNamespace(
        apply_locked=False,
        cloud=cloud,
        local_node_id="node-a",
        local_generation_id="a" * 64,
        local_digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
    )

    assert (
        _promotion_committed_for_status(
            snapshot=snapshot,
            config=config,
            route_context=route_context,
            receipt_path=receipt_path,
            promotion_ready=True,
            pending_operation=None,
        )
        is True
    )

    receipt["generation_id"] = "d" * 64
    receipt["digests"] = {
        "configuration": "d" * 64,
        "static_routes": "e" * 64,
        "bgp_policy": "f" * 64,
    }
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert (
        _promotion_committed_for_status(
            snapshot=snapshot,
            config=config,
            route_context=route_context,
            receipt_path=receipt_path,
            promotion_ready=True,
            pending_operation=None,
        )
        is False
    )


def test_apply_owner_adoption_commits_current_generation_terminal_receipt(
    tmp_path: Path,
) -> None:
    config = _passive_runtime_config()
    adoption_path = tmp_path / "apply-owner-adoption.json"
    adoption_path.write_text(json.dumps(_apply_owner_adoption()), encoding="utf-8")
    receipt_path = tmp_path / "promotion-receipt.json"
    status = {
        "promotion_ready": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": "node-b",
        "cluster_id": "cluster-a",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "former_owner_compute_state": "running",
        "former_attachment_absent": True,
        "candidate_attachment_exact": True,
        "ownership_re_read_exact": True,
        "apply_locked": False,
        "pending_operation_id": None,
        "ownership_epoch": "42",
        "route_reconciliation": {
            "operation_id": "route-effect",
            "owner_node_id": "node-b",
            "allocation_id": "allocation-a",
        },
    }

    receipt = _commit_promotion_receipt(
        status=status,
        config=config,
        lineage_path=tmp_path / "transfer-lineage.json",
        receipt_path=receipt_path,
        adoption_path=adoption_path,
        request_paths=(),
        clock=lambda: 12.0,
    )

    assert receipt is not None
    assert receipt["intent"] == "apply-owner-adoption"
    assert receipt["generation_id"] == "a" * 64
    assert receipt["route_operation_id"] == "route-effect"
    assert receipt["common_cutover_seconds"] == 0.0
    assert receipt_path.exists()
    assert not adoption_path.exists()


def test_apply_owner_adoption_retires_completed_restoration_before_receipt_replacement(
    tmp_path: Path,
) -> None:
    store = _awaiting_restoration(tmp_path)
    restoration = store.load()
    assert restoration is not None
    completed = store.complete(
        receipt_id=str(restoration.promotion_receipt_id),
        updated_at=22.0,
    )
    adoption_path = tmp_path / "apply-owner-adoption.json"
    adoption_path.write_text(
        json.dumps(
            {
                **_apply_owner_adoption(),
                "node_id": "node-a",
                "peer_node_id": "node-b",
            }
        ),
        encoding="utf-8",
    )
    status = {
        "promotion_ready": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": "node-a",
        "cluster_id": "cluster-a",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "former_owner_compute_state": "running",
        "former_attachment_absent": True,
        "candidate_attachment_exact": True,
        "ownership_re_read_exact": True,
        "apply_locked": False,
        "pending_operation_id": None,
        "ownership_epoch": "42",
        "route_reconciliation": {
            "operation_id": "route-effect",
            "owner_node_id": "node-a",
            "allocation_id": "allocation-a",
        },
    }

    receipt = _commit_promotion_receipt(
        status=status,
        config=_runtime_config(),
        lineage_path=tmp_path / "transfer-lineage.json",
        receipt_path=store.receipt_path,
        adoption_path=adoption_path,
        request_paths=(),
        clock=lambda: 23.0,
    )

    assert receipt is not None
    assert receipt["intent"] == "apply-owner-adoption"
    assert not store.path.exists()
    assert not adoption_path.exists()
    assert completed.phase is RestorationPhase.COMPLETED


def test_apply_owner_adoption_preserves_active_restoration_and_prior_receipt(
    tmp_path: Path,
) -> None:
    store = _awaiting_restoration(tmp_path)
    prior_receipt = store.receipt_path.read_bytes()
    adoption_path = tmp_path / "apply-owner-adoption.json"
    adoption_path.write_text(
        json.dumps(
            {
                **_apply_owner_adoption(),
                "node_id": "node-a",
                "peer_node_id": "node-b",
            }
        ),
        encoding="utf-8",
    )
    status = {
        "promotion_ready": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": "node-a",
        "cluster_id": "cluster-a",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": {
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        "former_owner_compute_state": "running",
        "former_attachment_absent": True,
        "candidate_attachment_exact": True,
        "ownership_re_read_exact": True,
        "apply_locked": False,
        "pending_operation_id": None,
        "ownership_epoch": "42",
        "route_reconciliation": {
            "operation_id": "route-effect",
            "owner_node_id": "node-a",
            "allocation_id": "allocation-a",
        },
    }

    with pytest.raises(
        StandbyRestorationError,
        match="active standby restoration conflicts with apply owner adoption",
    ):
        _commit_promotion_receipt(
            status=status,
            config=_runtime_config(),
            lineage_path=tmp_path / "transfer-lineage.json",
            receipt_path=store.receipt_path,
            adoption_path=adoption_path,
            request_paths=(),
            clock=lambda: 23.0,
        )

    assert store.receipt_path.read_bytes() == prior_receipt
    assert store.load() is not None
    assert adoption_path.exists()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("promotion_ready", False),
        ("data_plane_mode", "passive"),
        ("observed_owner_node_id", "node-b"),
        ("cluster_id", "foreign-cluster"),
        ("allocation_id", "foreign-allocation"),
        ("generation_id", "b" * 64),
        ("digests", {"configuration": "f" * 64}),
        ("former_owner_compute_state", "running"),
        ("former_attachment_absent", False),
        ("candidate_attachment_exact", False),
        ("ownership_re_read_exact", False),
        ("ownership_epoch", ""),
        ("apply_locked", True),
        ("pending_operation_id", "pending-effect"),
        ("route_reconciliation", None),
        ("route_reconciliation.operation_id", ""),
        ("route_reconciliation.owner_node_id", "node-b"),
        ("route_reconciliation.allocation_id", "foreign-allocation"),
    ],
)
def test_promotion_receipt_fails_closed_for_each_terminal_gate(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    config = _runtime_config()
    generation = config["generation"]
    lineage_path = tmp_path / "transfer-lineage.json"
    receipt_path = tmp_path / "promotion-receipt.json"
    lineage_path.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-transfer-lineage-v1",
                "intent": "planned-failback",
                "cluster_id": "cluster-a",
                "candidate_node_id": "node-a",
                "former_owner_node_id": "node-b",
                "allocation_id": "allocation-a",
                "generation_id": "a" * 64,
                "digests": generation["digests"],  # type: ignore[index]
                "first_operation_id": "first-effect",
                "started_at": 10.0,
            }
        ),
        encoding="utf-8",
    )
    status: dict[str, object] = {
        "promotion_ready": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": "node-a",
        "cluster_id": "cluster-a",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": generation["digests"],  # type: ignore[index]
        "former_owner_compute_state": "stopped",
        "former_attachment_absent": True,
        "candidate_attachment_exact": True,
        "ownership_re_read_exact": True,
        "apply_locked": False,
        "pending_operation_id": None,
        "ownership_epoch": "42",
        "route_reconciliation": {
            "operation_id": "route-effect",
            "owner_node_id": "node-a",
            "allocation_id": "allocation-a",
        },
    }
    if field.startswith("route_reconciliation."):
        route = status["route_reconciliation"]
        assert isinstance(route, dict)
        route[field.removeprefix("route_reconciliation.")] = invalid_value
    else:
        status[field] = invalid_value

    assert (
        _commit_promotion_receipt(
            status=status,
            config=config,
            lineage_path=lineage_path,
            receipt_path=receipt_path,
            request_paths=(),
            clock=lambda: 12.0,
        )
        is None
    )
    assert lineage_path.exists()
    assert not receipt_path.exists()


def test_effect_adapter_replays_completed_exact_operation_after_guard_restore(
    tmp_path: Path,
) -> None:
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
    assert calls == [action.operation_id, action.operation_id]
    assert json.loads((tmp_path / "effect.json").read_text())["operation_id"] == action.operation_id


def test_transfer_effect_rechecks_mtls_inhibition_after_controller_decision(
    tmp_path: Path,
) -> None:
    operation_id = "boot-a:1:stop-former-owner:node-b"
    action = ControllerAction(
        ActionKind.STOP_FORMER_OWNER,
        operation_id,
        "boot-a",
        "node-b",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    pending = ControllerCheckpoint(
        sequence=1,
        state=HAState.FENCING,
        pending_action=action,
    )
    decision = ControllerResult(
        state=HAState.FENCING,
        reasons=("former-owner-must-be-stopped",),
        forwarding_enabled=False,
        action=action,
        checkpoint=pending,
    )
    inhibition: list[str | None] = [None]
    calls: list[ActionKind] = []
    snapshot = SimpleNamespace(
        transfer_intent=None,
        transfer_effect_started=False,
    )
    snapshots = SimpleNamespace(
        providers=SimpleNamespace(
            apply_lock_operation_id=lambda: None,
            transfer_inhibition_operation_id=lambda: inhibition[0],
            transfer_intent=lambda: (None, False),
        ),
        last_snapshot=snapshot,
        observe=lambda: snapshot,
    )

    class _Checkpoints:
        current = ControllerCheckpoint()

        def load(self) -> ControllerCheckpoint:
            return self.current

        def save(self, checkpoint: ControllerCheckpoint) -> None:
            self.current = checkpoint

    effects = VMHAEffectAdapter(
        {kind: (lambda _action, kind=kind: calls.append(kind)) for kind in ActionKind},
        receipt_path=tmp_path / "effect.json",
        effect_guard=lambda candidate: _vm_ha_transfer_effect_guard(
            candidate,
            snapshots=snapshots,  # type: ignore[arg-type]
            state_dir=tmp_path,
        ),
    )
    controller = RecoverableController(
        policy=SimpleNamespace(decide=lambda _snapshot, _checkpoint: decision),
        snapshots=snapshots,
        checkpoints=_Checkpoints(),
        effects=effects,
        decision_observer=lambda _snapshot, _checkpoint, _decision: inhibition.__setitem__(
            0, "f" * 64
        ),
    )

    assert controller.step() is decision
    assert calls == []
    assert not (tmp_path / "effect.json").exists()


def test_transfer_effect_inhibition_allows_only_exact_owner_forwarding_restore(
    tmp_path: Path,
) -> None:
    operation_id = "f" * 64
    action = ControllerAction(
        ActionKind.ENABLE_ACTIVE,
        "boot-a:1:enable-active:node-a",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    routes = RouteReconciliationContext(
        owner_node_id="node-a",
        allocation_id=action.allocation_id,
        ownership_epoch=action.ownership_epoch,
        generation_id=action.generation_id,
        digests=action.digests,
        route_runtime_id="route-runtime-a",
        ownership_incarnation=action.ownership_incarnation,
        operation_id="route-effect",
    )
    snapshot = ControllerSnapshot(
        now=10.0,
        boot_id=action.boot_id,
        cluster_id="cluster-a",
        local_node_id=action.target_node_id,
        peer_node_id="node-b",
        configured_role=ConfiguredRole.ACTIVE,
        local_generation_id=action.generation_id,
        local_digests=action.digests,
        peer_heartbeat=None,
        peer_received_at=None,
        apply_locked=False,
        emergency_active_only=False,
        readiness=LocalReadiness(True, True, True, True),
        cloud=CloudObservation(
            authoritative=True,
            allocation_id=action.allocation_id,
            observed_owner_node_id=action.target_node_id,
            former_owner_node_id="node-b",
            former_owner_compute_state=ComputeState.STOPPED,
            former_attachment_absent=True,
            candidate_attachment_exact=True,
            ownership_re_read_exact=True,
            ownership_epoch=action.ownership_epoch,
        ),
        guard_boot_id=action.boot_id,
        data_plane_mode=DataPlaneMode.PASSIVE,
        routes_reconciled_context=routes,
        route_runtime_id=routes.route_runtime_id,
        transfer_inhibited=True,
    )
    current_inhibition = [operation_id]
    snapshots = SimpleNamespace(
        providers=SimpleNamespace(
            apply_lock_operation_id=lambda: None,
            transfer_inhibition_operation_id=lambda: current_inhibition[0],
            transfer_intent=lambda: (None, False),
        ),
        last_snapshot=snapshot,
        last_transfer_inhibition_operation_id=operation_id,
    )

    with _vm_ha_transfer_effect_guard(
        action,
        snapshots=snapshots,  # type: ignore[arg-type]
        state_dir=tmp_path,
    ) as permitted:
        assert permitted

    snapshots.last_snapshot = replace(snapshot, routes_reconciled_context=None)
    with _vm_ha_transfer_effect_guard(
        action,
        snapshots=snapshots,  # type: ignore[arg-type]
        state_dir=tmp_path,
    ) as permitted:
        assert not permitted

    snapshots.last_snapshot = snapshot
    current_inhibition[0] = "e" * 64
    with _vm_ha_transfer_effect_guard(
        action,
        snapshots=snapshots,  # type: ignore[arg-type]
        state_dir=tmp_path,
    ) as permitted:
        assert not permitted


def test_transfer_effect_inhibition_allows_exact_replacement_owner_preparation(
    tmp_path: Path,
) -> None:
    operation_id = "f" * 64
    action = ControllerAction(
        ActionKind.PREPARE_CANDIDATE_DATAPLANE,
        "boot-a:1:prepare-candidate-dataplane:node-a",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    snapshot = ControllerSnapshot(
        now=10.0,
        boot_id=action.boot_id,
        cluster_id="cluster-a",
        local_node_id=action.target_node_id,
        peer_node_id="node-b",
        configured_role=ConfiguredRole.ACTIVE,
        local_generation_id=action.generation_id,
        local_digests=action.digests,
        peer_heartbeat=None,
        peer_received_at=None,
        apply_locked=False,
        emergency_active_only=False,
        readiness=LocalReadiness(
            True,
            False,
            True,
            False,
            candidate_preparation_required=True,
            cold_standby_ready=True,
        ),
        cloud=CloudObservation(
            authoritative=True,
            allocation_id=action.allocation_id,
            observed_owner_node_id=action.target_node_id,
            former_owner_node_id="node-b",
            former_owner_compute_state=ComputeState.STOPPED,
            former_attachment_absent=True,
            candidate_attachment_exact=True,
            ownership_re_read_exact=True,
            ownership_epoch=action.ownership_epoch,
        ),
        guard_boot_id=action.boot_id,
        data_plane_mode=DataPlaneMode.PASSIVE,
        routes_reconciled_context=None,
        route_runtime_id="route-runtime-a",
        transfer_inhibited=True,
        standby_replacement_inhibited=True,
    )
    replacement_inhibition = [operation_id]
    snapshots = SimpleNamespace(
        providers=SimpleNamespace(
            apply_lock_operation_id=lambda: None,
            transfer_inhibition_operation_id=lambda: operation_id,
            standby_replacement_inhibition_operation_id=lambda: replacement_inhibition[0],
            transfer_intent=lambda: (None, False),
        ),
        last_snapshot=snapshot,
        last_transfer_inhibition_operation_id=operation_id,
    )

    with _vm_ha_transfer_effect_guard(
        action,
        snapshots=snapshots,  # type: ignore[arg-type]
        state_dir=tmp_path,
    ) as permitted:
        assert permitted

    replacement_inhibition[0] = None
    with _vm_ha_transfer_effect_guard(
        action,
        snapshots=snapshots,  # type: ignore[arg-type]
        state_dir=tmp_path,
    ) as permitted:
        assert not permitted


@pytest.mark.parametrize(
    "new_intent",
    (TransferIntent.PLANNED_FAILBACK, TransferIntent.PLANNED_FAILOVER),
)
def test_transfer_effect_rechecks_intent_after_controller_decision(
    tmp_path: Path,
    new_intent: TransferIntent,
) -> None:
    operation_id = "boot-a:1:stop-former-owner:node-b"
    action = ControllerAction(
        ActionKind.STOP_FORMER_OWNER,
        operation_id,
        "boot-a",
        "node-b",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    pending = ControllerCheckpoint(
        sequence=1,
        state=HAState.FENCING,
        pending_action=action,
    )
    decision = ControllerResult(
        state=HAState.FENCING,
        reasons=("former-owner-must-be-stopped",),
        forwarding_enabled=False,
        action=action,
        checkpoint=pending,
    )
    current_intent: list[tuple[TransferIntent | None, bool]] = [(None, False)]
    calls: list[ActionKind] = []
    snapshot = SimpleNamespace(
        transfer_intent=None,
        transfer_effect_started=False,
    )
    snapshots = SimpleNamespace(
        providers=SimpleNamespace(
            apply_lock_operation_id=lambda: None,
            transfer_inhibition_operation_id=lambda: None,
            transfer_intent=lambda: current_intent[0],
        ),
        last_snapshot=snapshot,
        observe=lambda: snapshot,
    )

    class _Checkpoints:
        current = ControllerCheckpoint()

        def load(self) -> ControllerCheckpoint:
            return self.current

        def save(self, checkpoint: ControllerCheckpoint) -> None:
            self.current = checkpoint

    effects = VMHAEffectAdapter(
        {kind: (lambda _action, kind=kind: calls.append(kind)) for kind in ActionKind},
        receipt_path=tmp_path / "effect.json",
        effect_guard=lambda candidate: _vm_ha_transfer_effect_guard(
            candidate,
            snapshots=snapshots,  # type: ignore[arg-type]
            state_dir=tmp_path,
        ),
    )
    controller = RecoverableController(
        policy=SimpleNamespace(decide=lambda _snapshot, _checkpoint: decision),
        snapshots=snapshots,
        checkpoints=_Checkpoints(),
        effects=effects,
        decision_observer=lambda _snapshot, _checkpoint, _decision: current_intent.__setitem__(
            0, (new_intent, False)
        ),
    )

    assert controller.step() is decision
    assert calls == []
    assert not (tmp_path / "effect.json").exists()


def test_effect_adapter_emits_structured_secret_free_timing_events(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    clock_values = iter((1.0, 1.0, 1.125, 1.125))
    handlers = {kind: (lambda _action: None) for kind in ActionKind}
    adapter = VMHAEffectAdapter(
        handlers,
        receipt_path=tmp_path / "effect.json",
        clock=lambda: next(clock_values),
        event_sink=lambda event: events.append(dict(event)),
    )
    action = ControllerAction(
        ActionKind.RECONCILE_ROUTES,
        "operation-a",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )

    adapter.apply(action)

    assert [event["event"] for event in events] == ["started", "completed"]
    assert events[0] == {
        "action": "reconcile-routes",
        "event": "started",
        "monotonic_ms": 1000.0,
        "operation_id": "operation-a",
        "schema": "nebius-vpngw/vm-ha-effect-event-v1",
        "target_node_id": "node-a",
    }
    assert events[1]["duration_ms"] == 125.0


def test_effect_adapter_failure_event_omits_exception_text(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    failed_operations: list[str] = []

    def fail(_action: ControllerAction) -> None:
        raise RuntimeError("fixture-secret-must-not-be-logged")

    handlers = {kind: fail for kind in ActionKind}
    clock_values = iter((2.0, 2.0, 2.25, 2.25))
    adapter = VMHAEffectAdapter(
        handlers,
        receipt_path=tmp_path / "effect.json",
        clock=lambda: next(clock_values),
        event_sink=lambda event: events.append(dict(event)),
        effect_failed=lambda action: failed_operations.append(action.operation_id),
    )
    action = ControllerAction(
        ActionKind.ATTACH_CANDIDATE,
        "operation-b",
        "boot-a",
        "node-a",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )

    with pytest.raises(RuntimeError, match="fixture-secret"):
        adapter.apply(action)

    assert [event["event"] for event in events] == ["started", "failed"]
    assert events[1]["duration_ms"] == 250.0
    assert events[1]["error_type"] == "RuntimeError"
    assert failed_operations == ["operation-b"]
    assert "fixture-secret" not in json.dumps(events)


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


def test_passive_routing_hygiene_requires_current_fenced_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot = tmp_path / "boot-id"
    guard = tmp_path / "guard.json"
    boot.write_text("boot-a\n", encoding="utf-8")
    guard.write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "passive"}),
        encoding="utf-8",
    )
    removed: list[str] = []
    monkeypatch.setattr("nebius_vpngw.agent.routing_guard._ipv4_forwarding_disabled", lambda: True)
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard.acquire_routing_lock",
        lambda blocking=False: os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard._remove_table_220",
        lambda: not removed.append("table-220"),
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard._remove_broad_apipa_route",
        lambda: not removed.append("broad-apipa"),
    )
    monkeypatch.setattr("nebius_vpngw.agent.routing_guard._flush_route_cache", lambda: None)

    def run(argv, **_kwargs):
        if argv == ["ip", "rule", "show"]:
            return SimpleNamespace(returncode=0, stdout="")
        if argv == ["ip", "-j", "-4", "route", "show", "table", "all"]:
            return SimpleNamespace(returncode=0, stdout="[]")
        if argv == ["ip", "route", "show", "169.254.0.0/16"]:
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(argv)

    monkeypatch.setattr("nebius_vpngw.agent.routing_guard.subprocess.run", run)
    enforce_vm_ha_passive_routing_hygiene({"vm_ha": {}}, guard_path=guard, boot_id_path=boot)
    assert removed == ["table-220", "broad-apipa"]

    guard.write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "blocked"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="current and fenced"):
        enforce_vm_ha_passive_routing_hygiene({"vm_ha": {}}, guard_path=guard, boot_id_path=boot)


def test_route_maintenance_admits_only_current_active_or_fenced_passive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot = tmp_path / "boot-id"
    guard = tmp_path / "guard.json"
    status = tmp_path / "status.json"
    boot.write_text("boot-a\n", encoding="utf-8")
    forwarding_disabled = True
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard._ipv4_forwarding_disabled",
        lambda: forwarding_disabled,
    )

    guard.write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "passive"}),
        encoding="utf-8",
    )
    require_vm_ha_route_maintenance_ready(
        {"vm_ha": {}}, status_path=status, guard_path=guard, boot_id_path=boot
    )

    forwarding_disabled = False
    with pytest.raises(RuntimeError, match="current and fenced"):
        require_vm_ha_route_maintenance_ready(
            {"vm_ha": {}}, status_path=status, guard_path=guard, boot_id_path=boot
        )
    forwarding_disabled = True

    guard.write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "active"}),
        encoding="utf-8",
    )
    status.write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "controller_ready_boot_id": "boot-a",
                "guard_boot_id": "boot-a",
                "promotion_ready": True,
                "data_plane_mode": "active",
                "state": "active",
            }
        ),
        encoding="utf-8",
    )
    require_vm_ha_route_maintenance_ready(
        {"vm_ha": {}}, status_path=status, guard_path=guard, boot_id_path=boot
    )

    guard.write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "blocked"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="active or fenced-passive"):
        require_vm_ha_route_maintenance_ready(
            {"vm_ha": {}}, status_path=status, guard_path=guard, boot_id_path=boot
        )

    require_vm_ha_route_maintenance_ready(
        {"vm_ha": {"enabled": False}},
        status_path=tmp_path / "missing-status",
        guard_path=tmp_path / "missing-guard",
        boot_id_path=tmp_path / "missing-boot",
    )


def test_periodic_routing_reenforces_only_passive_hygiene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard.acquire_routing_lock",
        lambda blocking=False: os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard._vm_ha_route_maintenance_mode",
        lambda _cfg: "passive",
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard.enforce_vm_ha_passive_routing_hygiene_locked",
        lambda _cfg: calls.append("passive"),
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard._enforce_routing_invariants_locked",
        lambda _cfg: pytest.fail("passive maintenance used the active reconciler"),
    )

    enforce_periodic_routing_invariants({"vm_ha": {}})
    # Model DHCP/network renewal reintroducing drift before the next timer run:
    # the periodic owner must invoke the narrow cleanup again.
    enforce_periodic_routing_invariants({"vm_ha": {}})

    assert calls == ["passive", "passive"]

    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard.enforce_vm_ha_passive_routing_hygiene_locked",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("postcondition failed")),
    )
    with pytest.raises(RuntimeError, match="postcondition failed"):
        enforce_periodic_routing_invariants({"vm_ha": {}})


def test_periodic_routing_preserves_active_and_lock_contention_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    lock_results = iter(
        (
            os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
            None,
        )
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard.acquire_routing_lock",
        lambda blocking=False: next(lock_results),
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard._vm_ha_route_maintenance_mode",
        lambda _cfg: "active",
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard._enforce_routing_invariants_locked",
        lambda _cfg: calls.append("active"),
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard.enforce_vm_ha_passive_routing_hygiene_locked",
        lambda _cfg: pytest.fail("active maintenance used passive cleanup"),
    )

    enforce_periodic_routing_invariants({"vm_ha": {}})
    enforce_periodic_routing_invariants({"vm_ha": {}})

    assert calls == ["active"]


def test_remove_table_220_flushes_route_only_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table_reads = iter(
        (
            json.dumps([{"table": 220, "dst": "10.10.0.0/24", "dev": "xfrm0"}]),
            "[]",
        )
    )
    calls: list[tuple[str, ...]] = []

    def run(argv, **_kwargs):
        calls.append(tuple(argv))
        if argv == ["ip", "rule", "show"]:
            return SimpleNamespace(returncode=0, stdout="")
        if argv == ["ip", "-j", "-4", "route", "show", "table", "all"]:
            return SimpleNamespace(returncode=0, stdout=next(table_reads))
        if argv == ["ip", "route", "flush", "table", "220"]:
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(argv)

    monkeypatch.setattr("nebius_vpngw.agent.routing_guard.subprocess.run", run)

    assert _remove_table_220() is True
    assert ("ip", "route", "flush", "table", "220") in calls


def test_table_220_rule_matching_uses_exact_lookup_table_not_priority() -> None:
    unrelated = "220: from all lookup main\n100: from all lookup 2200\n"
    assert has_table_220_rule(unrelated) is False
    assert has_table_220_rule(unrelated + "101: from all table 220\n") is True


def test_remove_table_220_preserves_unrelated_priority_and_lookup_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def run(argv, **_kwargs):
        calls.append(tuple(argv))
        outputs = {
            ("ip", "rule", "show"): "220: from all lookup main\n100: from all lookup 2200\n",
            ("ip", "-j", "-4", "route", "show", "table", "all"): "[]",
        }
        return SimpleNamespace(returncode=0, stdout=outputs[tuple(argv)])

    monkeypatch.setattr("nebius_vpngw.agent.routing_guard.subprocess.run", run)

    assert _remove_table_220() is False
    assert not any(call[:3] == ("ip", "rule", "del") for call in calls)
    assert ("ip", "route", "flush", "table", "220") not in calls


def test_remove_table_220_deletes_only_exact_table_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule_reads = iter(
        (
            "220: from all lookup main\n100: from all lookup 2200\n101: from all lookup 220\n",
            "220: from all lookup main\n100: from all lookup 2200\n",
        )
    )
    calls: list[tuple[str, ...]] = []

    def run(argv, **_kwargs):
        calls.append(tuple(argv))
        if argv == ["ip", "rule", "show"]:
            return SimpleNamespace(returncode=0, stdout=next(rule_reads))
        if argv == ["ip", "-j", "-4", "route", "show", "table", "all"]:
            return SimpleNamespace(returncode=0, stdout="[]")
        if argv in (
            ["ip", "route", "flush", "table", "220"],
            ["ip", "rule", "del", "lookup", "220"],
        ):
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(argv)

    monkeypatch.setattr("nebius_vpngw.agent.routing_guard.subprocess.run", run)

    assert _remove_table_220() is True
    assert calls.count(("ip", "rule", "del", "lookup", "220")) == 1
    assert not any(call[:4] == ("ip", "rule", "del", "pref") for call in calls)


def test_passive_routing_hygiene_rejects_remaining_table_220_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot = tmp_path / "boot-id"
    guard = tmp_path / "guard.json"
    boot.write_text("boot-a\n", encoding="utf-8")
    guard.write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "passive"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("nebius_vpngw.agent.routing_guard._ipv4_forwarding_disabled", lambda: True)
    monkeypatch.setattr(
        "nebius_vpngw.agent.routing_guard.acquire_routing_lock",
        lambda blocking=False: os.open(tmp_path / "routing.lock", os.O_CREAT | os.O_RDWR, 0o600),
    )
    monkeypatch.setattr("nebius_vpngw.agent.routing_guard._remove_table_220", lambda: False)
    monkeypatch.setattr("nebius_vpngw.agent.routing_guard._remove_broad_apipa_route", lambda: False)

    def run(argv, **_kwargs):
        if argv == ["ip", "rule", "show"]:
            return SimpleNamespace(returncode=0, stdout="")
        if argv == ["ip", "-j", "-4", "route", "show", "table", "all"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{"table": 220, "dst": "10.10.0.0/24", "dev": "xfrm0"}]),
            )
        if argv == ["ip", "route", "show", "169.254.0.0/16"]:
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(argv)

    monkeypatch.setattr("nebius_vpngw.agent.routing_guard.subprocess.run", run)

    with pytest.raises(RuntimeError, match="postcondition failed"):
        enforce_vm_ha_passive_routing_hygiene({"vm_ha": {}}, guard_path=guard, boot_id_path=boot)


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
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "blocked"}),
        encoding="utf-8",
    )
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
    assert status["state"] == "normal"
    assert status["reasons"] == ["owner-must-materialize-passive-dataplane"]
    assert status["apply_locked"] is False
    assert status["apply_operation_id"] is None
    assert status["route_runtime_id"] == _runtime_config()["runtime_binding"]["route_runtime_id"]  # type: ignore[index]
    assert status["route_reconciliation"] is None
    assert len(calls) == 1
    assert calls[0].endswith(":enter-passive:node-a")


def test_passive_standby_ready_uses_strict_current_boot_evidence(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")
    cloud = CloudObservation(
        True,
        "allocation-a",
        "node-b",
        "node-b",
        ComputeState.RUNNING,
        False,
        False,
        False,
        "7",
        former_attachment_exact=True,
        candidate_attachment_absent=True,
    )
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "passive"}),
        encoding="utf-8",
    )
    runtime = build_vm_ha_controller_runtime(
        config_path=config_path,
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.PASSIVE,
            routes=lambda: None,
        ),
        handlers={kind: (lambda _action: None) for kind in ActionKind},
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )

    _run_vm_ha_controller(runtime, once=True, config_path=config_path)

    evidence_path = tmp_path / "standby-ready.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["schema"] == "nebius-vpngw/vm-ha-standby-ready-v1"
    assert evidence["boot_id"] == "boot-a"
    assert (
        vm_ha_status(state_dir=tmp_path, clock=lambda: 10.0, boot_id=lambda: "boot-a")[
            "standby_ready"
        ]
        is True
    )

    stale_status = vm_ha_status(state_dir=tmp_path, clock=lambda: 20.0, boot_id=lambda: "boot-a")
    assert stale_status["standby_ready"] is False
    assert stale_status["redundancy_ready"] is False
    assert "standby-ready-evidence-invalid" in stale_status["standby_readiness_reasons"]

    evidence["extra"] = "invalid"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    status = vm_ha_status(state_dir=tmp_path, clock=lambda: 10.0, boot_id=lambda: "boot-a")
    assert status["standby_ready"] is False
    assert status["redundancy_ready"] is False
    assert "standby-ready-evidence-invalid" in status["standby_readiness_reasons"]


def test_passive_routing_hygiene_drift_invalidates_standby_status(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")
    cloud = CloudObservation(
        True,
        "allocation-a",
        "node-b",
        "node-b",
        ComputeState.RUNNING,
        False,
        False,
        False,
        "7",
        former_attachment_exact=True,
        candidate_attachment_absent=True,
    )
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "passive"}),
        encoding="utf-8",
    )
    runtime = build_vm_ha_controller_runtime(
        config_path=config_path,
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True, routing_hygiene_ready=False),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.PASSIVE,
            routes=lambda: None,
        ),
        handlers={kind: (lambda _action: None) for kind in ActionKind},
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )

    _run_vm_ha_controller(runtime, once=True, config_path=config_path)

    status = vm_ha_status(state_dir=tmp_path, clock=lambda: 10.0, boot_id=lambda: "boot-a")
    assert status["promotion_ready"] is False
    assert status["standby_ready"] is False
    assert status["redundancy_ready"] is False
    assert "routing-hygiene-not-ready" in status["standby_readiness_reasons"]
    assert not (tmp_path / "standby-ready.json").exists()


@pytest.mark.parametrize("stale_mode", ("active", "passive"))
def test_status_rejects_pre_reboot_evidence_after_current_guard_exists(
    tmp_path: Path,
    stale_mode: str,
) -> None:
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "new-boot", "data_plane_mode": "passive"}),
        encoding="utf-8",
    )
    (tmp_path / "apply.lock").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
                "apply_locked": True,
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
                "operation_id": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "state": stale_mode,
                "reasons": [],
                "recovery_action": None,
                "guard_boot_id": "old-boot",
                "controller_ready_boot_id": "old-boot",
                "data_plane_mode": stale_mode,
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "configured_role": stale_mode,
                "observed_owner_node_id": "node-a",
                "allocation_id": "allocation-a",
                "ownership_epoch": "7",
                "former_owner_compute_state": "stopped",
                "former_attachment_absent": True,
                "former_attachment_exact": True,
                "candidate_attachment_exact": True,
                "candidate_attachment_absent": True,
                "ownership_re_read_exact": True,
                "generation_id": "a" * 64,
                "digests": {
                    "configuration": "a" * 64,
                    "static_routes": "c" * 64,
                    "bgp_policy": "d" * 64,
                },
                "apply_locked": False,
                "apply_operation_id": None,
                "route_runtime_id": "route-runtime-a",
                "route_reconciliation": {"old": "receipt"},
                "promotion_ready": True,
                "standby_ready": True,
                "standby_tunnel_state": "warm",
                "standby_readiness_reasons": [],
                "redundancy_ready": True,
                "rearm_phase": "running",
                "rearm_reason": None,
                "phase_durations_seconds": {
                    "preparation": 1.0,
                    "detection_repair": 2.0,
                    "common_cutover": 3.0,
                    "redundancy_restoration": 4.0,
                },
                "pending_operation_id": "old-operation",
                "repair": {"old": "repair"},
            }
        ),
        encoding="utf-8",
    )

    status = vm_ha_status(state_dir=tmp_path, boot_id=lambda: "new-boot")

    assert status["state"] == "blocked"
    assert status["data_plane_mode"] == "blocked"
    assert status["guard_boot_id"] == "new-boot"
    assert status["controller_ready_boot_id"] is None
    assert status["observed_owner_node_id"] is None
    assert status["ownership_epoch"] is None
    assert status["former_owner_compute_state"] == "unknown"
    assert status["former_attachment_absent"] is False
    assert status["former_attachment_exact"] is False
    assert status["candidate_attachment_exact"] is False
    assert status["candidate_attachment_absent"] is False
    assert status["ownership_re_read_exact"] is False
    assert status["route_reconciliation"] is None
    assert status["promotion_ready"] is False
    assert status["standby_ready"] is False
    assert status["redundancy_ready"] is False
    assert status["pending_operation_id"] is None
    assert status["repair"] is None
    assert set(status["phase_durations_seconds"].values()) == {None}
    assert status["apply_locked"] is True
    assert status["apply_operation_id"] == "b" * 64
    assert status["cluster_id"] == "cluster-a"
    assert status["node_id"] == "node-a"
    assert status["generation_id"] == "a" * 64
    assert status["allocation_id"] == "allocation-a"
    assert status["route_runtime_id"] == "route-runtime-a"
    assert status["reasons"] == ["current-boot-status-stale"]


@pytest.mark.parametrize("data_plane_mode", ("active", "passive"))
def test_current_boot_status_projection_is_unchanged(
    tmp_path: Path,
    data_plane_mode: str,
) -> None:
    guard = {
        "guard_boot_id": "boot-a",
        "data_plane_mode": data_plane_mode,
    }
    payload = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "state": data_plane_mode,
        "guard_boot_id": "boot-a",
        "data_plane_mode": data_plane_mode,
        "promotion_ready": False,
        "promotion_committed": False,
        "standby_ready": False,
    }
    (tmp_path / "guard.json").write_text(json.dumps(guard), encoding="utf-8")
    (tmp_path / "status.json").write_text(json.dumps(payload), encoding="utf-8")

    assert vm_ha_status(state_dir=tmp_path, boot_id=lambda: "boot-a") == {
        **payload,
        "runtime_identity": {
            "state": "unknown",
            "reason": "identity-proof-unavailable",
        },
        "auto_healing": {
            "state": "blocked",
            "peer_agrees": False,
            "accepted_start": False,
        },
    }


def test_default_controller_consumes_only_the_current_systemd_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation_id = "1" * 32
    observed: dict[str, object] = {}
    state_dir = tmp_path / "state"

    class Runtime:
        def step(self) -> dict[str, object]:
            return {}

        def close(self) -> None:
            observed["closed"] = True

    def build_default(**kwargs: object) -> Runtime:
        observed.update(kwargs)
        return Runtime()

    monkeypatch.setenv("INVOCATION_ID", invocation_id)
    monkeypatch.setattr(
        "nebius_vpngw.agent.main._read_vm_ha_config",
        lambda _path: _runtime_config(),
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.require_vm_ha_runtime_prerequisites",
        lambda: None,
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.build_default_vm_ha_controller_runtime",
        build_default,
    )

    _run_vm_ha_controller(
        once=True,
        config_path=tmp_path / "config.yaml",
        state_dir=state_dir,
    )

    assert observed["identity_proof_mode"] == "systemd-controller"
    assert observed["systemd_invocation_id"] == invocation_id
    assert observed["state_dir"] == state_dir
    assert observed["closed"] is True


def test_systemd_preflight_consumes_pending_activation_heartbeat_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    generation_store = AtomicGenerationStore(state_dir / "generation-store")
    generation_store.request_accepted_peer_heartbeat_reset(
        "node-b",
        generation_id="a" * 64,
    )
    heartbeat_path = generation_store.peers / "node-b" / "accepted-heartbeat.json"
    reset_path = generation_store.peers / "node-b" / "accepted-heartbeat-reset.json"
    heartbeat_path.write_text("{legacy-writer", encoding="utf-8")
    observed: dict[str, bool] = {}

    def stop_after_boundary(*_args: object, **_kwargs: object) -> None:
        observed["heartbeat_exists"] = heartbeat_path.exists()
        observed["reset_exists"] = reset_path.exists()
        raise RuntimeError("stop after reset boundary")

    monkeypatch.setattr(
        "nebius_vpngw.agent.main._read_vm_ha_config",
        lambda _path: _runtime_config(),
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.build_runtime_ports",
        stop_after_boundary,
    )

    with pytest.raises(RuntimeError, match="stop after reset boundary"):
        build_default_vm_ha_controller_runtime(
            config_path=tmp_path / "config.yaml",
            state_dir=state_dir,
            boot_id=lambda: "boot-a",
            identity_proof_mode="systemd-preflight",
            systemd_invocation_id="1" * 32,
        )

    assert observed == {"heartbeat_exists": False, "reset_exists": False}


def test_migration_required_identity_fences_stale_current_boot_status(tmp_path: Path) -> None:
    guard = {"guard_boot_id": "boot-a", "data_plane_mode": "passive"}
    payload = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "state": "active",
        "guard_boot_id": "boot-a",
        "data_plane_mode": "active",
        "promotion_ready": True,
        "promotion_committed": True,
        "standby_ready": False,
        "redundancy_ready": False,
        "reasons": [],
    }
    identity = {
        "schema": "nebius-vpngw/vm-ha-runtime-identity-v1",
        "state": "migration-required",
        "reason": "apply-required",
        "boot_id": "boot-a",
    }
    (tmp_path / "guard.json").write_text(json.dumps(guard), encoding="utf-8")
    (tmp_path / "status.json").write_text(json.dumps(payload), encoding="utf-8")
    identity_path = tmp_path / "runtime-identity.json"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    identity_path.chmod(0o600)

    status = vm_ha_status(state_dir=tmp_path, boot_id=lambda: "boot-a")

    assert status["runtime_identity"] == {
        "state": "migration-required",
        "reason": "apply-required",
    }
    assert status["state"] == "blocked"
    assert status["data_plane_mode"] == "blocked"
    assert status["promotion_ready"] is False
    assert status["standby_ready"] is False
    assert status["redundancy_ready"] is False
    assert "runtime-identity-blocked" in status["reasons"]


def test_transfer_progress_projection_requires_the_exact_pending_checkpoint(
    tmp_path: Path,
) -> None:
    request = {
        "schema": "nebius-vpngw/vm-ha-manual-failover-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
        "requested_at": 10.0,
    }
    identity = TransferProgressIdentity(
        cluster_id="cluster-a",
        candidate_node_id="node-a",
        former_owner_node_id="node-b",
        allocation_id="allocation-a",
        generation_id="a" * 64,
        digests={
            "configuration": "a" * 64,
            "static_routes": "b" * 64,
            "bgp_policy": "c" * 64,
        },
        route_runtime_id="route-runtime-a",
        intent="planned-failover",
        request_fingerprint=planned_request_fingerprint(request),
        first_operation_id="boot-a:1:stop-former-owner:node-b",
        ownership_incarnation=0,
    )
    action = ControllerAction(
        ActionKind.STOP_FORMER_OWNER,
        identity.first_operation_id,
        "boot-a",
        "node-b",
        "allocation-a",
        "7",
        "a" * 64,
        DigestSet("a" * 64, "b" * 64, "c" * 64),
    )
    checkpoint_store = VMHACheckpointFileStore(tmp_path / "controller-checkpoint.json")
    checkpoint_store.save(ControllerCheckpoint(sequence=1, pending_action=action))
    progress_store = TransferProgressStore(
        tmp_path / "transfer-progress.json",
        writer=lambda path, value: path.write_text(json.dumps(value), encoding="utf-8"),
        clock=lambda: 10.0,
    )
    progress_store.attempting(
        identity,
        action=action.kind.value,
        operation_id=action.operation_id,
        boot_id=action.boot_id,
        ownership_epoch=action.ownership_epoch,
    )
    payload = {
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "allocation_id": "allocation-a",
        "generation_id": "a" * 64,
        "digests": identity.to_dict()["digests"],
        "route_runtime_id": "route-runtime-a",
    }

    projected = _project_transfer_progress(
        dict(payload), state_dir=tmp_path, current_boot_id="boot-a"
    )
    assert projected["transfer_progress"]["history"][-1]["state"] == "attempting"
    assert "transfer_progress" not in _project_transfer_progress(
        {**payload, "promotion_committed": True},
        state_dir=tmp_path,
        current_boot_id="boot-a",
    )
    assert "transfer_progress" not in _project_transfer_progress(
        {**payload, "apply_locked": True},
        state_dir=tmp_path,
        current_boot_id="boot-a",
    )

    checkpoint_store.save(ControllerCheckpoint(sequence=1))
    assert "transfer_progress" not in _project_transfer_progress(
        dict(payload), state_dir=tmp_path, current_boot_id="boot-a"
    )


def test_current_boot_status_projects_live_writer_inhibition_over_stale_record(
    tmp_path: Path,
) -> None:
    guard = {
        "guard_boot_id": "boot-a",
        "data_plane_mode": "passive",
    }
    payload = {
        "schema": "nebius-vpngw/vm-ha-status-v1",
        "state": "blocked",
        "reasons": ["apply-lock-held"],
        "guard_boot_id": "boot-a",
        "data_plane_mode": "passive",
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
        "apply_locked": True,
        "apply_operation_id": "b" * 64,
        "promotion_ready": False,
        "standby_ready": False,
    }
    (tmp_path / "guard.json").write_text(json.dumps(guard), encoding="utf-8")
    (tmp_path / "status.json").write_text(json.dumps(payload), encoding="utf-8")

    status = vm_ha_status(state_dir=tmp_path, boot_id=lambda: "boot-a")

    assert status["apply_locked"] is False
    assert status["apply_operation_id"] is None


def test_controller_effect_failure_persists_secret_free_blocked_status(
    tmp_path: Path,
) -> None:
    config = _runtime_config()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": config}), encoding="utf-8")
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "passive"}),
        encoding="utf-8",
    )
    (tmp_path / "apply-owner-adoption.json").write_text(
        json.dumps(
            {
                **_apply_owner_adoption(),
                "node_id": "node-a",
                "peer_node_id": "node-b",
            }
        ),
        encoding="utf-8",
    )
    peer = PeerHeartbeat(
        cluster_id="cluster-a",
        node_id="node-b",
        boot_id="boot-b",
        sequence=3,
        sent_at="2026-08-20T00:00:00Z",
        configured_role="passive",
        observed_owner_id="node-a",
        generation_id="a" * 64,
        mtls_epoch=1,
        certificate_fingerprint="d" * 64,
        digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        service_healthy=True,
        route_ready=True,
        promotion_ready=True,
        auto_healing_policy_state="enabled",
        auto_healing_policy_digest="e" * 64,
    )
    mode = [DataPlaneMode.PASSIVE]

    def reconcile(_action: ControllerAction) -> None:
        raise RuntimeError("VM-HA ledger route identity is not exact in cloud state")

    handlers = {kind: (lambda _action: None) for kind in ActionKind}
    handlers[ActionKind.RECONCILE_ROUTES] = reconcile
    runtime = build_vm_ha_controller_runtime(
        config_path=config_path,
        providers=VMHASnapshotProviders(
            peer=lambda: (peer, 9.0),
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
        handlers=handlers,
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )

    def guard() -> dict[str, object]:
        mode[0] = DataPlaneMode.BLOCKED
        record = {"guard_boot_id": "boot-a", "data_plane_mode": "blocked"}
        (tmp_path / "guard.json").write_text(json.dumps(record), encoding="utf-8")
        return record

    runtime.guard = guard

    with pytest.raises(RuntimeError, match="ledger route identity"):
        runtime.step()

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "blocked"
    assert status["reasons"] == ["route-ledger-identity-not-exact"]
    assert status["data_plane_mode"] == "blocked"
    assert status["promotion_ready"] is False
    assert status["pending_operation_id"].endswith(":reconcile-routes:node-a")


def test_stale_status_projection_rejects_malformed_current_apply_lock(tmp_path: Path) -> None:
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "new-boot", "data_plane_mode": "passive"}),
        encoding="utf-8",
    )
    (tmp_path / "apply.lock").write_text("{}", encoding="utf-8")
    (tmp_path / "status.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-status-v1",
                "guard_boot_id": "old-boot",
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "generation_id": "a" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="apply-lock record"):
        vm_ha_status(state_dir=tmp_path, boot_id=lambda: "new-boot")


def test_corrupt_rearm_status_cannot_fail_the_safety_controller(tmp_path: Path) -> None:
    config = _runtime_config()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": config}), encoding="utf-8")
    route_runtime_id = config["runtime_binding"]["route_runtime_id"]  # type: ignore[index]
    digests = DigestSet("a" * 64, "b" * 64, "c" * 64)
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
    routes = RouteReconciliationContext(
        owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        generation_id="a" * 64,
        digests=digests,
        route_runtime_id=route_runtime_id,
        operation_id="route-operation",
    )
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "active"}),
        encoding="utf-8",
    )
    (tmp_path / "rearm-status.json").write_text("not-json", encoding="utf-8")
    runtime = build_vm_ha_controller_runtime(
        config_path=config_path,
        providers=VMHASnapshotProviders(
            peer=lambda: (None, None),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.ACTIVE,
            routes=lambda: routes,
        ),
        handlers={kind: (lambda _action: None) for kind in ActionKind},
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )

    _run_vm_ha_controller(runtime, once=True, config_path=config_path)

    status = json.loads((tmp_path / "status.json").read_text())
    assert status["promotion_ready"] is True
    assert status["rearm_phase"] == "blocked"
    assert status["rearm_reason"] == "rearm-status-invalid"


@pytest.mark.parametrize(
    ("rearm_receipt_id", "expected_phase", "expected_redundancy"),
    (
        ("receipt-a", "running", True),
        ("stale-receipt", "idle", False),
    ),
)
def test_owner_redundancy_requires_rearm_bound_to_current_promotion(
    tmp_path: Path,
    rearm_receipt_id: str,
    expected_phase: str,
    expected_redundancy: bool,
) -> None:
    config = _runtime_config()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": config}), encoding="utf-8")
    route_runtime_id = config["runtime_binding"]["route_runtime_id"]  # type: ignore[index]
    digests = DigestSet("a" * 64, "b" * 64, "c" * 64)
    cloud = CloudObservation(
        True,
        "allocation-a",
        "node-a",
        "node-b",
        ComputeState.RUNNING,
        True,
        True,
        True,
        "7",
    )
    routes = RouteReconciliationContext(
        owner_node_id="node-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        generation_id="a" * 64,
        digests=digests,
        route_runtime_id=route_runtime_id,
        operation_id="route-operation",
    )
    (tmp_path / "guard.json").write_text(
        json.dumps({"guard_boot_id": "boot-a", "data_plane_mode": "active"}),
        encoding="utf-8",
    )
    promotion = _promotion_receipt(owner="node-a", epoch="7")
    promotion.update(
        intent="planned-failback",
        former_owner_node_id="node-b",
        route_operation_id="route-operation",
    )
    (tmp_path / "promotion-receipt.json").write_text(
        json.dumps(promotion),
        encoding="utf-8",
    )
    (tmp_path / "rearm-status.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-rearm-status-v1",
                "phase": "running",
                "reason": None,
                "promotion_receipt_id": rearm_receipt_id,
                "operation_id": "operation-a",
                "completed_at": 9.0,
                "updated_at": 9.0,
            }
        ),
        encoding="utf-8",
    )
    peer = PeerHeartbeat(
        cluster_id="cluster-a",
        node_id="node-b",
        boot_id="boot-b",
        sequence=3,
        sent_at="2026-08-17T00:00:00Z",
        configured_role="passive",
        observed_owner_id="node-a",
        generation_id="a" * 64,
        mtls_epoch=1,
        certificate_fingerprint="d" * 64,
        digests=digests,
        service_healthy=True,
        route_ready=True,
        promotion_ready=True,
        auto_healing_policy_state="enabled",
        auto_healing_policy_digest="e" * 64,
    )
    runtime = build_vm_ha_controller_runtime(
        config_path=config_path,
        providers=VMHASnapshotProviders(
            peer=lambda: (peer, 9.0),
            readiness=lambda: LocalReadiness(True, True, True, True),
            cloud=lambda: cloud,
            data_plane=lambda: DataPlaneMode.ACTIVE,
            routes=lambda: routes,
        ),
        handlers={kind: (lambda _action: None) for kind in ActionKind},
        state_dir=tmp_path,
        clock=lambda: 10.0,
        boot_id=lambda: "boot-a",
    )

    _run_vm_ha_controller(runtime, once=True, config_path=config_path)

    status = json.loads((tmp_path / "status.json").read_text())
    assert status["promotion_ready"] is True
    assert status["standby_ready"] is False
    assert status["rearm_phase"] == expected_phase
    assert status["redundancy_ready"] is expected_redundancy
    if rearm_receipt_id == "stale-receipt":
        assert status["rearm_reason"] == "rearm-status-unavailable"


def test_service_fails_closed_without_bound_runtime(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"vm_ha": _runtime_config()}), encoding="utf-8")

    def blocked(**_kwargs: object) -> None:
        raise RuntimeError("VM-HA activation BLOCKED: runtime adapters unavailable")

    monkeypatch.setattr("nebius_vpngw.agent.main.require_vm_ha_runtime_prerequisites", blocked)
    with pytest.raises(RuntimeError, match="activation BLOCKED"):
        _run_vm_ha_controller(config_path=config_path)


@pytest.mark.parametrize(
    "crash_after",
    [
        ActionKind.ENTER_PASSIVE,
        ActionKind.STOP_FORMER_OWNER,
        ActionKind.DETACH_FORMER_ATTACHMENT,
        ActionKind.ATTACH_CANDIDATE,
        ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
        ActionKind.RECONCILE_ROUTES,
        ActionKind.ENABLE_ACTIVE,
    ],
)
def test_default_factory_two_node_transfer_restarts_after_every_effect(
    tmp_path: Path, monkeypatch, crash_after: ActionKind
) -> None:
    digest = "a" * 64
    clock = [0.0]
    shared: dict[str, object] = {
        "owner": "node-a",
        "former_state": ComputeState.RUNNING,
        "candidate_revision": 3,
        "confirmed": {"node-a"},
        "modes": {"node-a": DataPlaneMode.BLOCKED, "node-b": DataPlaneMode.BLOCKED},
        "receipts": {},
        "trace": [],
    }
    for node_id, role in (("node-a", "active"), ("node-b", "passive")):
        config = json.loads(json.dumps(_runtime_config()))
        config["node"] = {"node_id": node_id, "role": role}
        (tmp_path / f"{node_id}.yaml").write_text(
            yaml.safe_dump({"vm_ha": config}), encoding="utf-8"
        )

    class Cloud:
        def __init__(self, local_id: str, peer_id: str) -> None:
            self.local_id = local_id
            self.peer_id = peer_id

        def observe(self) -> CloudObservation:
            owner = shared["owner"]
            owner_is_local = owner == self.local_id
            confirmed = shared["confirmed"]
            return CloudObservation(
                authoritative=True,
                allocation_id="allocation-a",
                observed_owner_node_id=owner if isinstance(owner, str) else None,
                former_owner_node_id=self.peer_id,
                former_owner_compute_state=(
                    shared["former_state"] if self.peer_id == "node-a" else ComputeState.STOPPED
                ),
                former_attachment_absent=owner != self.peer_id,
                candidate_attachment_exact=owner_is_local,
                ownership_re_read_exact=bool(
                    owner_is_local and isinstance(confirmed, set) and self.local_id in confirmed
                ),
                ownership_epoch=str(
                    shared["candidate_revision"] if self.local_id == "node-b" else 3
                ),
            )

    class Routes:
        def __init__(self, local_id: str, binding: VMHARuntimeBinding) -> None:
            self.local_id = local_id
            self.binding = binding

        def readiness(self) -> LocalReadiness:
            return LocalReadiness(True, True, True, True)

        def receipt_context(self) -> RouteReconciliationContext | None:
            receipts = shared["receipts"]
            assert isinstance(receipts, dict)
            value = receipts.get(self.local_id)
            return value if isinstance(value, RouteReconciliationContext) else None

    class Peer:
        def observe(self) -> tuple[PeerHeartbeat | None, float | None]:
            return (
                PeerHeartbeat(
                    cluster_id="cluster-a",
                    node_id="node-a",
                    boot_id="boot-a",
                    sequence=1,
                    sent_at="2026-08-13T00:00:00Z",
                    configured_role="active",
                    observed_owner_id="node-a",
                    generation_id=digest,
                    mtls_epoch=1,
                    certificate_fingerprint="d" * 64,
                    digests=DigestSet(digest, "b" * 64, "c" * 64),
                    service_healthy=True,
                    route_ready=True,
                    promotion_ready=True,
                    auto_healing_policy_state="enabled",
                    auto_healing_policy_digest="e" * 64,
                ),
                clock[0] - 100.0,
            )

        def poll(self, *, timeout_seconds: float) -> None:
            raise PeerTransportError(f"offline peer fixture {timeout_seconds}")

        def send(self, _heartbeat: PeerHeartbeat) -> None:
            return None

    class Ports:
        def __init__(self, config: dict[str, object], state_dir: Path) -> None:
            self.binding = VMHARuntimeBinding.model_validate(config["runtime_binding"])
            node = config["node"]
            assert isinstance(node, dict)
            local_id = str(node["node_id"])
            nodes = {item.node_id: item for item in self.binding.nodes}
            self.local = nodes[local_id]
            self.peer = next(item for item in self.binding.nodes if item.node_id != local_id)
            self.cloud = Cloud(local_id, self.peer.node_id)
            self.routes = Routes(local_id, self.binding)
            self.peer_runtime = Peer()
            self.state_dir = state_dir
            self.data_plane = type("DataPlane", (), {"mode": self.data_mode})()

        def data_mode(self) -> DataPlaneMode:
            modes = shared["modes"]
            assert isinstance(modes, dict)
            value = modes[self.local.node_id]
            assert isinstance(value, DataPlaneMode)
            return value

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            modes = shared["modes"]
            trace = shared["trace"]
            assert isinstance(modes, dict) and isinstance(trace, list)
            modes[self.local.node_id] = DataPlaneMode.BLOCKED
            self.state_dir.mkdir(parents=True, exist_ok=True)
            (self.state_dir / "guard.json").write_text(
                json.dumps(
                    {
                        "schema": "nebius-vpngw/vm-ha-status-v1",
                        "guard_boot_id": boot_id,
                        "data_plane_mode": "blocked",
                    }
                ),
                encoding="utf-8",
            )
            trace.append("guard")

        def handlers(self) -> dict[ActionKind, object]:
            def effect(action: ControllerAction) -> None:
                trace = shared["trace"]
                modes = shared["modes"]
                confirmed = shared["confirmed"]
                receipts = shared["receipts"]
                assert isinstance(trace, list) and isinstance(modes, dict)
                assert isinstance(confirmed, set) and isinstance(receipts, dict)
                trace.append(action.kind)
                if action.kind is ActionKind.ENTER_PASSIVE:
                    modes[self.local.node_id] = DataPlaneMode.PASSIVE
                elif action.kind is ActionKind.DISABLE_ACTIVE:
                    modes[self.local.node_id] = DataPlaneMode.BLOCKED
                elif action.kind is ActionKind.STOP_FORMER_OWNER:
                    shared["former_state"] = ComputeState.STOPPED
                elif action.kind is ActionKind.DETACH_FORMER_ATTACHMENT:
                    shared["owner"] = None
                elif action.kind is ActionKind.ATTACH_CANDIDATE:
                    shared["owner"] = self.local.node_id
                    shared["candidate_revision"] = 4
                    confirmed.discard(self.local.node_id)
                elif action.kind is ActionKind.CONFIRM_CANDIDATE_OWNERSHIP:
                    confirmed.add(self.local.node_id)
                elif action.kind is ActionKind.RECONCILE_ROUTES:
                    receipts[self.local.node_id] = RouteReconciliationContext(
                        owner_node_id=self.local.node_id,
                        allocation_id=action.allocation_id,
                        ownership_epoch=action.ownership_epoch,
                        generation_id=action.generation_id,
                        digests=action.digests,
                        route_runtime_id=self.binding.route_runtime_id,
                        ownership_incarnation=action.ownership_incarnation,
                        operation_id=action.operation_id,
                    )
                elif action.kind is ActionKind.ENABLE_ACTIVE:
                    modes[self.local.node_id] = DataPlaneMode.ACTIVE

            return {kind: effect for kind in ActionKind}

        def heartbeat(self, *, boot_id: str, sequence: int, clock: float) -> PeerHeartbeat:
            owner = shared["owner"]
            return PeerHeartbeat(
                cluster_id="cluster-a",
                node_id=self.local.node_id,
                boot_id=boot_id,
                sequence=sequence,
                sent_at="2026-08-13T00:00:00Z",
                configured_role=self.local.role.value,
                observed_owner_id=owner if isinstance(owner, str) else None,
                generation_id=digest,
                mtls_epoch=1,
                certificate_fingerprint="d" * 64,
                digests=DigestSet(digest, "b" * 64, "c" * 64),
                service_healthy=True,
                route_ready=True,
                promotion_ready=False,
                auto_healing_policy_state="enabled",
                auto_healing_policy_digest="e" * 64,
            )

        def close(self) -> None:
            return None

    def fake_ports(config: dict[str, object], *, state_dir: Path, **_kwargs: object) -> Ports:
        return Ports(config, state_dir)

    monkeypatch.setattr("nebius_vpngw.agent.main.build_runtime_ports", fake_ports)
    active = build_default_vm_ha_controller_runtime(
        config_path=tmp_path / "node-a.yaml",
        state_dir=tmp_path / "active-state",
        clock=lambda: clock[0],
        boot_id=lambda: "boot-a",
    )
    # This fixture does not exercise listener pacing or failed peer sends. The
    # dedicated retry test below asserts the production delay schedule.
    active.sleeper = lambda _seconds: None
    try:
        active.step()
    finally:
        active.close()

    passive_state = tmp_path / f"passive-{crash_after.value}"

    def build_passive() -> DefaultVMHAControllerRuntime:
        runtime = build_default_vm_ha_controller_runtime(
            config_path=tmp_path / "node-b.yaml",
            state_dir=passive_state,
            clock=lambda: clock[0],
            boot_id=lambda: "boot-b",
        )
        # Avoid the real listener-startup pacing delay in this crash matrix.
        runtime.sleeper = lambda _seconds: None
        return runtime

    passive = build_passive()
    crashed = False
    try:
        for _attempt in range(30):
            trace = shared["trace"]
            assert isinstance(trace, list)
            previous = len(trace)
            status = passive.step()
            clock[0] += 10.0
            if not crashed and crash_after in trace[previous:]:
                passive.close()
                passive = build_passive()
                crashed = True
            elif status.get("promotion_ready") is True:
                break
        else:
            pytest.fail(f"default VM-HA runtime did not converge after crash replay: {status}")

        trace = shared["trace"]
        modes = shared["modes"]
        assert isinstance(trace, list) and isinstance(modes, dict)
        effects = [item for item in trace if isinstance(item, ActionKind)]
        expected = [
            ActionKind.STOP_FORMER_OWNER,
            ActionKind.DETACH_FORMER_ATTACHMENT,
            ActionKind.ATTACH_CANDIDATE,
            ActionKind.CONFIRM_CANDIDATE_OWNERSHIP,
            ActionKind.RECONCILE_ROUTES,
            ActionKind.ENABLE_ACTIVE,
        ]
        positions = [effects.index(kind) for kind in expected]
        assert positions == sorted(positions)
        assert trace[0] == "guard"
        assert shared["former_state"] is ComputeState.STOPPED
        assert shared["owner"] == "node-b"
        assert modes["node-b"] is DataPlaneMode.ACTIVE
        assert crashed is True
    finally:
        # This may be the replacement runtime created after the simulated crash.
        passive.close()


def test_default_service_runtime_exchanges_peer_state_and_closes_guarded(tmp_path: Path) -> None:
    calls: list[str] = []

    class Peer:
        def poll(self, *, timeout_seconds: float) -> None:
            calls.append(f"receive:{timeout_seconds}")

        def send(self, _heartbeat: object) -> None:
            calls.append("send")

    class Ports:
        local = type("Local", (), {"role": type("Role", (), {"value": "active"})()})()
        peer_runtime = Peer()

        def heartbeat(self, **kwargs: object) -> object:
            calls.append(f"heartbeat:{kwargs['sequence']}")
            return object()

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            calls.append(f"guard:{boot_id}")

        def close(self) -> None:
            calls.append("close")

    class Controller:
        def step(self) -> dict[str, object]:
            calls.append("step")
            return {}

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
        clock=lambda: 10.0,
    )
    runtime.step()
    runtime.close()
    runtime.close()

    assert calls[0] == "guard:boot-a"
    assert calls.count("heartbeat:0") == 1
    assert calls.count("send") == 1
    assert calls.count("step") == 1
    assert calls[-2:] == ["guard:boot-a", "close"]
    assert "receive:2.0" in calls

    persisted = json.loads((tmp_path / "sequence.json").read_text(encoding="utf-8"))
    assert persisted["next_sequence"] == 1

    calls.clear()
    restarted = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
        clock=lambda: 11.0,
    )
    restarted.step()
    restarted.close(restore_guard=False)
    assert "heartbeat:1" in calls


def test_default_service_runtime_retries_bounded_peer_send(tmp_path: Path) -> None:
    sends = 0
    sleeps: list[float] = []

    class Peer:
        def send(self, _heartbeat: object) -> None:
            nonlocal sends
            sends += 1
            if sends < 3:
                raise PeerTransportError("fixture unavailable")

        def poll(self, *, timeout_seconds: float) -> None:
            raise PeerTransportError(f"fixture timeout {timeout_seconds}")

    class Ports:
        local = type("Local", (), {"role": type("Role", (), {"value": "passive"})()})()
        peer_runtime = Peer()

        def heartbeat(self, **_kwargs: object) -> object:
            return object()

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            return None

        def close(self) -> None:
            return None

    class Controller:
        def step(self) -> dict[str, object]:
            return {}

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
        peer_retry_seconds=0.05,
        sleeper=sleeps.append,
    )

    runtime.step()
    runtime.close(restore_guard=False)

    assert sends == 3
    assert sleeps == [0.05, 0.05, 0.05]


@pytest.mark.parametrize(
    "invalid_state", [StateValidationError("invalid"), StalePeerStateError("stale")]
)
def test_default_service_runtime_treats_authenticated_invalid_peer_state_as_advisory(
    tmp_path: Path, invalid_state: Exception
) -> None:
    class Peer:
        def poll(self, *, timeout_seconds: float) -> None:
            assert timeout_seconds == 2.0
            runtime.listener_stop.set()
            raise invalid_state

    class Ports:
        peer_runtime = Peer()

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        object(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
    )

    runtime._listen_for_peer_state()

    assert runtime.listener_error is None


def test_default_service_runtime_consumes_validated_manual_failback_after_restore(
    tmp_path: Path,
) -> None:
    failback_path = tmp_path / "manual-failback.json"
    failback_path.write_text("{}", encoding="utf-8")

    class Peer:
        def poll(self, *, timeout_seconds: float) -> None:
            raise PeerTransportError(f"fixture timeout {timeout_seconds}")

        def send(self, _heartbeat: object) -> None:
            return None

    class Ports:
        local = type(
            "Local",
            (),
            {"node_id": "node-a", "role": type("Role", (), {"value": "active"})()},
        )()
        peer_runtime = Peer()

        def heartbeat(self, **_kwargs: object) -> object:
            return object()

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            return None

        def close(self) -> None:
            return None

    class Controller:
        def step(self) -> dict[str, object]:
            return {"promotion_ready": True, "observed_owner_node_id": "node-a"}

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-a",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
        manual_failback_path=failback_path,
    )

    runtime.step()
    runtime.close(restore_guard=False)

    assert not failback_path.exists()
    guard = json.loads((tmp_path / "guard.json").read_text(encoding="utf-8"))
    assert guard["data_plane_mode"] == "active"


def test_default_service_runtime_consumes_validated_manual_failover_after_promotion(
    tmp_path: Path,
) -> None:
    failover_path = tmp_path / "manual-failover.json"
    failover_path.write_text("{}", encoding="utf-8")

    class Peer:
        def poll(self, *, timeout_seconds: float) -> None:
            raise PeerTransportError(f"fixture timeout {timeout_seconds}")

        def send(self, _heartbeat: object) -> None:
            return None

    class Ports:
        local = type(
            "Local",
            (),
            {"node_id": "node-b", "role": type("Role", (), {"value": "passive"})()},
        )()
        peer_runtime = Peer()

        def heartbeat(self, **_kwargs: object) -> object:
            return object()

        def install_shutdown_guard(self, *, boot_id: str) -> None:
            return None

        def close(self) -> None:
            return None

    class Controller:
        def step(self) -> dict[str, object]:
            return {"promotion_ready": True, "observed_owner_node_id": "node-b"}

    runtime = DefaultVMHAControllerRuntime(  # type: ignore[arg-type]
        Controller(),
        Ports(),
        boot_id="boot-b",
        sequence_path=tmp_path / "sequence.json",
        guard_path=tmp_path / "guard.json",
        manual_failover_path=failover_path,
    )

    runtime.step()
    runtime.close(restore_guard=False)

    assert not failover_path.exists()
