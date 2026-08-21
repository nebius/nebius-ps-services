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
    vm_ha_passive_replacement_binding_key,
    vm_ha_passive_replacement_effect,
    vm_ha_resource_binding_matches_observation,
)


def _digest(character: str) -> str:
    return character * 64


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


def test_failed_passive_replacement_supports_a_second_append_only_cycle() -> None:
    first = _replacement_state().authorize_failed_passive_replacement(
        passive_instance_name="gateway-1",
        approval_digest=_digest("d"),
        retired_compute_id="vm-1",
        retired_disk_id="disk-1",
        current_observation=_replacement_observation(),
    )
    assert first.transaction is not None
    next_observation = _replacement_observation(passive_revision="9")
    next_members = next_observation["members"]
    assert isinstance(next_members, list)
    next_members[1]["compute_id"] = "vm-2"  # type: ignore[index]
    next_members[1]["boot_disk_id"] = "disk-2"  # type: ignore[index]
    transaction = first.transaction.advance(
        predecessor_sha256=first.record_sha256,
        completed_effect=vm_ha_passive_replacement_effect("gateway-1", 1, "create-boot-disk"),
        resource_updates={vm_ha_passive_replacement_binding_key("disk", "gateway-1", 1): "disk-2"},
    )
    first = replace(first, transaction=transaction)
    transaction = transaction.advance(
        predecessor_sha256=first.record_sha256,
        completed_effect=vm_ha_passive_replacement_effect("gateway-1", 1, "create-compute"),
        resource_updates={vm_ha_passive_replacement_binding_key("compute", "gateway-1", 1): "vm-2"},
        observation=normalize_vm_ha_observation(next_observation),
    )
    first = replace(first, transaction=transaction)

    second = first.authorize_failed_passive_replacement(
        passive_instance_name="gateway-1",
        approval_digest=_digest("e"),
        retired_compute_id="vm-2",
        retired_disk_id="disk-2",
        current_observation=next_observation,
        replacement_cycle=2,
    )

    assert second.transaction is not None
    bindings = dict(second.transaction.resource_bindings)
    assert bindings["retired-2-compute:gateway-1"] == "vm-2"
    assert bindings["retired-compute:gateway-1"] == "vm-1"
    assert bindings["passive-replacement-2-approval:gateway-1"] == _digest("e")
    assert "replace-failed-2-gateway-1-intent" in second.transaction.completed_effects


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
        interrupted.rewind_host_activation_for_owner_adoption(
            "install-owner-adoption-node-1"
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
    with first, pytest.raises(RuntimeError, match="another VM-HA apply"), second:
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
