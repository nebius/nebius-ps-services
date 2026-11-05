from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from nebius_vpngw.deploy.vm_ha_lifecycle import (
    LIFECYCLE_SCHEMA_V2,
    VMHAApplyLock,
    VMHAEffectObservationGuard,
    VMHALifecycleJournal,
    VMHALifecycleMember,
    VMHALifecycleState,
    VMHALifecycleStatus,
    VMHALifecycleStore,
    normalize_vm_ha_observation,
    vm_ha_effective_resource_bindings,
    vm_ha_missing_standby_disk_name,
    vm_ha_missing_standby_disk_name_binding_key,
    vm_ha_missing_standby_replacement_effect,
    vm_ha_missing_standby_ssh_binding_key,
    vm_ha_passive_replacement_binding_key,
    vm_ha_passive_replacement_cycle_for_approval,
    vm_ha_passive_replacement_cycles,
    vm_ha_passive_replacement_effect,
    vm_ha_resource_binding_matches_observation,
)


@pytest.mark.parametrize(
    "observation",
    [
        {"routes": list(range(12))},
        {"labels": {"a/": "slash", "a0": "digit", "a~": "tilde"}},
        {"a": {"z": 1}, "a-": 2},
    ],
)
def test_observation_normalizes_json_pointer_order(observation: object) -> None:
    normalized = normalize_vm_ha_observation(observation)

    assert normalized == tuple(sorted(normalized))
    assert len({path for path, _value in normalized}) == len(normalized)


def _digest(character: str) -> str:
    return character * 64


@pytest.mark.parametrize("cycle", (1, 2, 9, 10, 11, 19, 20, 99, 100, 101, 199, 200, 1000))
def test_replacement_cycle_readers_agree_at_decimal_boundaries(cycle: int) -> None:
    name = "gateway-1"
    bindings = {"compute:gateway-1": "original-vm", "disk:gateway-1": "original-disk"}
    for current in range(1, cycle + 1):
        for kind, value in {
            "approval": f"approval-{current}",
            "retired-compute": f"vm-{current - 1}",
            "retired-disk": f"disk-{current - 1}",
            "compute": f"vm-{current}",
            "disk": f"disk-{current}",
        }.items():
            bindings[vm_ha_passive_replacement_binding_key(kind, name, current)] = value
    assert vm_ha_passive_replacement_cycles(bindings, name) == tuple(range(1, cycle + 1))
    assert (
        vm_ha_passive_replacement_cycle_for_approval(bindings, name, f"approval-{cycle}") == cycle
    )
    effective = vm_ha_effective_resource_bindings(bindings)
    assert effective["compute:gateway-1"] == f"vm-{cycle}"
    assert effective["disk:gateway-1"] == f"disk-{cycle}"


@pytest.mark.parametrize("suffix", ("0", "1", "01", "010", "+10", "-10", "١٠", "10x"))
def test_replacement_cycle_readers_ignore_noncanonical_suffixes(suffix: str) -> None:
    bindings = {
        "compute:gateway-1": "vm-original",
        "disk:gateway-1": "disk-original",
        f"passive-replacement-{suffix}-approval:gateway-1": "approval-invalid",
        f"retired-{suffix}-compute:gateway-1": "vm-original",
        f"replacement-{suffix}-compute:gateway-1": "vm-invalid",
        f"replacement-{suffix}-disk:gateway-1": "disk-invalid",
    }
    assert vm_ha_passive_replacement_cycles(bindings, "gateway-1") == ()
    assert (
        vm_ha_passive_replacement_cycle_for_approval(bindings, "gateway-1", "approval-invalid")
        is None
    )
    assert vm_ha_effective_resource_bindings(bindings)["compute:gateway-1"] == "vm-original"


@pytest.mark.parametrize("prior_key", ("heartbeat-initial-artifact", "heartbeat-upgrade-artifact"))
def test_missing_standby_replacements_persist_through_cycles_nine_ten_eleven(tmp_path, prior_key):
    config = tmp_path / "gateway.yaml"
    config.write_text("version: 1\n")
    store = VMHALifecycleStore(config)
    active = _missing_standby_state(prior_artifact_key=prior_key, start_replacement=False)
    owner = active.members[0]
    store.write_verified(active)
    history = {}
    observation = _replacement_observation()
    observation["members"][0].update(state="running", network_interface_name="eth0")
    observation["members"][1] = {"instance_name": "gateway-1", "present": False}

    def persist(previous, successor):
        store.write_verified(successor, predecessor_sha256=previous.record_sha256)
        loaded = store.read(expected_project_id="project-1", expected_gateway_name="gateway")
        assert loaded == successor
        return loaded

    for cycle in range(1, 12):
        old_bindings = dict(active.transaction.resource_bindings)
        artifact = f"{cycle + 100:064x}"
        old_key = prior_key if cycle == 1 else "heartbeat-initial-artifact"
        history[f"standby-replacement-{cycle}-prior-{old_key}:gateway-1"] = old_bindings[old_key]
        successor = VMHALifecycleState.start_missing_standby_replacement(
            active,
            target_instance_name="gateway-1",
            replacement_cycle=cycle,
            replacement_disk_name=vm_ha_missing_standby_disk_name(
                gateway_name="gateway",
                instance_name="gateway-1",
                predecessor_sha256=active.record_sha256,
                cycle=cycle,
            ),
            operation_id=f"{cycle:064x}",
            approval_digest=f"{cycle + 20:064x}",
            desired_state_digest=_digest("b"),
            current_state_digest=_digest("f"),
            current_observation=observation,
            artifact_sha256=artifact,
        )
        successor = persist(active, successor)
        journal = VMHALifecycleJournal(store, successor)
        for kind, action in (("disk", "create-boot-disk"), ("compute", "create-compute")):
            effect = vm_ha_missing_standby_replacement_effect("gateway-1", cycle, action)
            journal.begin(effect)
            # Reconstruct the journal after an interruption, preserving idempotency.
            operation_id = journal.effect_operation_id(effect)
            journal = VMHALifecycleJournal(
                store,
                store.read(expected_project_id="project-1", expected_gateway_name="gateway"),
            )
            assert journal.effect_operation_id(effect) == operation_id
            journal.complete(
                effect,
                resource_updates={
                    vm_ha_passive_replacement_binding_key(
                        kind, "gateway-1", cycle
                    ): f"{kind}-new-{cycle}"
                },
            )
        pending = journal.state
        activating = replace(
            pending,
            status=VMHALifecycleStatus.ACTIVATING,
            transaction=pending.transaction.advance(
                predecessor_sha256=pending.record_sha256, checkpoint="activating"
            ),
            members=(
                owner,
                replace(
                    pending.members[1],
                    compute_id=f"compute-new-{cycle}",
                    disk_id=f"disk-new-{cycle}",
                    compute_revision=str(cycle + 10),
                ),
            ),
        )
        activating = persist(pending, activating)
        active = persist(activating, activating.with_status(VMHALifecycleStatus.ACTIVE))
        bindings = dict(active.transaction.resource_bindings)
        assert all(bindings[key] == value for key, value in history.items())
        assert bindings["heartbeat-initial-artifact"] == artifact
        assert "heartbeat-upgrade-artifact" not in bindings
        assert vm_ha_passive_replacement_cycles(bindings, "gateway-1") == tuple(range(1, cycle + 1))
        effective = vm_ha_effective_resource_bindings(bindings)
        assert effective["compute:gateway-1"] == f"compute-new-{cycle}"
        assert effective["disk:gateway-1"] == f"disk-new-{cycle}"
        assert active.members[0] == owner
        assert active.allocation_id == "allocation-1"
        assert active.route_targets == ("route-table-1:10.0.0.0/8",)


@pytest.mark.parametrize("cycle", (1, 9, 10, 11, 99, 100, 1000))
@pytest.mark.parametrize("name", ("gateway-1", "10-gateway-1"))
@pytest.mark.parametrize("kind", ("failed", "missing"))
def test_replacement_compute_guard_keeps_exact_member_across_cycles(cycle, name, kind):
    builder = (
        vm_ha_passive_replacement_effect
        if kind == "failed"
        else vm_ha_missing_standby_replacement_effect
    )
    guard = VMHAEffectObservationGuard(
        effect=builder(name, cycle, "create-compute"),
        permitted_paths=(),
        pre_observation=(("/members/1/instance_name", json.dumps(name)),),
    )
    assert guard.permits(frozenset({"/members/1/state"}))
    assert not guard.permits(frozenset({"/members/0/state"}))
    assert not guard.permits(frozenset({"/shared_allocation/owner"}))


def test_pre_compute_member_resource_binding_is_not_claimed_as_observed() -> None:
    expected = {"disk:gateway-1": "disk-1"}

    assert vm_ha_resource_binding_matches_observation(
        "disk:gateway-1",
        "disk-1",
        observed={},
        expected=expected,
    )
    assert not vm_ha_resource_binding_matches_observation(
        "disk:gateway-1",
        "disk-1",
        observed={"compute:gateway-1": "foreign-compute"},
        expected=expected,
    )
    assert not vm_ha_resource_binding_matches_observation(
        "shared-allocation-id",
        "shared-1",
        observed={},
        expected=expected,
    )


def _member(index: int, role: str) -> VMHALifecycleMember:
    return VMHALifecycleMember(
        instance_index=index,
        instance_name=f"gateway-{index}",
        node_id=f"node-{index}",
        role=role,
        compute_id=f"vm-{index}",
        network_interface_name="eth0",
        public_ip=f"198.51.100.{index + 10}",
        compute_revision=str(index + 7),
        disk_id=f"disk-{index}",
        network_interface_subnet_id="subnet-1",
        primary_allocation_id=f"private-{index}",
        public_allocation_id=f"public-{index}",
        alias_allocation_ids=("allocation-1",) if role == "active" else (),
    )


def test_hardened_lifecycle_snapshot_detects_post_read_change(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    state = _provisioning_state()
    store.write_verified(state)

    snapshot = store.read_hardened(
        expected_project_id=state.project_id,
        expected_gateway_name=state.gateway_name,
    )
    assert snapshot is not None
    store.path.write_bytes(store.path.read_bytes() + b" ")

    with pytest.raises(RuntimeError, match="changed after preflight"):
        snapshot.assert_current()


def test_hardened_lifecycle_snapshot_rejects_hard_link(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    state = _provisioning_state()
    store.write_verified(state)
    (tmp_path / "lifecycle-copy.json").hardlink_to(store.path)

    with pytest.raises(ValueError, match="unsafe local metadata"):
        store.read_hardened(
            expected_project_id=state.project_id,
            expected_gateway_name=state.gateway_name,
        )


def _provisioning_state() -> VMHALifecycleState:
    return VMHALifecycleState.start_provisioning(
        project_id="project-1",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=(_member(0, "active"), _member(1, "passive")),
        operation_id="operation-12345678",
        approval_kind="migration",
        approval_digest=_digest("a"),
        desired_state_digest=_digest("b"),
        current_state_digest=_digest("c"),
    )


def _replacement_observation(*, passive_revision: str = "8") -> dict[str, object]:
    return {
        "members": [
            {
                "aliases": ["allocation-1"],
                "boot_disk_id": "disk-0",
                "compute_id": "vm-0",
                "compute_revision": "7",
                "instance_name": "gateway-0",
                "present": True,
            },
            {
                "aliases": [],
                "boot_disk_id": "disk-1",
                "compute_id": "vm-1",
                "compute_revision": passive_revision,
                "instance_name": "gateway-1",
                "present": True,
            },
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "allocation-1",
            "owner": {"compute_id": "vm-0", "network_interface_name": "eth0"},
            "present": True,
        },
    }


def _replacement_state() -> VMHALifecycleState:
    observation = _replacement_observation(passive_revision="7")
    state = VMHALifecycleState.start_provisioning(
        project_id="project-1",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=(_member(0, "active"), _member(1, "passive")),
        operation_id="operation-replacement",
        approval_kind="migration",
        approval_digest=_digest("a"),
        desired_state_digest=_digest("b"),
        current_state_digest=_digest("c"),
        initial_resource_bindings={
            "compute:gateway-0": "vm-0",
            "compute:gateway-1": "vm-1",
            "disk:gateway-0": "disk-0",
            "disk:gateway-1": "disk-1",
        },
        current_observation=observation,
    )
    assert state.transaction is not None
    transaction = state.transaction.advance(
        predecessor_sha256=state.record_sha256,
        completed_effect="provision-gateway-1-boot-disk",
    )
    state = replace(state, transaction=transaction)
    transaction = transaction.advance(
        predecessor_sha256=state.record_sha256,
        completed_effect="provision-gateway-1-compute",
    )
    return replace(state, transaction=transaction)


def _missing_standby_state(
    *, prior_artifact_key: str | None = None, start_replacement: bool = True
) -> VMHALifecycleState:
    members = (_member(0, "active"), _member(1, "passive"))
    observation = {
        "members": [
            {
                "aliases": ["allocation-1"],
                "boot_disk_id": "disk-0",
                "compute_id": "vm-0",
                "compute_revision": "7",
                "instance_name": "gateway-0",
                "network_interface_name": "eth0",
                "primary_allocation_id": "private-0",
                "public_allocation_id": "public-0",
                "public_ip": "198.51.100.10",
                "present": True,
                "state": "running",
            },
            {"instance_name": "gateway-1", "present": False},
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "allocation-1",
            "owner": {"compute_id": "vm-0", "network_interface_name": "eth0"},
            "present": True,
        },
    }
    initial = VMHALifecycleState.start_provisioning(
        project_id="project-1",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=members,
        operation_id="initial-operation",
        approval_kind="migration",
        approval_digest=_digest("a"),
        desired_state_digest=_digest("b"),
        current_state_digest=_digest("c"),
        initial_resource_bindings={
            **({prior_artifact_key: "a" * 64} if prior_artifact_key else {}),
            "compute:gateway-0": "vm-0",
            "compute:gateway-1": "vm-1",
            "disk:gateway-0": "disk-0",
            "disk:gateway-1": "disk-1",
            "primary-allocation:gateway-0:eth0": "private-0",
            "primary-allocation:gateway-1:eth0": "private-1",
            "public-allocation:gateway-0:eth0": "public-0",
            "public-allocation:gateway-1:eth0": "public-1",
            "shared-allocation-id": "allocation-1",
            "shared-allocation-owner-compute": "vm-0",
            "shared-allocation-owner-nic": "eth0",
        },
    )
    active = replace(
        initial,
        status=VMHALifecycleStatus.ACTIVE,
        allocation_id="allocation-1",
        route_runtime_id="route-runtime-1",
        route_targets=("route-table-1:10.0.0.0/8",),
    )
    if not start_replacement:
        return active
    disk_name = vm_ha_missing_standby_disk_name(
        gateway_name="gateway",
        instance_name="gateway-1",
        predecessor_sha256=active.record_sha256,
        cycle=1,
    )
    return VMHALifecycleState.start_missing_standby_replacement(
        active,
        target_instance_name="gateway-1",
        replacement_cycle=1,
        replacement_disk_name=disk_name,
        operation_id=_digest("d"),
        approval_digest=_digest("e"),
        desired_state_digest=_digest("b"),
        current_state_digest=_digest("f"),
        current_observation=observation,
    )


@pytest.mark.parametrize(
    "prior_key", (None, "heartbeat-initial-artifact", "heartbeat-upgrade-artifact")
)
@pytest.mark.parametrize(
    "artifact_sha256", ("a" * 64, "f" * 64), ids=("same-artifact", "new-artifact")
)
def test_active_missing_non_owner_starts_creation_only_replacement(
    tmp_path: Path, prior_key: str | None, artifact_sha256: str
) -> None:
    members = (_member(0, "passive"), _member(1, "active"))
    observation = {
        "members": [
            {
                "aliases": ["allocation-1"],
                "boot_disk_id": "disk-0",
                "compute_id": "vm-0",
                "compute_revision": "7",
                "instance_name": "gateway-0",
                "network_interface_name": "eth0",
                "present": True,
                "state": "running",
            },
            {"instance_name": "gateway-1", "present": False},
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "allocation-1",
            "owner": {"compute_id": "vm-0", "network_interface_name": "eth0"},
            "present": True,
        },
    }
    initial = VMHALifecycleState.start_provisioning(
        project_id="project-1",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=members,
        operation_id="initial-operation",
        approval_kind="migration",
        approval_digest=_digest("a"),
        desired_state_digest=_digest("b"),
        current_state_digest=_digest("c"),
        initial_resource_bindings={
            **({prior_key: "a" * 64} if prior_key else {}),
            "compute:gateway-0": "vm-0",
            "compute:gateway-1": "vm-1",
            "disk:gateway-0": "disk-0",
            "disk:gateway-1": "disk-1",
            "primary-allocation:gateway-0:eth0": "private-0",
            "primary-allocation:gateway-1:eth0": "private-1",
            "public-allocation:gateway-0:eth0": "public-0",
            "public-allocation:gateway-1:eth0": "public-1",
            "shared-allocation-id": "allocation-1",
            "shared-allocation-owner-compute": "vm-1",
            "shared-allocation-owner-nic": "eth0",
        },
    )
    active = replace(
        initial,
        status=VMHALifecycleStatus.ACTIVE,
        allocation_id="allocation-1",
        route_runtime_id="route-runtime-1",
        route_targets=("route-table-1:10.0.0.0/8",),
    )
    disk_name = vm_ha_missing_standby_disk_name(
        gateway_name="gateway",
        instance_name="gateway-1",
        predecessor_sha256=active.record_sha256,
        cycle=1,
    )

    successor = VMHALifecycleState.start_missing_standby_replacement(
        active,
        target_instance_name="gateway-1",
        replacement_cycle=1,
        replacement_disk_name=disk_name,
        operation_id=_digest("d"),
        approval_digest=_digest("e"),
        desired_state_digest=_digest("b"),
        current_state_digest=_digest("f"),
        current_observation=observation,
        artifact_sha256=artifact_sha256,
    )

    assert successor.status is VMHALifecycleStatus.PROVISIONING
    assert successor.members[0] == members[0]
    assert successor.members[1] == replace(
        members[1],
        compute_id="",
        compute_revision="",
        disk_id="",
    )
    assert successor.transaction is not None
    bindings = dict(successor.transaction.resource_bindings)
    assert bindings["retired-compute:gateway-1"] == "vm-1"
    assert bindings["retired-disk:gateway-1"] == "disk-1"
    assert bindings[vm_ha_missing_standby_disk_name_binding_key("gateway-1", 1)] == disk_name
    effective = vm_ha_effective_resource_bindings(bindings)
    assert "compute:gateway-1" not in effective
    assert "disk:gateway-1" not in effective
    assert effective["compute:gateway-0"] == "vm-0"
    assert effective["shared-allocation-owner-compute"] == "vm-0"

    assert bindings["heartbeat-initial-artifact"] == artifact_sha256
    assert "heartbeat-upgrade-artifact" not in bindings
    if prior_key:
        audit_key = f"standby-replacement-1-prior-{prior_key}:gateway-1"
        assert bindings[audit_key] == "a" * 64
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    store.write_verified(active)
    if prior_key:
        assert successor.transaction is not None
        for bad_value in (None, "0" * 64):
            bad_bindings = dict(bindings)
            if bad_value is None:
                del bad_bindings[audit_key]
            else:
                bad_bindings[audit_key] = bad_value
            tampered = replace(
                successor,
                transaction=replace(
                    successor.transaction, resource_bindings=tuple(sorted(bad_bindings.items()))
                ),
            )
            with pytest.raises(ValueError, match="audit bindings"):
                store.write_verified(tampered, predecessor_sha256=active.record_sha256)
    store.write_verified(successor, predecessor_sha256=active.record_sha256)
    persisted = store.read(expected_project_id="project-1", expected_gateway_name="gateway")
    assert persisted == successor
    assert persisted.transaction is not None
    assert (
        dict(persisted.transaction.resource_bindings)["heartbeat-initial-artifact"]
        == artifact_sha256
    )


def test_missing_non_owner_ssh_rotation_is_bound_before_cloud_creation(
    tmp_path: Path,
) -> None:
    members = (_member(0, "passive"), _member(1, "active"))
    observation = {
        "members": [
            {
                "aliases": ["allocation-1"],
                "boot_disk_id": "disk-0",
                "compute_id": "vm-0",
                "compute_revision": "7",
                "instance_name": "gateway-0",
                "network_interface_name": "eth0",
                "present": True,
                "state": "running",
            },
            {"instance_name": "gateway-1", "present": False},
        ],
        "route_targets": [],
        "routes": [],
        "shared_allocation": {
            "allocation_id": "allocation-1",
            "owner": {"compute_id": "vm-0", "network_interface_name": "eth0"},
            "present": True,
        },
    }
    initial = VMHALifecycleState.start_provisioning(
        project_id="project-1",
        gateway_name="gateway",
        cluster_id="cluster",
        allocation_name="gateway-cluster-shared-private-ip",
        members=members,
        operation_id="initial-operation",
        approval_kind="migration",
        approval_digest=_digest("a"),
        desired_state_digest=_digest("b"),
        current_state_digest=_digest("c"),
        initial_resource_bindings={
            "compute:gateway-0": "vm-0",
            "compute:gateway-1": "vm-1",
            "disk:gateway-0": "disk-0",
            "disk:gateway-1": "disk-1",
            "primary-allocation:gateway-0:eth0": "private-0",
            "primary-allocation:gateway-1:eth0": "private-1",
            "public-allocation:gateway-0:eth0": "public-0",
            "public-allocation:gateway-1:eth0": "public-1",
            "shared-allocation-id": "allocation-1",
            "shared-allocation-owner-compute": "vm-1",
            "shared-allocation-owner-nic": "eth0",
        },
    )
    active = replace(
        initial,
        status=VMHALifecycleStatus.ACTIVE,
        allocation_id="allocation-1",
        route_runtime_id="route-runtime-1",
        route_targets=("route-table-1:10.0.0.0/8",),
    )
    disk_name = vm_ha_missing_standby_disk_name(
        gateway_name="gateway",
        instance_name="gateway-1",
        predecessor_sha256=active.record_sha256,
        cycle=1,
    )
    successor = VMHALifecycleState.start_missing_standby_replacement(
        active,
        target_instance_name="gateway-1",
        replacement_cycle=1,
        replacement_disk_name=disk_name,
        operation_id=_digest("d"),
        approval_digest=_digest("e"),
        desired_state_digest=_digest("b"),
        current_state_digest=_digest("f"),
        current_observation=observation,
        ssh_identity_rotation={
            "action": "generate-and-rotate-missing-non-owner-ssh-identity",
            "hostname": "gateway-1",
            "old_fingerprint": "SHA256:old",
            "predecessor_projection_sha256": _digest("1"),
            "predecessor_receipt_sha256": _digest("2"),
            "storage_owner": "product-managed-default",
            "trust_scope_sha256": _digest("3"),
        },
    )
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    store.write_verified(active)
    store.write_verified(successor, predecessor_sha256=active.record_sha256)
    journal = VMHALifecycleJournal(store, successor)
    stage_effect = vm_ha_missing_standby_replacement_effect("gateway-1", 1, "stage-ssh-identity")
    publish_effect = vm_ha_missing_standby_replacement_effect("gateway-1", 1, "publish-ssh-trust")
    create_effect = vm_ha_missing_standby_replacement_effect("gateway-1", 1, "create-boot-disk")

    with pytest.raises(ValueError, match="preceded SSH trust publication"):
        journal.begin(create_effect)
    with pytest.raises(ValueError, match="published before identity staging"):
        journal.begin(publish_effect)

    journal.begin(stage_effect)
    journal.complete(
        stage_effect,
        resource_updates={
            vm_ha_missing_standby_ssh_binding_key("stage-token", "gateway-1", 1): _digest("4"),
            vm_ha_missing_standby_ssh_binding_key("new-fingerprint", "gateway-1", 1): "SHA256:new",
            vm_ha_missing_standby_ssh_binding_key("successor-receipt", "gateway-1", 1): _digest(
                "5"
            ),
            vm_ha_missing_standby_ssh_binding_key("successor-projection", "gateway-1", 1): _digest(
                "6"
            ),
        },
    )
    journal.begin(publish_effect)
    journal.complete(publish_effect)

    journal.begin(create_effect)
    assert journal.state.transaction is not None
    assert journal.state.transaction.pending_effect == create_effect


def _interrupted_passive_owner_activation() -> VMHALifecycleState:
    base = _provisioning_state()
    transaction = base.transaction
    assert transaction is not None
    return replace(
        base,
        status=VMHALifecycleStatus.ACTIVATING,
        allocation_id="allocation-1",
        route_runtime_id="route-runtime-1",
        route_targets=("route-table-1:10.0.0.0/8",),
        members=(
            replace(_member(0, "active"), alias_allocation_ids=()),
            replace(_member(1, "passive"), alias_allocation_ids=("allocation-1",)),
        ),
        transaction=replace(
            transaction,
            checkpoint="before-activate-node-1",
            pending_effect="activate-node-1",
            resource_bindings=(
                ("compute:gateway-0", "vm-0"),
                ("compute:gateway-1", "vm-1"),
                ("disk:gateway-0", "disk-0"),
                ("disk:gateway-1", "disk-1"),
                ("route-runtime-id", "route-runtime-1"),
                ("shared-allocation-id", "allocation-1"),
                ("shared-allocation-owner-compute", "vm-1"),
                ("shared-allocation-owner-nic", "eth0"),
            ),
        ),
    )


def test_activation_recovery_replaces_only_exact_configured_active_baseline(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    previous = _interrupted_passive_owner_activation()
    store.write_verified(previous)
    members = (
        replace(previous.members[0], compute_revision="9", alias_allocation_ids=("allocation-1",)),
        replace(previous.members[1], compute_revision="10", alias_allocation_ids=()),
    )
    bindings = {
        "compute:gateway-0": "vm-0",
        "compute:gateway-1": "vm-1",
        "disk:gateway-0": "disk-0",
        "disk:gateway-1": "disk-1",
        "route-runtime-id": "route-runtime-1",
        "shared-allocation-id": "allocation-1",
        "shared-allocation-owner-compute": "vm-0",
        "shared-allocation-owner-nic": "eth0",
    }
    successor = VMHALifecycleState.recover_interrupted_activation(
        previous,
        members=members,
        operation_id="activation-recovery-operation",
        approval_digest=_digest("d"),
        desired_state_digest=_digest("b"),
        current_state_digest=_digest("e"),
        initial_resource_bindings=bindings,
        current_observation={"members": [], "routes": []},
    )

    store.write_verified(successor, predecessor_sha256=previous.record_sha256)

    assert successor.status is VMHALifecycleStatus.PROVISIONING
    assert successor.transaction is not None
    assert successor.transaction.approval_kind == "recovery"
    assert successor.transaction.revision == 1
    assert successor.transaction.completed_effects == ()

    with pytest.raises(ValueError, match="configured-active alias owner"):
        VMHALifecycleState.recover_interrupted_activation(
            previous,
            members=(replace(members[0], alias_allocation_ids=()), members[1]),
            operation_id="activation-recovery-wrong-owner",
            approval_digest=_digest("d"),
            desired_state_digest=_digest("b"),
            current_state_digest=_digest("e"),
            initial_resource_bindings=bindings,
            current_observation={"members": [], "routes": []},
        )


def test_activation_recovery_rejects_cloud_effect_or_changed_identity() -> None:
    previous = _interrupted_passive_owner_activation()
    cloud_pending = replace(
        previous,
        transaction=replace(
            previous.transaction,
            checkpoint="before-attach-shared-allocation-active",
            pending_effect="attach-shared-allocation-active",
        ),
    )
    members = (
        replace(previous.members[0], compute_revision="9", alias_allocation_ids=("allocation-1",)),
        replace(previous.members[1], compute_revision="10", alias_allocation_ids=()),
    )
    bindings = {
        "compute:gateway-0": "vm-0",
        "compute:gateway-1": "vm-1",
        "disk:gateway-0": "disk-0",
        "disk:gateway-1": "disk-1",
        "route-runtime-id": "route-runtime-1",
        "shared-allocation-id": "allocation-1",
        "shared-allocation-owner-compute": "vm-0",
        "shared-allocation-owner-nic": "eth0",
    }
    arguments = {
        "operation_id": "activation-recovery-operation",
        "approval_digest": _digest("d"),
        "desired_state_digest": _digest("b"),
        "current_state_digest": _digest("e"),
        "initial_resource_bindings": bindings,
        "current_observation": {"members": [], "routes": []},
    }

    with pytest.raises(ValueError, match="cannot supersede a cloud effect"):
        VMHALifecycleState.recover_interrupted_activation(
            cloud_pending,
            members=members,
            **arguments,
        )
    with pytest.raises(ValueError, match="member compute_id changed"):
        VMHALifecycleState.recover_interrupted_activation(
            previous,
            members=(replace(members[0], compute_id="foreign-vm"), members[1]),
            **arguments,
        )


def test_failed_passive_replacement_is_append_only_and_overlays_live_bindings() -> None:
    state = _replacement_state()

    successor = state.authorize_failed_passive_replacement(
        passive_instance_name="gateway-1",
        approval_digest=_digest("d"),
        retired_compute_id="vm-1",
        retired_disk_id="disk-1",
        current_observation=_replacement_observation(),
    )
    assert successor.transaction is not None
    bindings = dict(successor.transaction.resource_bindings)
    assert bindings["compute:gateway-1"] == "vm-1"
    assert bindings["retired-compute:gateway-1"] == "vm-1"
    assert bindings["passive-replacement-approval:gateway-1"] == _digest("d")

    replacement = successor.transaction.advance(
        predecessor_sha256=successor.record_sha256,
        resource_updates={
            "replacement-compute:gateway-1": "vm-2",
            "replacement-disk:gateway-1": "disk-2",
        },
    )
    effective = vm_ha_effective_resource_bindings(dict(replacement.resource_bindings))
    assert effective["compute:gateway-1"] == "vm-2"
    assert effective["disk:gateway-1"] == "disk-2"
    assert "retired-compute:gateway-1" not in effective


def test_failed_passive_replacement_rejects_unrelated_drift() -> None:
    state = _replacement_state()
    observation = _replacement_observation()
    assert isinstance(observation["members"], list)
    observation["members"][0]["compute_revision"] = "changed"  # type: ignore[index]

    with pytest.raises(ValueError, match="unrelated cloud drift"):
        state.authorize_failed_passive_replacement(
            passive_instance_name="gateway-1",
            approval_digest=_digest("d"),
            retired_compute_id="vm-1",
            retired_disk_id="disk-1",
            current_observation=observation,
        )


def test_failed_passive_replacements_persist_through_cycles_nine_ten_eleven(tmp_path) -> None:
    config = tmp_path / "gateway.yaml"
    config.write_text("version: 1\n")
    store = VMHALifecycleStore(config)
    state = _replacement_state()
    state = replace(
        state, transaction=replace(state.transaction, revision=1, predecessor_sha256=None)
    )
    store.write_verified(state)
    observation = _replacement_observation()
    history = {}

    def persist(previous, successor):
        store.write_verified(successor, predecessor_sha256=previous.record_sha256)
        loaded = store.read(expected_project_id="project-1", expected_gateway_name="gateway")
        assert loaded == successor
        return loaded

    for cycle in range(1, 12):
        approved = state.authorize_failed_passive_replacement(
            passive_instance_name="gateway-1",
            approval_digest=f"{cycle + 20:064x}",
            retired_compute_id=f"vm-{cycle}",
            retired_disk_id=f"disk-{cycle}",
            current_observation=observation,
            replacement_cycle=cycle,
        )
        state = persist(state, approved)
        for kind in ("compute", "disk"):
            history[
                vm_ha_passive_replacement_binding_key(f"retired-{kind}", "gateway-1", cycle)
            ] = f"{'vm' if kind == 'compute' else 'disk'}-{cycle}"
        observation["members"][1].update(
            compute_id=f"vm-{cycle + 1}",
            boot_disk_id=f"disk-{cycle + 1}",
            compute_revision=str(cycle + 8),
        )
        for kind, action in (("disk", "create-boot-disk"), ("compute", "create-compute")):
            transaction = state.transaction.advance(
                predecessor_sha256=state.record_sha256,
                completed_effect=vm_ha_passive_replacement_effect("gateway-1", cycle, action),
                resource_updates={
                    vm_ha_passive_replacement_binding_key(
                        kind, "gateway-1", cycle
                    ): f"{'vm' if kind == 'compute' else 'disk'}-{cycle + 1}"
                },
                observation=normalize_vm_ha_observation(observation),
            )
            state = persist(state, replace(state, transaction=transaction))
        bindings = dict(state.transaction.resource_bindings)
        assert all(bindings[key] == value for key, value in history.items())
        assert vm_ha_passive_replacement_cycles(bindings, "gateway-1") == tuple(range(1, cycle + 1))
        assert vm_ha_effective_resource_bindings(bindings)["compute:gateway-1"] == f"vm-{cycle + 1}"


def test_failed_passive_replacement_rejects_wrong_role_and_late_activation() -> None:
    state = _replacement_state()
    with pytest.raises(ValueError, match="configured passive"):
        state.authorize_failed_passive_replacement(
            passive_instance_name="gateway-0",
            approval_digest=_digest("d"),
            retired_compute_id="vm-0",
            retired_disk_id="disk-0",
            current_observation=_replacement_observation(),
        )

    assert state.transaction is not None
    late = replace(
        state,
        transaction=state.transaction.advance(
            predecessor_sha256=state.record_sha256,
            completed_effect="stage-gateway-1",
        ),
    )
    with pytest.raises(ValueError, match="too late"):
        late.authorize_failed_passive_replacement(
            passive_instance_name="gateway-1",
            approval_digest=_digest("d"),
            retired_compute_id="vm-1",
            retired_disk_id="disk-1",
            current_observation=_replacement_observation(),
        )


def _bound_state() -> VMHALifecycleState:
    state = _provisioning_state()
    transaction = state.transaction
    assert transaction is not None
    return VMHALifecycleState(
        status=VMHALifecycleStatus.ACTIVATING,
        project_id=state.project_id,
        gateway_name=state.gateway_name,
        cluster_id=state.cluster_id,
        allocation_id="allocation-1",
        allocation_name=state.allocation_name,
        members=state.members,
        route_runtime_id="route-runtime-1",
        route_targets=("route-table-1:10.0.0.0/8",),
        transaction=transaction.advance(
            predecessor_sha256=state.record_sha256,
            checkpoint="binding-complete",
            resource_updates={"shared-allocation-id": "allocation-1"},
        ),
    )


def _v2_payload(status: VMHALifecycleStatus = VMHALifecycleStatus.ACTIVE) -> dict:
    state = _bound_state()
    identity = {
        "allocation_id": state.allocation_id,
        "allocation_name": state.allocation_name,
        "cluster_id": state.cluster_id,
        "gateway_name": state.gateway_name,
        "members": [member.to_dict(legacy=True) for member in state.members],
        "project_id": state.project_id,
    }
    digest = (
        __import__("hashlib")
        .sha256(
            json.dumps(
                {"identity": identity, "schema": LIFECYCLE_SCHEMA_V2, "status": status.value},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        )
        .hexdigest()
    )
    return {
        "identity": identity,
        "record_sha256": digest,
        "schema": LIFECYCLE_SCHEMA_V2,
        "status": status.value,
    }


def test_v3_lifecycle_checkpoints_every_effect_and_writes_active_last(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)

    provisioning = _provisioning_state()
    store.write_verified(provisioning)
    before = provisioning.begin_effect("create-shared-allocation")
    store.write_verified(before, predecessor_sha256=provisioning.record_sha256)
    after = before.complete_effect(
        "create-shared-allocation",
        resource_updates={"shared-allocation-id": "allocation-1"},
    )
    store.write_verified(after, predecessor_sha256=before.record_sha256)

    bound = _bound_state()
    # Rebase the fixture binding on the actual effect successor.
    assert bound.transaction is not None
    rebound = VMHALifecycleState(
        status=bound.status,
        project_id=bound.project_id,
        gateway_name=bound.gateway_name,
        cluster_id=bound.cluster_id,
        allocation_id=bound.allocation_id,
        allocation_name=bound.allocation_name,
        members=bound.members,
        route_runtime_id=bound.route_runtime_id,
        route_targets=bound.route_targets,
        transaction=after.transaction.advance(  # type: ignore[union-attr]
            predecessor_sha256=after.record_sha256,
            checkpoint="binding-complete",
        ),
    )
    store.write_verified(rebound, predecessor_sha256=after.record_sha256)
    passive_pending = rebound.begin_effect("verify-passive-unlocked")
    store.write_verified(passive_pending, predecessor_sha256=rebound.record_sha256)
    passive_verified = passive_pending.complete_effect("verify-passive-unlocked")
    store.write_verified(
        passive_verified,
        predecessor_sha256=passive_pending.record_sha256,
    )
    active = passive_verified.with_status(
        VMHALifecycleStatus.ACTIVE,
        checkpoint="activation-complete",
    )
    store.write_verified(active, predecessor_sha256=passive_verified.record_sha256)

    observed = store.read(expected_project_id="project-1", expected_gateway_name="gateway")
    assert observed == active
    assert observed.transaction is not None
    assert observed.transaction.completed_effects == (
        "create-shared-allocation",
        "verify-passive-unlocked",
    )
    assert observed.transaction.revision == 7
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_legacy_shared_allocation_guard_allows_only_scalar_unattached_owner() -> None:
    guard = VMHAEffectObservationGuard(
        effect="provision-shared-allocation",
        permitted_paths=("/shared_allocation/present",),
        pre_observation=normalize_vm_ha_observation({"shared_allocation": {"present": False}}),
    )
    unattached = normalize_vm_ha_observation(
        {"shared_allocation": {"owner": None, "present": True}}
    )
    attached = normalize_vm_ha_observation(
        {
            "shared_allocation": {
                "owner": {"compute_id": "compute-a", "network_interface_name": "eth0"},
                "present": True,
            }
        }
    )

    assert guard.permits(
        frozenset(path for path, _value in unattached)
        ^ frozenset(path for path, _value in guard.pre_observation)
    )
    assert guard.unpermitted(
        frozenset(path for path, _value in attached)
        ^ frozenset(path for path, _value in guard.pre_observation)
    ) == (
        "/shared_allocation/owner/compute_id",
        "/shared_allocation/owner/network_interface_name",
    )


def test_existing_compute_create_guard_allows_only_exact_target_provider_state() -> None:
    before = {
        "members": [
            {"instance_name": "gateway-0", "present": True, "state": "running"},
            {"instance_name": "gateway-1", "present": False},
        ]
    }
    guard = VMHAEffectObservationGuard(
        effect="replace-missing-gateway-1-create-compute",
        permitted_paths=("/members/1/present",),
        pre_observation=normalize_vm_ha_observation(before),
    )

    assert guard.unpermitted(frozenset({"/members/1/present", "/members/1/state"})) == ()
    assert guard.unpermitted(frozenset({"/members/0/state", "/members/1/state"})) == (
        "/members/0/state",
    )


def test_lifecycle_compare_and_swap_rejects_stale_writer(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    initial = _provisioning_state()
    store.write_verified(initial)
    first = initial.begin_effect("create-shared-allocation")
    store.write_verified(first, predecessor_sha256=initial.record_sha256)
    stale = initial.begin_effect("prepare-service-account")

    with pytest.raises(ValueError, match="compare-and-swap"):
        store.write_verified(stale, predecessor_sha256=initial.record_sha256)


@pytest.mark.parametrize(
    "effect",
    (
        "prepare-service-account",
        "provision-shared-allocation",
        "provision-gateway-1-boot-disk",
        "provision-gateway-1-eth0-public-allocation",
        "provision-gateway-1-eth0-primary-allocation",
        "provision-gateway-1-compute",
        "attach-shared-allocation-active",
        "resolve-authoritative-route-targets",
        "construct-authoritative-runtime-binding",
        "stage-node-b",
        "install-apply-lock-node-b",
        "install-owner-adoption-node-b",
        "activate-node-b",
        "verify-active-forwarding-and-routes",
        "verify-passive-unlocked-non-forwarding",
    ),
)
def test_every_vm_ha_effect_resumes_from_durable_before_checkpoint(
    tmp_path: Path,
    effect: str,
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    initial = _provisioning_state()
    store.write_verified(initial)

    journal = VMHALifecycleJournal(store, initial)
    operation_id = journal.begin(effect)
    interrupted = store.read(
        expected_project_id="project-1",
        expected_gateway_name="gateway",
    )
    assert interrupted is not None and interrupted.transaction is not None
    assert interrupted.transaction.pending_effect == effect

    resumed = VMHALifecycleJournal(store, interrupted)
    assert resumed.begin(effect) == operation_id
    resumed.complete(effect)
    completed = store.read(
        expected_project_id="project-1",
        expected_gateway_name="gateway",
    )
    assert completed is not None and completed.transaction is not None
    assert completed.transaction.pending_effect is None
    assert effect in completed.transaction.completed_effects


def test_owner_adoption_rewinds_only_an_interrupted_later_host_effect(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    interrupted = _interrupted_passive_owner_activation()
    assert interrupted.transaction is not None
    interrupted = replace(
        interrupted,
        transaction=replace(
            interrupted.transaction,
            checkpoint="before-verify-active-forwarding-and-routes",
            pending_effect="verify-active-forwarding-and-routes",
        ),
    )
    store.write_verified(interrupted)
    completed_before = interrupted.transaction.completed_effects

    journal = VMHALifecycleJournal(store, interrupted)
    adoption = "install-owner-adoption-node-1"
    journal.rewind_host_activation_for_owner_adoption(adoption)

    rewound = store.read(
        expected_project_id="project-1",
        expected_gateway_name="gateway",
    )
    assert rewound is not None and rewound.transaction is not None
    assert rewound.transaction.pending_effect is None
    assert rewound.transaction.completed_effects == completed_before
    assert rewound.transaction.checkpoint == f"rewind-before-{adoption}"

    journal.begin(adoption)
    journal.complete(adoption)
    journal.begin("verify-active-forwarding-and-routes")
    replaying = store.read(
        expected_project_id="project-1",
        expected_gateway_name="gateway",
    )
    assert replaying is not None and replaying.transaction is not None
    assert replaying.transaction.pending_effect == "verify-active-forwarding-and-routes"
    assert adoption in replaying.transaction.completed_effects


def test_owner_adoption_does_not_rewind_a_pending_cloud_effect() -> None:
    interrupted = _interrupted_passive_owner_activation()
    assert interrupted.transaction is not None
    interrupted = replace(
        interrupted,
        transaction=replace(
            interrupted.transaction,
            checkpoint="before-attach-shared-allocation-active",
            pending_effect="attach-shared-allocation-active",
        ),
    )

    with pytest.raises(ValueError, match="cannot rewind"):
        interrupted.rewind_host_activation_for_owner_adoption("install-owner-adoption-node-1")


def test_owner_refresh_rewinds_only_interrupted_standby_replacement_inhibition(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    state = _missing_standby_state()
    assert state.transaction is not None
    state = replace(
        state,
        transaction=replace(
            state.transaction,
            completed_effects=tuple(
                sorted(
                    (
                        *state.transaction.completed_effects,
                        "prepare-live-peer-replacement-owner-v2-node-0",
                    )
                )
            ),
        ),
    )
    store.write_verified(state)
    owner_refresh = "prepare-live-peer-replacement-owner-v5-node-0"
    inhibition = "install-standby-replacement-inhibition-node-0"
    journal = VMHALifecycleJournal(store, state)
    journal.begin(inhibition)

    journal.rewind_standby_replacement_inhibition_for_owner_refresh(
        owner_refresh_effect=owner_refresh,
        inhibition_effect=inhibition,
    )

    rewound = journal.state
    assert rewound.transaction is not None
    assert rewound.transaction.pending_effect is None
    assert rewound.transaction.checkpoint == f"rewind-before-{owner_refresh}"
    assert rewound.transaction.completed_effects == state.transaction.completed_effects
    journal.begin(owner_refresh)
    journal.complete(owner_refresh)
    journal.begin(inhibition)
    assert journal.state.transaction is not None
    assert journal.state.transaction.pending_effect == inhibition

    unrelated = state.begin_effect("unrelated-host-effect")
    with pytest.raises(ValueError, match="cannot be rewound"):
        unrelated.rewind_standby_replacement_inhibition_for_owner_refresh(
            owner_refresh_effect=owner_refresh,
            inhibition_effect=inhibition,
        )

    create_effect = vm_ha_missing_standby_replacement_effect(
        "gateway-1",
        1,
        "create-boot-disk",
    )
    assert state.transaction is not None
    cloud_started = replace(
        state,
        transaction=replace(
            state.transaction,
            checkpoint=f"before-{inhibition}",
            pending_effect=inhibition,
            completed_effects=tuple(sorted((*state.transaction.completed_effects, create_effect))),
        ),
    )
    with pytest.raises(ValueError, match="cannot be rewound"):
        cloud_started.rewind_standby_replacement_inhibition_for_owner_refresh(
            owner_refresh_effect=owner_refresh,
            inhibition_effect=inhibition,
        )


def test_lifecycle_resource_identity_is_fill_once(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    initial = _provisioning_state()
    store.write_verified(initial)
    before = initial.begin_effect("create-shared-allocation")
    store.write_verified(before, predecessor_sha256=initial.record_sha256)
    after = before.complete_effect(
        "create-shared-allocation",
        resource_updates={"shared-allocation-id": "allocation-1"},
    )
    store.write_verified(after, predecessor_sha256=before.record_sha256)

    with pytest.raises(ValueError, match="cannot change"):
        after.transaction.advance(  # type: ignore[union-attr]
            predecessor_sha256=after.record_sha256,
            resource_updates={"shared-allocation-id": "foreign-allocation"},
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("compute_id", "foreign-compute"),
        ("disk_id", "foreign-disk"),
        ("network_interface_name", "eth1"),
        ("network_interface_subnet_id", "foreign-subnet"),
        ("primary_allocation_id", "foreign-primary"),
        ("public_allocation_id", "foreign-public"),
        ("public_ip", "203.0.113.99"),
    ),
)
def test_provisioning_cannot_rebind_retained_member_identity(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    initial = _provisioning_state()
    store.write_verified(initial)
    members = (replace(initial.members[0], **{field: replacement}), initial.members[1])
    successor = replace(
        initial,
        members=members,
        transaction=initial.transaction.advance(  # type: ignore[union-attr]
            predecessor_sha256=initial.record_sha256,
        ),
    )

    with pytest.raises(ValueError, match=field):
        store.write_verified(successor, predecessor_sha256=initial.record_sha256)


def test_provisioning_binding_allows_only_shared_alias_addition(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    initial = replace(
        _provisioning_state(),
        members=(
            replace(_member(0, "active"), alias_allocation_ids=()),
            _member(1, "passive"),
        ),
    )
    store.write_verified(initial)
    transaction = initial.transaction
    assert transaction is not None
    legitimate = replace(
        initial,
        allocation_id="allocation-1",
        route_runtime_id="route-runtime-1",
        route_targets=("route-table-1:10.0.0.0/8",),
        members=(
            replace(
                initial.members[0],
                alias_allocation_ids=("allocation-1",),
                compute_revision="8",
            ),
            initial.members[1],
        ),
        status=VMHALifecycleStatus.ACTIVATING,
        transaction=transaction.advance(
            predecessor_sha256=initial.record_sha256,
            resource_updates={
                "route-runtime-id": "route-runtime-1",
                "shared-allocation-id": "allocation-1",
            },
        ),
    )
    foreign = replace(
        legitimate,
        members=(
            replace(
                legitimate.members[0],
                alias_allocation_ids=("allocation-1", "foreign-allocation"),
            ),
            legitimate.members[1],
        ),
    )
    with pytest.raises(ValueError, match="aliases cannot be rebound"):
        store.write_verified(foreign, predecessor_sha256=initial.record_sha256)

    store.write_verified(legitimate, predecessor_sha256=initial.record_sha256)


def test_managed_reapply_binding_accepts_exact_current_owner_alias_move(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    base = _provisioning_state()
    transaction = base.transaction
    assert transaction is not None
    initial = replace(
        base,
        allocation_id="allocation-1",
        route_runtime_id="route-runtime-1",
        route_targets=("route-table-1:10.0.0.0/8",),
        transaction=replace(
            transaction,
            resource_bindings=(
                ("shared-allocation-id", "allocation-1"),
                ("shared-allocation-owner-compute", "vm-1"),
                ("shared-allocation-owner-nic", "eth0"),
            ),
        ),
    )
    store.write_verified(initial)
    transaction_only = replace(
        initial,
        transaction=initial.transaction.advance(  # type: ignore[union-attr]
            predecessor_sha256=initial.record_sha256,
            checkpoint="before-provision-shared-allocation",
            pending_effect="provision-shared-allocation",
        ),
    )
    store.write_verified(transaction_only, predecessor_sha256=initial.record_sha256)
    successor = replace(
        transaction_only,
        status=VMHALifecycleStatus.ACTIVATING,
        members=(
            replace(
                transaction_only.members[0],
                alias_allocation_ids=(),
                compute_revision="9",
            ),
            replace(
                transaction_only.members[1],
                alias_allocation_ids=("allocation-1",),
                compute_revision="10",
            ),
        ),
        transaction=transaction_only.transaction.advance(  # type: ignore[union-attr]
            predecessor_sha256=transaction_only.record_sha256,
            pending_effect=None,
        ),
    )

    store.write_verified(successor, predecessor_sha256=transaction_only.record_sha256)

    observed = store.read(
        expected_project_id="project-1",
        expected_gateway_name="gateway",
    )
    assert observed == successor


def test_managed_reapply_binding_rejects_partial_owner_alias_refresh(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    base = _provisioning_state()
    transaction = base.transaction
    assert transaction is not None
    initial = replace(
        base,
        allocation_id="allocation-1",
        route_runtime_id="route-runtime-1",
        route_targets=("route-table-1:10.0.0.0/8",),
        transaction=replace(
            transaction,
            resource_bindings=(
                ("shared-allocation-id", "allocation-1"),
                ("shared-allocation-owner-compute", "vm-1"),
                ("shared-allocation-owner-nic", "eth0"),
            ),
        ),
    )
    store.write_verified(initial)
    partial = replace(
        initial,
        members=(
            replace(initial.members[0], alias_allocation_ids=()),
            initial.members[1],
        ),
        transaction=initial.transaction.advance(  # type: ignore[union-attr]
            predecessor_sha256=initial.record_sha256,
        ),
    )

    with pytest.raises(ValueError, match="aliases cannot be rebound"):
        store.write_verified(partial, predecessor_sha256=initial.record_sha256)


def test_provisioning_replay_accepts_only_forward_compute_revisions(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    initial = replace(
        _provisioning_state(),
        allocation_id="allocation-1",
        route_runtime_id="route-runtime-1",
        route_targets=("route-table-1:10.0.0.0/8",),
    )
    store.write_verified(initial)
    transaction = initial.transaction
    assert transaction is not None
    advanced = replace(
        initial,
        members=(
            replace(initial.members[0], compute_revision="9"),
            replace(initial.members[1], compute_revision="10"),
        ),
        status=VMHALifecycleStatus.ACTIVATING,
        transaction=transaction.advance(predecessor_sha256=initial.record_sha256),
    )

    store.write_verified(advanced, predecessor_sha256=initial.record_sha256)

    rolled_back = replace(
        initial,
        members=(replace(initial.members[0], compute_revision="6"), initial.members[1]),
        transaction=transaction.advance(predecessor_sha256=initial.record_sha256),
    )
    other_config = tmp_path / "other.yaml"
    other_config.write_text("version: 1\n", encoding="utf-8")
    other_store = VMHALifecycleStore(other_config)
    other_store.write_verified(initial)
    with pytest.raises(ValueError, match="compute revision cannot be rebound"):
        other_store.write_verified(rolled_back, predecessor_sha256=initial.record_sha256)


@pytest.mark.parametrize("status", list(VMHALifecycleStatus)[1:])
def test_v2_statuses_are_read_without_rewrite(tmp_path: Path, status: VMHALifecycleStatus) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    payload = _v2_payload(status)
    original = json.dumps(payload, indent=2) + "\n"
    store.path.write_text(original, encoding="utf-8")

    observed = store.read(expected_project_id="project-1", expected_gateway_name="gateway")

    assert observed is not None and observed.is_legacy_v2
    assert observed.status is status
    assert store.path.read_text(encoding="utf-8") == original
    with pytest.raises(ValueError, match="approved v3 successor"):
        observed.with_status(VMHALifecycleStatus.REMOVAL_IN_PROGRESS)


def test_v2_successor_requires_exact_predecessor_and_fresh_approval(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    store.path.write_text(json.dumps(_v2_payload()) + "\n", encoding="utf-8")
    previous = store.read(expected_project_id="project-1", expected_gateway_name="gateway")
    assert previous is not None
    observed_members = (
        replace(
            previous.members[0],
            compute_revision="8",
            disk_id="disk-0",
            network_interface_subnet_id="subnet-1",
            primary_allocation_id="private-0",
            public_allocation_id="public-0",
            alias_allocation_ids=("allocation-1", "retained-alias"),
        ),
        replace(
            previous.members[1],
            compute_revision="9",
            disk_id="disk-1",
            network_interface_subnet_id="subnet-1",
            primary_allocation_id="private-1",
            public_allocation_id="public-1",
        ),
    )
    successor = VMHALifecycleState.successor_from_v2(
        previous,
        operation_id="operation-v2-successor",
        approval_kind="recovery",
        approval_digest=_digest("d"),
        desired_state_digest=_digest("e"),
        current_state_digest=_digest("f"),
        observed_members=observed_members,
    )

    store.write_verified(successor, predecessor_sha256=previous.record_sha256)
    assert store.read(expected_project_id="project-1", expected_gateway_name="gateway") == successor
    assert successor.members == observed_members


def test_v3_is_read_without_rewrite_and_upgrades_with_fresh_observation(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    legacy = replace(_bound_state(), record_version=3)
    original = json.dumps(legacy.to_dict(), indent=2) + "\n"
    store.path.write_text(original, encoding="utf-8")

    observed = store.read(expected_project_id="project-1", expected_gateway_name="gateway")

    assert observed is not None and observed.record_version == 3
    assert store.path.read_text(encoding="utf-8") == original
    assert observed.transaction is not None
    previous_revision = observed.transaction.revision
    current_observation = {
        "allocation_id": "allocation-1",
        "members": [
            {"compute_id": "vm-0", "revision": "8"},
            {"compute_id": "vm-1", "revision": "9"},
        ],
    }

    successor = VMHALifecycleState.successor_from_v3(
        observed,
        current_observation=current_observation,
    )

    assert successor.record_version == 4
    assert successor.transaction is not None
    assert successor.transaction.predecessor_sha256 == observed.record_sha256
    assert successor.transaction.revision == previous_revision + 1
    assert successor.transaction.checkpoint == "v3-successor"
    assert successor.transaction.observation == normalize_vm_ha_observation(current_observation)
    store.write_verified(successor, predecessor_sha256=observed.record_sha256)
    assert store.read(expected_project_id="project-1", expected_gateway_name="gateway") == successor


def test_v3_successor_rejects_a_pending_legacy_effect() -> None:
    pending_v4 = _provisioning_state().begin_effect("prepare-service-account")
    pending_v3 = replace(pending_v4, record_version=3)

    with pytest.raises(ValueError, match="pending effect"):
        VMHALifecycleState.successor_from_v3(
            pending_v3,
            current_observation={"members": []},
        )


@pytest.mark.parametrize("field", ["status", "record_sha256", "transaction"])
def test_lifecycle_tamper_fails_closed(tmp_path: Path, field: str) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    store.write_verified(_provisioning_state())
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload[field] = "unexpected"
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="lifecycle"):
        store.read(expected_project_id="project-1", expected_gateway_name="gateway")


def test_apply_lock_key_ignores_config_filename_and_rejects_second_writer(
    tmp_path: Path,
) -> None:
    first = VMHAApplyLock(project_id="project-1", gateway_name="gateway", runtime_dir=tmp_path)
    second = VMHAApplyLock(project_id="project-1", gateway_name="gateway", runtime_dir=tmp_path)
    assert first.path == second.path
    with first, pytest.raises(RuntimeError, match="another gateway apply"), second:
        raise AssertionError("unreachable")


def test_apply_lock_rejects_symlinked_runtime_directory(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    runtime_dir = tmp_path / "lock-root"
    runtime_dir.symlink_to(target, target_is_directory=True)
    lock = VMHAApplyLock(
        project_id="project-1",
        gateway_name="gateway",
        runtime_dir=runtime_dir,
    )

    with pytest.raises(RuntimeError, match="runtime directory"), lock:
        raise AssertionError("unreachable")


def test_lifecycle_scope_mismatch_and_symlink_fail_closed(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    store = VMHALifecycleStore(config_path)
    store.write_verified(_provisioning_state())

    with pytest.raises(ValueError, match="project identity"):
        store.read(expected_project_id="project-2", expected_gateway_name="gateway")
    original = tmp_path / "original.json"
    store.path.replace(original)
    store.path.symlink_to(original)
    with pytest.raises(ValueError, match="regular file"):
        store.read(expected_project_id="project-1", expected_gateway_name="gateway")


def test_conflicting_heartbeat_artifact_intents_fail_closed() -> None:
    with pytest.raises(ValueError, match="artifact intent is ambiguous"):
        vm_ha_effective_resource_bindings(
            {"heartbeat-upgrade-artifact": "a" * 64, "heartbeat-initial-artifact": "a" * 64}
        )


@pytest.mark.parametrize("prior_key", ("heartbeat-initial-artifact", "heartbeat-upgrade-artifact"))
def test_missing_standby_preview_retires_completed_artifact_intent(prior_key: str) -> None:
    state = _missing_standby_state(prior_artifact_key=prior_key)
    assert state.transaction is not None
    bindings = dict(state.transaction.resource_bindings)
    assert "heartbeat-initial-artifact" not in bindings
    assert "heartbeat-upgrade-artifact" not in bindings
    assert bindings[f"standby-replacement-1-prior-{prior_key}:gateway-1"] == "a" * 64
    assert vm_ha_effective_resource_bindings(bindings)["compute:gateway-0"] == "vm-0"
