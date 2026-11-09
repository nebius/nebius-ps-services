"""Desired attachment updates and uncertainty, using generated Compute messages."""

import copy
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute import v1 as compute
from nebius.api.nebius.vpc import v1 as vpc

from nebius_vpngw.deploy import security_group_apply as apply
from nebius_vpngw.deploy.vm_ha_cloud import clone_nebius_sdk_message


@pytest.fixture
def cloud(monkeypatch):
    instance = compute.Instance(
        metadata=ResourceMetadata(
            id="compute-a", parent_id="project-a", name="gateway-a-0", resource_version=7
        ),
        spec=compute.InstanceSpec(
            resources=compute.ResourcesSpec(platform="cpu-d3", preset="4vcpu-16gb"),
            cloud_init_user_data="private-user-data-must-not-be-journaled",
            network_interfaces=[
                compute.NetworkInterfaceSpec(
                    name="eth0",
                    subnet_id="subnet-a",
                    ip_address=compute.IPAddress(allocation_id="private-a"),
                    public_ip_address=compute.PublicIPAddress(
                        allocation_id="public-a", static=True
                    ),
                    aliases=[compute.IPAlias(allocation_id="shared-a")],
                    security_groups=[compute.SecurityGroup(id="sg-a")],
                )
            ],
        ),
    )
    state = NS(
        instance=instance, requests=[], operations={}, effects=0, lose_receipt=False, timeout=False
    )

    def group(identifier):
        return NS(
            metadata=NS(id=identifier),
            spec=NS(network_id="network-a"),
            status=NS(default=identifier == "sg-default", state=NS(name="READY")),
        )

    group_service = NS(
        get=lambda request, **kw: NS(wait=lambda: group(request.id)),
        list_by_network=lambda request, **kw: NS(
            wait=lambda: NS(items=[group("sg-default")], next_page_token="")
        ),
        create=Mock(side_effect=AssertionError("No group mutation")),
        update=Mock(side_effect=AssertionError("No rule mutation")),
        delete=Mock(side_effect=AssertionError("No group deletion")),
    )
    monkeypatch.setattr(vpc, "SecurityGroupServiceClient", lambda _: group_service)
    monkeypatch.setattr(
        vpc,
        "SubnetServiceClient",
        lambda _: NS(
            get=lambda request, **kw: NS(
                wait=lambda: NS(metadata=NS(id="subnet-a"), spec=NS(network_id="network-a"))
            )
        ),
    )

    def update(request, **kw):
        assert 0 < kw["timeout"] <= 30
        key = dict(kw["metadata"])["x-idempotency-key"]
        state.requests.append((clone_nebius_sdk_message(request), key))
        if key not in state.operations:
            assert request.metadata.resource_version == state.instance.metadata.resource_version
            state.effects += 1
            state.instance.spec = clone_nebius_sdk_message(request.spec)
            state.instance.metadata.resource_version += 1

            def sync_wait(**args):
                assert args["timeout"] == 300
                if state.timeout:
                    raise TimeoutError("accepted operation still running")

            state.operations[key] = NS(
                id="operation-a",
                sync_wait=sync_wait,
                successful=lambda: True,
                resource_id="compute-a",
            )

        def wait():
            if state.lose_receipt:
                state.lose_receipt = False
                raise TimeoutError("accepted response lost")
            return state.operations[key]

        return NS(wait=wait)

    service = NS(
        get=lambda request, **kw: NS(wait=lambda: clone_nebius_sdk_message(state.instance)),
        update=update,
    )
    monkeypatch.setattr(compute, "InstanceServiceClient", lambda _: service)
    import nebius.api.nebius.common.v1 as common

    monkeypatch.setattr(
        common,
        "OperationServiceClient",
        lambda _: NS(
            get=lambda request, **kw: NS(wait=lambda: next(iter(state.operations.values())))
        ),
    )
    manager = NS(
        project_id="project-a",
        _get_client=lambda: object(),
        inspect_ordinary_targets=lambda names: {
            name: ("compute-a", "transport-a") for name in names
        },
        get_ha_instance=lambda _: clone_nebius_sdk_message(state.instance),
    )
    return state, manager


def journal_for(tmp_path, plan):
    journal = apply.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "sg")
    journal.__enter__()
    journal.begin(plan, scope="a" * 64, approval="b" * 64)
    return journal


@pytest.mark.parametrize(
    "drift",
    [None, "key", "target", "spec", "user-data", "revision", "unknown-metadata", "unknown-spec"],
)
def test_attachment_readback_refreshes_exact_compute_revision_without_changing_pins(
    cloud, tmp_path, monkeypatch, drift
):
    import hashlib
    from dataclasses import replace

    from nebius_vpngw.deploy.ssh_policy import SSHTrustPolicy
    from nebius_vpngw.deploy.vm_manager import VMManager

    state, manager = cloud
    # The live API may return fields newer than the pinned SDK. JSON journals
    # omit those fields, but the original binary request hash includes them.
    state.instance.metadata.__pb2_message__.MergeFromString(b"\xa2\x06\x03new")
    state.instance.spec.__pb2_message__.MergeFromString(b"\xf8\x07\x01")
    verifier = VMManager(project_id="project-a", region="eu-north1")
    monkeypatch.setattr(verifier, "_build_sdk_client", lambda _: object())
    monkeypatch.setattr(
        verifier,
        "_get_vm_by_id_for_ordinary_ssh_preflight",
        lambda *_: clone_nebius_sdk_message(state.instance),
    )
    monkeypatch.setattr(verifier, "_vm_public_ip_from_object", lambda _: "transport-a")
    spec = NS(vm_ha=None, region="eu-north1")

    def evidence():
        verifier._ordinary_ssh_preflight_snapshot = {
            "gateway-a-0": (clone_nebius_sdk_message(state.instance), "transport-a")
        }
        return tuple(
            verifier.ordinary_ssh_trust_bindings(spec, retained_hosts=("gateway-a-0",)).values()
        )

    pins = tmp_path / "pins"
    pins.write_text("immutable-test-pins")
    policy = SSHTrustPolicy(
        pins,
        hashlib.sha256(pins.read_bytes()).hexdigest(),
        pin_targets=(("gateway-a-0", "gateway-a-0"),),
        transport_targets=(("gateway-a-0", "transport-a"),),
        _evidence_checks=evidence(),
    )
    ssh = NS(_ssh_policy=policy)
    plan = apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    journal = apply.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "sg")
    refreshed = []

    def fresh():
        assert all(
            x["status"] == "settled" for x in journal.state["transaction"]["operations"].values()
        )
        refreshed.append(state.instance.metadata.resource_version)
        if drift == "spec":
            state.instance.spec.resources.preset = "8vcpu-32gb"
            state.instance.metadata.resource_version += 1
        if drift == "user-data":
            state.instance.spec.cloud_init_user_data = "unrelated-secret-change"
            state.instance.metadata.resource_version += 1
        if drift == "revision":
            state.instance.metadata.resource_version += 1
        if drift == "unknown-metadata":
            state.instance.metadata.__pb2_message__.MergeFromString(b"\xa2\x06\x03bad")
        if drift == "unknown-spec":
            state.instance.spec.__pb2_message__.MergeFromString(b"\xf8\x07\x02")
        candidate = replace(policy, _evidence_checks=evidence())
        if drift == "key":
            return replace(candidate, known_hosts_sha256="d" * 64)
        if drift == "target":
            return replace(candidate, transport_targets=(("gateway-a-0", "another-target"),))
        return candidate

    def guest(*_):
        ssh._ssh_policy.assert_current()
        return {"boot": "boot", "config": "c" * 64, "binding": None}

    monkeypatch.setattr(apply, "guest_identity", guest)

    def execute():
        apply.execute(
            manager,
            ssh,
            plan,
            {},
            (NS(hostname="gateway-a-0", vm_ha_node=None),),
            journal,
            scope="a" * 64,
            approval="b" * 64,
            refresh_ssh_policy=fresh,
        )

    if drift is not None:
        with pytest.raises(
            RuntimeError,
            match="cannot change|outside the exact|while refreshing|reconstruction changed",
        ):
            execute()
        assert ssh._ssh_policy is policy
        assert journal.pending()["verified"] == []
        assert state.effects == 1
        return
    execute()
    assert refreshed == [8]
    assert state.effects == 1
    assert journal.pending() is None
    assert ssh._ssh_policy.known_hosts_sha256 == policy.known_hosts_sha256
    state.instance.metadata.resource_version += 1
    with pytest.raises(RuntimeError, match="trust evidence changed"):
        ssh._ssh_policy.assert_current()


@pytest.mark.parametrize("identifiers", [("sg-a", "sg-b"), ("sg-b",), ()])
def test_updates_only_exact_nic_membership_and_noop_is_mutation_free(cloud, tmp_path, identifiers):
    state, manager = cloud
    original = clone_nebius_sdk_message(state.instance)
    plan = apply.inspect_plan(manager, ("gateway-a-0",), identifiers)
    journal = journal_for(tmp_path, plan)
    try:
        apply.update_member(manager, journal, "gateway-a-0")
        assert state.effects == 1
        actual = state.instance.spec.network_interfaces[0]
        assert [g.id for g in actual.security_groups] == list(identifiers)
        assert (
            actual.ip_address.__pb2_message__
            == original.spec.network_interfaces[0].ip_address.__pb2_message__
        )
        assert (
            actual.public_ip_address.__pb2_message__
            == original.spec.network_interfaces[0].public_ip_address.__pb2_message__
        )
        assert [x.__pb2_message__ for x in actual.aliases] == [
            x.__pb2_message__ for x in original.spec.network_interfaces[0].aliases
        ]
        assert state.instance.spec.cloud_init_user_data == original.spec.cloud_init_user_data
        assert "private-user-data-must-not-be-journaled" not in journal.path.read_text()
        apply.update_member(manager, journal, "gateway-a-0")
        assert state.effects == 1 and len(state.requests) == 1
        assert not apply.changed(apply.inspect_plan(manager, ("gateway-a-0",), identifiers))
    finally:
        journal.__exit__()


@pytest.mark.parametrize("failure", ["lose_receipt", "timeout"])
@pytest.mark.parametrize("new_api_metadata", [False, True])
def test_accepted_timeout_resumes_same_operation_before_readback(
    cloud, tmp_path, failure, new_api_metadata
):
    state, manager = cloud
    if new_api_metadata:
        state.instance.metadata.__pb2_message__.MergeFromString(b"\xa2\x06\x03new")
    plan = apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    journal = journal_for(tmp_path, plan)
    setattr(state, failure, True)
    with pytest.raises(TimeoutError):
        apply.update_member(manager, journal, "gateway-a-0")
    journal.__exit__()
    pending = journal.pending()
    observed = apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    assert apply.resume_plan(observed, pending, scope="a" * 64) == plan
    state.timeout = False
    with journal:
        apply.update_member(manager, journal, "gateway-a-0")
        assert state.effects == 1
        assert len({key for _, key in state.requests}) == 1
        assert all(
            record["status"] == "settled"
            for record in journal.state["transaction"]["operations"].values()
        )


def test_drift_changed_intent_and_nonpositive_revision_fail_closed(cloud, tmp_path):
    state, manager = cloud
    plan = apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    journal = journal_for(tmp_path, plan)
    try:
        state.instance.metadata.resource_version = 0
        with pytest.raises(RuntimeError, match="positive resource version"):
            apply.update_member(manager, journal, "gateway-a-0")
        state.instance.spec.network_interfaces[0].security_groups = [
            compute.SecurityGroup(id="sg-c")
        ]
        with pytest.raises(RuntimeError, match="drifted"):
            apply.require_plan(manager, plan)
        with pytest.raises(RuntimeError, match="different configuration"):
            apply.resume_plan(plan, journal.state["transaction"], scope="c" * 64)
        assert state.effects == 0
    finally:
        journal.__exit__()


def test_no_journal_or_default_reference_is_created_by_preview(tmp_path):
    journal = apply.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "absent")
    assert journal.pending() is None
    assert not journal.root.exists()


def test_new_repair_uses_a_new_cloud_key_after_same_drift_returns(cloud, tmp_path):
    state, manager = cloud
    plan = apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    journal = journal_for(tmp_path, plan)
    try:
        apply.update_member(manager, journal, "gateway-a-0")
        journal.state["transaction"].update(phase="complete", verified=["gateway-a-0"])
        journal.save()
        state.instance.spec.network_interfaces[0].security_groups = [
            compute.SecurityGroup(id="sg-a")
        ]
        state.instance.metadata.resource_version += 1
        journal.begin(plan, scope="a" * 64, approval="b" * 64)
        apply.update_member(manager, journal, "gateway-a-0")
        assert state.effects == 2
        assert [g.id for g in state.instance.spec.network_interfaces[0].security_groups] == ["sg-b"]
    finally:
        journal.__exit__()


def test_new_default_is_approval_drift(cloud, tmp_path):
    state, manager = cloud
    plan = apply.inspect_plan(manager, ("gateway-a-0",), ())
    assert plan["members"]["gateway-a-0"]["desired"][0]["explicit_ids"] == []
    original = copy.deepcopy(plan)
    plan["members"]["gateway-a-0"]["desired"][0]["effective_ids"] = ["sg-other-default"]
    with pytest.raises(RuntimeError, match="drifted"):
        apply.require_plan(manager, plan)
    assert original["desired_ids"] == []


@pytest.mark.parametrize(
    "failure_mode",
    [
        "none",
        "timeout",
        "reboot",
        "config-drift",
        "inhibition-unknown",
        "health",
        "owner-release-reply",
        "owner-release-unavailable",
        "standby-release-reply",
        "release-checkpoint",
        "release-guest-drift",
        "release-owner-read-drift",
        "release-passive-read-drift",
        "release-corrupt-cluster_id",
        "release-corrupt-node_id",
        "release-corrupt-generation_id",
        "release-corrupt-operation_id",
        "release-corrupt-passive-digest",
        "release-corrupt-passive-node",
        "release-corrupt-passive-missing",
    ],
)
@pytest.mark.parametrize("composed", [False, True])
def test_ha_attachment_preserves_owner_and_resumes_nonowner_first(
    cloud, tmp_path, monkeypatch, failure_mode, composed
):
    interrupted = failure_mode in {"timeout", "reboot", "config-drift", "inhibition-unknown"}
    release_interrupted = failure_mode in {
        "health",
        "owner-release-reply",
        "owner-release-unavailable",
        "standby-release-reply",
        "release-checkpoint",
        "release-guest-drift",
        "release-owner-read-drift",
        "release-passive-read-drift",
    } or failure_mode.startswith("release-corrupt-")
    from test_vm_ha_rearm import _binding

    from nebius_vpngw.deploy import vm_ha_cloud
    from nebius_vpngw.deploy.ssh_push import SSHPush

    state, manager = cloud
    plan = apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    plan["members"]["gateway-a-1"] = copy.deepcopy(plan["members"]["gateway-a-0"])
    plan["members"]["gateway-a-1"]["compute_id"] = "compute-b"
    binding = _binding()
    configs = tuple(
        NS(hostname="gateway-a-" + str(i), vm_ha_node=NS(node_id=node.node_id))
        for i, node in enumerate(binding.nodes)
    )
    targets = {"gateway-a-0": ("compute-a", "target-a"), "gateway-a-1": ("compute-b", "target-b")}
    manager.inspect_ordinary_targets = lambda _: targets
    manager.get_ha_allocation = lambda _: object()
    owner = vm_ha_cloud.AllocationOwner("compute-a", "eth0")
    monkeypatch.setattr(vm_ha_cloud, "allocation_observation", lambda *_: NS(owner=owner))
    events = []
    guest = {"boot_suffix": "-boot", "config": "c" * 64}
    drift_at_release = False
    resumed_reads = {}

    def identity(_ssh, _target, cfg, _local):
        if drift_at_release:
            resumed_reads[cfg.hostname] = resumed_reads.get(cfg.hostname, 0) + 1
        drift_name = (
            "gateway-a-0" if failure_mode == "release-owner-read-drift" else "gateway-a-1"
        )
        drift = (
            drift_at_release
            and cfg.hostname == drift_name
            and resumed_reads[cfg.hostname] == 2
        )
        return {
            "boot": cfg.hostname + guest["boot_suffix"],
            "config": "e" * 64 if drift else guest["config"],
            "binding": binding.model_dump(mode="json"),
        }

    monkeypatch.setattr(apply, "guest_identity", identity)
    # Revision preparation is exercised against installed guest state and the
    # real controller in test_vm_ha_attachment_continuity. This transport test
    # independently covers command/release interruption and exact lock authority.
    monkeypatch.setattr(apply, "prepare_owner_continuity", lambda *_a, **_kw: None)
    monkeypatch.setattr(apply, "require_plan", lambda *_args, **_kwargs: events.append("admission"))
    failed = False
    mutated = set()
    active_locks = set()
    owner_released = False

    def update(_manager, _journal, name):
        nonlocal failed
        if interrupted and name == "gateway-a-0" and not failed:
            failed = True
            raise TimeoutError("bounded owner mutation interrupted")
        if name not in mutated:
            events.append("update-" + name)
            mutated.add(name)

    monkeypatch.setattr(apply, "update_member", update)

    def install(_target, cfg, _local, **kwargs):
        if cfg.hostname == "gateway-a-0":
            # Exercise the actual controller response, not a mocked lock success.
            owner_decision(apply_locked=True, transfer_inhibited=False)
        else:
            from test_vm_ha_controller import _snapshot

            from nebius_vpngw.agent.vm_ha_controller import ControllerCheckpoint, VMHAController

            decision = VMHAController(peer_timeout_seconds=10, suspicion_seconds=30).decide(
                _snapshot(apply_locked=True), ControllerCheckpoint()
            )
            assert not decision.forwarding_enabled and decision.action is None
        events.append("lock-" + cfg.hostname)
        active_locks.add(cfg.hostname)
        return SSHPush._vm_ha_apply_lock_payload(
            cluster_id=binding.cluster_id,
            node_id=cfg.vm_ha_node.node_id,
            generation_id=binding.generation_id,
            operation_id=kwargs["operation_id"],
        )[1]

    def owner_decision(*, apply_locked, transfer_inhibited):
        from dataclasses import replace

        from test_vm_ha_controller import _owned_cloud, _peer, _reconciled, _snapshot

        from nebius_vpngw.agent.vm_ha_controller import (
            ComputeState,
            ControllerCheckpoint,
            DataPlaneMode,
            HAState,
            VMHAController,
        )

        active = _snapshot(
            cloud=replace(_owned_cloud(), former_owner_compute_state=ComputeState.RUNNING),
            apply_locked=apply_locked,
            transfer_inhibited=transfer_inhibited,
            standby_replacement_inhibited=transfer_inhibited,
            data_plane_mode=DataPlaneMode.ACTIVE,
            peer_heartbeat=replace(
                _peer(), service_healthy=False, promotion_ready=False, route_ready=False
            ),
        )
        active = replace(active, routes_reconciled_context=_reconciled(active))
        result = VMHAController(peer_timeout_seconds=10, suspicion_seconds=30).decide(
            active,
            ControllerCheckpoint(
                state=HAState.ACTIVE,
                established_ownership_context=active.ownership_context,
            ),
        )
        assert result.forwarding_enabled and result.action is None, (
            "Attachment inhibition deactivated the serving owner"
        )

    def inhibit(_target, name, _local, **kwargs):
        assert name == "gateway-a-0"
        assert not owner_released, "A released operation must never be reinhibited"
        owner_decision(apply_locked=False, transfer_inhibited=True)
        events.append("gate-" + name)
        return {
            "schema": "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
            "cluster_id": binding.cluster_id,
            "node_id": configs[0].vm_ha_node.node_id,
            "generation_id": binding.generation_id,
            "operation_id": kwargs["operation_id"],
        }

    def clear(_target, cfg, _local, **kwargs):
        nonlocal failed
        assert cfg.hostname in active_locks
        active_locks.remove(cfg.hostname)
        events.append("clear-" + cfg.hostname)
        if failure_mode == "standby-release-reply" and not failed:
            failed = True
            raise TimeoutError("standby unlock reply lost")

    def release_owner(_target, name, _local, **kwargs):
        nonlocal failed, owner_released
        assert name == "gateway-a-0"
        if failure_mode == "owner-release-unavailable" and not failed:
            failed = True
            raise TimeoutError("owner release reply lost before dispatch")
        owner_released = True
        events.append("release-" + name)
        if failure_mode == "owner-release-reply" and not failed:
            failed = True
            raise TimeoutError("owner release reply lost")

    def health():
        nonlocal failed
        events.append("health")
        if failure_mode == "health" and not failed:
            failed = True
            raise RuntimeError("original independent health failure")

    monkeypatch.setattr(
        apply,
        "guest_lock_present",
        lambda _ssh, _target, cfg, _local, _receipt: cfg.hostname in active_locks,
    )
    ssh = NS(
        install_vm_ha_apply_lock=install,
        inhibit_vm_ha_standby_replacement=inhibit,
        verify_vm_ha_standby_replacement_quiescent=lambda _target, name, _local, **kwargs: (
            events.append("quiescent-" + name)
        ),
        release_vm_ha_standby_replacement_inhibition=release_owner,
        verify_vm_ha_apply_inhibition_quiescent=lambda _target, name, _local, **kwargs: (
            events.append("quiescent-" + name)
        ),
        clear_vm_ha_apply_lock=clear,
    )
    journal = apply.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "ha")
    real_save = journal.save

    def save():
        nonlocal failed
        real_save()
        if (
            (
                failure_mode
                in {
                    "release-checkpoint",
                    "release-guest-drift",
                    "release-owner-read-drift",
                    "release-passive-read-drift",
                }
                or failure_mode.startswith("release-corrupt-")
            )
            and not failed
            and (journal.state["transaction"].get("release_started"))
        ):
            failed = True
            raise TimeoutError("interrupted after durable release intent")

    monkeypatch.setattr(journal, "save", save)
    from nebius_vpngw.deploy import attachment_command
    from nebius_vpngw.deploy.apply_command import CommandJournal

    parent = CommandJournal("project-a", "gateway-a", root=tmp_path / "command")

    def observe(*_args, **_kwargs):
        result = copy.deepcopy(plan)
        for name in mutated:
            result["members"][name]["before"] = result["members"][name]["desired"]
        return result

    monkeypatch.setattr(apply, "inspect_plan", observe)

    def execute():
        if composed:
            command = attachment_command.inspect(
                parent,
                observed=observe(),
                child=journal.read_transaction(),
                scope="a" * 64,
                artifact="f" * 64,
            )
            assert command is not None
            command.execute(manager, ssh, {}, configs, journal, verify_ha=health)
            assert parent.pending() is None
            return
        apply.execute(
            manager,
            ssh,
            plan,
            {},
            configs,
            journal,
            scope="a" * 64,
            approval="b" * 64,
            verify_ha=health,
        )

    if interrupted:
        with pytest.raises(TimeoutError):
            execute()
        assert journal.pending()["verified"] == ["gateway-a-1"]
        assert not any(item.startswith("clear-") for item in events)
        if failure_mode in {"reboot", "inhibition-unknown"}:
            guest["boot_suffix"] = "-new-boot"
        if failure_mode == "config-drift":
            guest["config"] = "e" * 64
            with pytest.raises(RuntimeError, match="configuration or runtime binding changed"):
                execute()
            assert "gateway-a-0" not in mutated
            return
        if failure_mode == "inhibition-unknown":
            ssh.verify_vm_ha_apply_inhibition_quiescent = Mock(side_effect=TimeoutError())
            with pytest.raises(TimeoutError):
                execute()
            assert "gateway-a-0" not in mutated
            assert journal.pending()["guests"]["gateway-a-0"]["boot"].endswith("-boot")
            assert not journal.pending()["guests"]["gateway-a-0"]["boot"].endswith("-new-boot")
            return
    if release_interrupted:
        error, message = (
            (RuntimeError, "original independent health failure")
            if failure_mode == "health"
            else (TimeoutError, "reply lost|durable release intent")
        )
        with pytest.raises(error, match=message):
            execute()
        assert journal.pending()["release_started"]
        assert set(journal.pending()["verified"]) == set(targets)
        before_resume = list(events)
        if failure_mode in {"owner-release-reply", "owner-release-unavailable"}:
            assert active_locks == {"gateway-a-1"}
            assert "clear-gateway-a-1" not in events
        if failure_mode == "release-guest-drift":
            guest["config"] = "e" * 64
            with pytest.raises(RuntimeError, match="configuration or runtime binding changed"):
                execute()
            assert events == before_resume
            return
        if failure_mode in {"release-owner-read-drift", "release-passive-read-drift"}:
            drift_at_release = True
            with pytest.raises(RuntimeError, match="identity changed before attachment release"):
                execute()
            assert not owner_released and active_locks == {"gateway-a-1"}
            assert not any(
                item.startswith(("release-", "clear-", "update-", "gate-", "lock-"))
                for item in events[len(before_resume) :]
            )
            assert max(resumed_reads.values()) == 2
            return
        if failure_mode.startswith("release-corrupt-"):
            import json

            record = json.loads(journal.path.read_text())
            field = failure_mode.removeprefix("release-corrupt-")
            if field == "passive-missing":
                record["transaction"]["locks"].pop("gateway-a-1")
            elif field.startswith("passive-"):
                receipt = record["transaction"]["locks"]["gateway-a-1"]
                receipt["record_sha256" if field == "passive-digest" else "node_id"] = "f" * 64
            else:
                record["transaction"]["owner_inhibition"][field] = "f" * 64
            journal.path.write_text(json.dumps(record))
            with pytest.raises((RuntimeError, ValueError)):
                execute()
            assert events == before_resume, "Corrupt release authority caused an effect"
            return
    execute()
    if release_interrupted:
        assert not any(
            item.startswith(("lock-", "gate-", "update-")) for item in events[len(before_resume) :]
        )
    assert (
        events.index("lock-gateway-a-1")
        < events.index("gate-gateway-a-0")
        < events.index("update-gateway-a-1")
        < events.index("update-gateway-a-0")
    )
    assert events.index("quiescent-gateway-a-0") < events.index("update-gateway-a-1")
    assert events.index("release-gateway-a-0") < events.index("clear-gateway-a-1")
    assert events[-1] == "health"
    assert not active_locks and owner_released
    assert events.count("update-gateway-a-1") == 1
    assert journal.pending() is None
    if failure_mode == "reboot":
        assert len(journal.read_transaction()["guest_history"]) == 1
        assert journal.read_transaction()["guests"]["gateway-a-0"]["boot"].endswith("-new-boot")


def test_status_does_not_turn_failed_cloud_observation_into_empty_membership(cloud):
    _, manager = cloud
    manager.get_ha_instance = Mock(side_effect=TimeoutError())
    value = apply.status(manager, ("gateway-a-0",), ("sg-b",))["gateway-a-0"]
    assert value == {"desired_ids": ["sg-b"], "observed": None, "state": "unknown"}


@pytest.mark.parametrize("state", ["absent", "exact", "foreign", "symlink", "unsafe"])
def test_attachment_unlock_observes_only_absent_or_exact_safe_lock(tmp_path, monkeypatch, state):
    import hashlib
    import io
    import json
    from contextlib import redirect_stdout

    from nebius_vpngw import apply_lock

    path = tmp_path / "apply.lock"
    raw = apply_lock.encode({"operation_id": "a" * 64, "apply_locked": True})
    expected = hashlib.sha256(raw).hexdigest()
    if state != "absent":
        target = tmp_path / "target" if state == "symlink" else path
        target.write_bytes(raw if state != "foreign" else apply_lock.encode({"foreign": True}))
        target.chmod(0o644 if state == "unsafe" else 0o600)
        if state == "symlink":
            path.symlink_to(target)

    def observe(_ssh, _target, _instance, _local, script):
        script = script.replace("/var/lib/nebius-vpngw/vm-ha/apply.lock", str(path))
        output = io.StringIO()
        with redirect_stdout(output):
            exec(compile(script, "attachment-lock-observer", "exec"), {})
        return json.loads(output.getvalue())

    monkeypatch.setattr(apply, "_guest_observation", observe)
    receipt = {"record_sha256": expected}
    if state in {"foreign", "symlink", "unsafe"}:
        with pytest.raises((OSError, ValueError)):
            apply.guest_lock_present(None, "target", None, {}, receipt)
    else:
        assert apply.guest_lock_present(None, "target", None, {}, receipt) == (state == "exact")


@pytest.mark.parametrize("mutation", ["request_type", "compute_id", "attachment"])
def test_journal_rejects_replay_outside_approved_attachment_update(cloud, tmp_path, mutation):
    state, manager = cloud
    plan = apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    journal = journal_for(tmp_path, plan)
    state.timeout = True
    try:
        with pytest.raises(TimeoutError):
            apply.update_member(manager, journal, "gateway-a-0")
        record = next(iter(journal.state["transaction"]["operations"].values()))
        if mutation == "request_type":
            record["request_type"] = "DeleteInstanceRequest"
        elif mutation == "compute_id":
            record["request_metadata"]["metadata"]["id"] = "foreign-compute"
        else:
            record["request_metadata"]["spec"]["network_interfaces"][0]["security_groups"] = [
                {"id": "foreign-group"}
            ]
        with pytest.raises(ValueError, match="transaction is invalid"):
            journal.validate(journal.state)
        assert state.effects == 1
    finally:
        journal.__exit__()


def corrective_fixture(cloud, tmp_path):
    _state, manager = cloud
    plan = apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    scope = apply.digest({"desired": {}, "artifact": "a" * 64, "project": "project-a"})
    journal = apply.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "corrective")
    with journal:
        journal.begin(plan, scope=scope, approval="b" * 64)
    context = {"prior_artifact": "a" * 64, "artifact": "c" * 64, "prior_record": "d" * 64}
    return journal, plan, context


def test_corrective_artifact_archives_unstarted_attachment_intent(cloud, tmp_path):
    journal, plan, context = corrective_fixture(cloud, tmp_path)
    before = journal.path.read_bytes()
    proposal = apply.corrective_prelude(
        plan, journal.pending(), corrective=context, local={}, project="project-a"
    )
    assert journal.path.read_bytes() == before
    with journal:
        journal.replace_unstarted(proposal, approval="e" * 64)
        saved = journal.path.read_bytes()
        journal.replace_unstarted(proposal, approval="e" * 64)
        journal.begin(plan, scope=proposal["scope"], approval="e" * 64)
        assert journal.path.read_bytes() == saved
    pending = journal.pending()
    history = journal.root / "history" / (pending["history"] + ".json")
    assert history.read_bytes() == before
    assert history.stat().st_mode & 0o777 == 0o600
    assert pending["approval"] == "e" * 64
    assert pending["plan"] == plan
    assert apply.resume_plan(plan, pending, scope=proposal["scope"]) == plan


@pytest.mark.parametrize(
    "field,value",
    [
        ("operations", {"uncertain": {"status": "intent"}}),
        ("operations", {"accepted": {"status": "accepted"}}),
        ("operations", {"settled": {"status": "settled"}}),
        ("locks", {"node": {}}),
        ("verified", ["node"]),
        ("guests", {}),
        ("owner", {}),
        ("member", 1),
    ],
)
def test_corrective_prelude_never_discards_started_or_uncertain_attachment_work(
    cloud, tmp_path, field, value
):
    journal, plan, context = corrective_fixture(cloud, tmp_path)
    pending = journal.pending()
    pending[field] = value
    with pytest.raises(RuntimeError, match="unstarted attachment prelude"):
        apply.corrective_prelude(plan, pending, corrective=context, local={}, project="project-a")


def test_successive_unstarted_corrective_preludes_keep_exact_lineage(cloud, tmp_path):
    journal, plan, context = corrective_fixture(cloud, tmp_path)
    first = apply.corrective_prelude(
        plan, journal.pending(), corrective=context, local={}, project="project-a"
    )
    with journal:
        journal.replace_unstarted(first, approval="e" * 64)
    second = apply.corrective_prelude(
        plan,
        journal.pending(),
        corrective={**context, "artifact": "f" * 64},
        local={},
        project="project-a",
    )
    with journal:
        journal.replace_unstarted(second, approval="1" * 64)
    assert len(list((journal.root / "history").glob("*.json"))) == 2
    with pytest.raises(RuntimeError, match="lineage differs"):
        apply.corrective_prelude(
            plan,
            journal.pending(),
            corrective={**context, "prior_record": "2" * 64},
            local={},
            project="project-a",
        )


def test_corrective_archive_survives_failure_before_canonical_successor(
    cloud, tmp_path, monkeypatch
):
    journal, plan, context = corrective_fixture(cloud, tmp_path)
    before = journal.path.read_bytes()
    proposal = apply.corrective_prelude(
        plan, journal.pending(), corrective=context, local={}, project="project-a"
    )
    with journal, monkeypatch.context() as patch:
        patch.setattr(journal, "save", Mock(side_effect=OSError("publication interrupted")))
        with pytest.raises(OSError):
            journal.replace_unstarted(proposal, approval="e" * 64)
    assert journal.path.read_bytes() == before
    with journal:
        journal.replace_unstarted(proposal, approval="e" * 64)
    assert len(list((journal.root / "history").glob("*.json"))) == 1


@pytest.mark.parametrize("change", ["config", "member", "policy"])
def test_corrective_prelude_requires_exact_original_scope(cloud, tmp_path, change):
    journal, plan, context = corrective_fixture(cloud, tmp_path)
    local = {}
    if change == "config":
        local["changed"] = True
    elif change == "member":
        plan["members"]["gateway-a-0"]["compute_id"] = "foreign-compute"
    else:
        plan["desired_ids"] = ["sg-other"]
    with pytest.raises(RuntimeError):
        apply.corrective_prelude(
            plan, journal.pending(), corrective=context, local=local, project="project-a"
        )
