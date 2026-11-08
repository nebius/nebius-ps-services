"""An attachment-only CAS must preserve HA service authority without masking drift."""

import ast
import io
import json
from contextlib import redirect_stdout
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
import yaml
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute import v1 as compute
from test_security_group_apply import cloud as cloud
from test_vm_ha_controller import _owned_cloud, _reconciled, _snapshot
from test_vm_ha_rearm import _binding, _runtime

from nebius_vpngw.agent.vm_ha import attachment_continuity as continuity
from nebius_vpngw.agent.vm_ha.inhibition import (
    STANDBY_REPLACEMENT_INHIBITION_FILENAME,
    STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
)
from nebius_vpngw.agent.vm_ha_controller import (
    ComputeState,
    ConfiguredRole,
    ControllerCheckpoint,
    ControllerResult,
    DataPlaneMode,
    HAState,
    VMHAController,
)
from nebius_vpngw.agent.vm_ha_rearm import _promotion_receipt
from nebius_vpngw.deploy.replacement_cloud import message_digest
from nebius_vpngw.deploy.ssh_push import SSHPush
from nebius_vpngw.deploy.vm_ha_cloud import clone_nebius_sdk_message


def _publish_inhibited_status(path, binding, *, node_id, operation, boot, epoch):
    """Use the production status writer, never a parallel status-schema fixture."""
    from nebius_vpngw.agent.main import VMHAControllerRuntime

    owner = node_id == "node-a"
    config = {"runtime_binding": binding.model_dump(mode="json"), "node": {"node_id": node_id}}
    snapshot = _snapshot(
        boot_id=boot,
        guard_boot_id=boot,
        cluster_id=binding.cluster_id,
        local_node_id=node_id,
        peer_node_id="node-b" if owner else "node-a",
        configured_role=ConfiguredRole.ACTIVE if owner else ConfiguredRole.PASSIVE,
        local_generation_id=binding.generation_id,
        peer_heartbeat=None,
        cloud=replace(
            _owned_cloud(),
            observed_owner_node_id="node-a",
            former_owner_compute_state=ComputeState.RUNNING,
            ownership_epoch=epoch,
        ),
        data_plane_mode=DataPlaneMode.ACTIVE if owner else DataPlaneMode.PASSIVE,
        apply_locked=not owner,
        transfer_inhibited=owner,
        standby_replacement_inhibited=owner,
    )
    snapshot = replace(snapshot, routes_reconciled_context=_reconciled(snapshot))
    runtime = NS(
        status_path=path / "status.json",
        snapshots=NS(
            config=config,
            last_snapshot=snapshot,
            last_apply_operation_id=None if owner else operation,
            last_transfer_inhibition_operation_id=operation if owner else None,
        ),
    )
    status = VMHAControllerRuntime._write_status(
        runtime,
        ControllerResult(
            state=HAState.ACTIVE if owner else HAState.BLOCKED,
            reasons=(),
            forwarding_enabled=owner,
            action=None,
            checkpoint=ControllerCheckpoint(),
        ),
    )
    assert "boot_id" not in status and status["guard_boot_id"] == boot
    return status


@pytest.fixture
def prepared(tmp_path):
    binding = _binding()
    before = compute.Instance(
        metadata=ResourceMetadata(id="compute-a", resource_version=42),
        spec=compute.InstanceSpec(
            network_interfaces=[
                compute.NetworkInterfaceSpec(
                    name="eth0",
                    aliases=[compute.IPAlias(allocation_id="allocation-a")],
                    security_groups=[compute.SecurityGroup(id="sg-a")],
                )
            ]
        ),
    )
    after = clone_nebius_sdk_message(before)
    after.metadata.resource_version = 43
    interfaces = list(after.spec.network_interfaces)
    interfaces[0].security_groups = [compute.SecurityGroup(id="sg-b")]
    after.spec.network_interfaces = interfaces
    proposal = {
        "schema": continuity.SCHEMA,
        "cluster_id": binding.cluster_id,
        "generation_id": binding.generation_id,
        "owner_node_id": "node-a",
        "compute_id": "compute-a",
        "operation_id": "d" * 64,
        "before_revision": "42",
        "after_revision": "43",
        "before_spec": message_digest(before.spec),
        "after_spec": message_digest(after.spec),
    }
    config = {"runtime_binding": binding.model_dump(mode="json"), "node": {"node_id": "node-a"}}
    status = _publish_inhibited_status(
        tmp_path,
        binding,
        node_id="node-a",
        operation=proposal["operation_id"],
        boot="boot-a",
        epoch="42",
    )
    gate = {key: proposal[key] for key in ("cluster_id", "generation_id", "operation_id")}
    gate.update(schema=STANDBY_REPLACEMENT_INHIBITION_SCHEMA, node_id="node-a")
    (tmp_path / STANDBY_REPLACEMENT_INHIBITION_FILENAME).write_text(json.dumps(gate))
    record = continuity.prepare(proposal, config=config, state_dir=tmp_path, boot_id="boot-a")
    return NS(
        binding=binding,
        before=before,
        after=after,
        proposal=proposal,
        record=record,
        config=config,
        status=status,
        directory=tmp_path,
    )


@pytest.mark.parametrize("role", list(ConfiguredRole))
def test_exact_attachment_cas_preserves_real_controller_forwarding(prepared, role):
    p = prepared
    original = _snapshot(
        configured_role=role,
        cloud=replace(_owned_cloud(), former_owner_compute_state=ComputeState.RUNNING),
        data_plane_mode=DataPlaneMode.ACTIVE,
        transfer_inhibited=True,
        standby_replacement_inhibited=True,
    )
    original = replace(original, routes_reconciled_context=_reconciled(original))
    checkpoint = ControllerCheckpoint(
        state=HAState.ACTIVE, established_ownership_context=original.ownership_context
    )
    controller = VMHAController(peer_timeout_seconds=10, suspicion_seconds=30)
    raw = replace(original, cloud=replace(original.cloud, ownership_epoch="43"))
    assert controller.decide(raw, checkpoint).action.kind.value == "disable-active"
    epoch = continuity.observe_epoch(p.after, state_dir=p.directory, binding=p.binding)
    fixed = replace(raw, cloud=replace(raw.cloud, ownership_epoch=epoch))
    decision = controller.decide(fixed, checkpoint)
    assert decision.forwarding_enabled and decision.action is None
    assert p.after.metadata.resource_version == 43  # CAS authority is never rewritten.


@pytest.mark.parametrize(
    "change", ["gap", "rollback", "alias", "unrelated", "compute", "generation"]
)
def test_no_continuity_for_drift_or_aba(prepared, change):
    p = prepared
    after = clone_nebius_sdk_message(p.after)
    binding = p.binding
    if change == "gap":
        after.metadata.resource_version = 44
    elif change == "rollback":
        after.metadata.resource_version = 41
    elif change == "alias":
        nics = list(after.spec.network_interfaces)
        nics[0].aliases = []
        after.spec.network_interfaces = nics
    elif change == "unrelated":
        after.spec.hostname = "changed"
    elif change == "compute":
        after.metadata.id = "replacement"
    else:
        binding = p.binding.model_copy(update={"generation_id": "f" * 64})
    assert continuity.observe_epoch(after, state_dir=p.directory, binding=binding) == str(
        after.metadata.resource_version
    )


def test_resume_and_next_operation_preserve_original_epoch(prepared):
    p = prepared
    prior = (p.directory / continuity.FILENAME).read_bytes()
    assert (
        continuity.prepare(p.proposal, config=p.config, state_dir=p.directory, boot_id="boot-a")
        == p.record
    )
    assert (p.directory / continuity.FILENAME).read_bytes() == prior
    next_proposal = {
        **p.proposal,
        "operation_id": "e" * 64,
        "before_revision": "43",
        "after_revision": "44",
        "before_spec": p.proposal["after_spec"],
        "after_spec": "f" * 64,
    }
    p.status["transfer_inhibition_operation_id"] = "e" * 64
    (p.directory / "status.json").write_text(json.dumps(p.status))
    gate_path = p.directory / STANDBY_REPLACEMENT_INHIBITION_FILENAME
    gate = json.loads(gate_path.read_text())
    gate["operation_id"] = "e" * 64
    gate_path.write_text(json.dumps(gate))
    result = continuity.prepare(
        next_proposal, config=p.config, state_dir=p.directory, boot_id="boot-a"
    )
    assert result["ownership_epoch"] == "42"


@pytest.mark.parametrize("change", ["operation", "boot", "epoch", "gate", "spec"])
def test_conflicting_preparation_has_no_write(prepared, change):
    p = prepared
    before = (p.directory / continuity.FILENAME).read_bytes()
    proposal = dict(p.proposal)
    boot = "boot-a"
    if change == "operation":
        proposal["operation_id"] = "e" * 64
    elif change == "boot":
        boot = "changed"
    elif change == "epoch":
        p.status["ownership_epoch"] = "43"
        (p.directory / "status.json").write_text(json.dumps(p.status))
    elif change == "gate":
        (p.directory / STANDBY_REPLACEMENT_INHIBITION_FILENAME).unlink()
    else:
        proposal["after_spec"] = "f" * 64
    with pytest.raises((ValueError, RuntimeError)):
        continuity.prepare(proposal, config=p.config, state_dir=p.directory, boot_id=boot)
    assert (p.directory / continuity.FILENAME).read_bytes() == before


@pytest.mark.parametrize("change", ["missing", "stale"])
def test_unpublished_boot_alias_cannot_replace_current_guard_evidence(prepared, change):
    p = prepared
    before = (p.directory / continuity.FILENAME).read_bytes()
    p.status["boot_id"] = "boot-a"
    if change == "missing":
        p.status.pop("guard_boot_id")
    else:
        p.status["guard_boot_id"] = "previous-boot"
    (p.directory / "status.json").write_text(json.dumps(p.status))
    with pytest.raises(RuntimeError, match="current quiescent guest identity"):
        continuity.prepare(p.proposal, config=p.config, state_dir=p.directory, boot_id="boot-a")
    assert (p.directory / continuity.FILENAME).read_bytes() == before


def test_passive_preparation_requires_exact_lock(prepared):
    p = prepared
    p.config["node"]["node_id"] = "node-b"
    p.status.update(data_plane_mode="passive", apply_locked=True, node_id="node-b")
    (p.directory / "status.json").write_text(json.dumps(p.status))
    with pytest.raises(RuntimeError, match="passive apply lock"):
        continuity.prepare(p.proposal, config=p.config, state_dir=p.directory, boot_id="boot-a")
    lock, _ = SSHPush._vm_ha_apply_lock_payload(
        cluster_id=p.binding.cluster_id,
        node_id="node-b",
        generation_id=p.binding.generation_id,
        operation_id=p.proposal["operation_id"],
    )
    (p.directory / "apply.lock").write_bytes(lock)
    assert (
        continuity.prepare(p.proposal, config=p.config, state_dir=p.directory, boot_id="boot-a")
        == p.record
    )


def test_rearm_uses_retained_epoch_but_preserves_raw_revision(tmp_path):
    runtime, cloud = _runtime(tmp_path, local_node_id="node-a")
    original = cloud.observe_instance
    cloud.observe_instance = lambda identifier: (
        replace(original(identifier), resource_version="11", retained_ownership_epoch="10")
        if identifier == runtime.local.compute_id
        else original(identifier)
    )
    receipt = _promotion_receipt(
        tmp_path / "promotion-receipt.json",
        binding=runtime.binding,
        local=runtime.local,
        peer=runtime.peer,
    )
    owner, _ = runtime._stable_owner_observation(receipt)
    assert owner.resource_version == "11" and owner.ownership_epoch == "10"


@pytest.mark.parametrize("damage", ["symlink", "malformed", "extra-field", "gap", "future-epoch"])
def test_invalid_durable_receipts_fail_closed(prepared, damage):
    p = prepared
    path = p.directory / continuity.FILENAME
    if damage == "symlink":
        target = p.directory / "record-copy.json"
        path.rename(target)
        path.symlink_to(target)
    elif damage == "malformed":
        path.write_text("{")
    else:
        record = dict(p.record)
        if damage == "extra-field":
            record["unknown"] = "x"
        elif damage == "gap":
            record["after_revision"] = "44"
        else:
            record["ownership_epoch"] = "43"
        path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        continuity.observe_epoch(p.after, state_dir=p.directory, binding=p.binding)


def test_receipt_retains_authority_after_process_and_boot_restart(prepared):
    p = prepared
    # The receipt proves a cloud spec transition, not a cached readiness sample.
    # A new process must re-read it, and native cold-start/boot gates still apply.
    (p.directory / "status.json").unlink()
    assert continuity.observe_epoch(p.after, state_dir=p.directory, binding=p.binding) == "42"


def test_cloud_and_rearm_ports_resolve_the_same_exact_instance(prepared, monkeypatch):
    from nebius_vpngw.agent.vm_ha.runtime import build_cloud_runtime
    from nebius_vpngw.agent.vm_ha_rearm import SDKRearmCloudPort
    from nebius_vpngw.deploy import vm_ha_cloud

    p = prepared
    observed = vm_ha_cloud.ComputeObservation(
        instance_id="compute-a",
        state=vm_ha_cloud.InstanceCloudState.RUNNING,
        resource_version="43",
        nic_allocations=(("eth0", "private-a"),),
        nic_alias_allocations=(("eth0", ("allocation-a",)),),
    )
    monkeypatch.setattr(vm_ha_cloud.NebiusSDKCloudClient, "get_instance", lambda *_: p.after)
    monkeypatch.setattr(vm_ha_cloud, "compute_observation", lambda *_: observed)
    monkeypatch.setattr("nebius_vpngw.agent.vm_ha_rearm.compute_observation", lambda *_: observed)
    runtime = build_cloud_runtime(p.binding, "node-a", object(), state_dir=p.directory)
    cloud_observation = runtime.adapter._read_instance("compute-a")
    rearm = SDKRearmCloudPort(object(), journal=NS(), binding=p.binding, state_dir=p.directory)
    rearm_observation = rearm.observe_instance("compute-a")
    assert cloud_observation == rearm_observation
    assert cloud_observation.resource_version == "43"
    assert cloud_observation.ownership_epoch == "42"


@pytest.mark.parametrize("lost_reply", ["gateway-a-0", "gateway-a-1"])
def test_command_prepares_both_guests_before_cas_and_resumes_exact_intent(
    cloud, tmp_path, monkeypatch, lost_reply
):
    from nebius_vpngw.deploy import security_group_apply as apply

    state, manager = cloud
    binding = _binding()
    plan = apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    journal = apply.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "command")
    with journal:
        journal.begin(plan, scope="a" * 64, approval="b" * 64)
        operation = "d" * 64
        configs = {
            "gateway-a-" + str(i): NS(
                hostname="gateway-a-" + str(i), vm_ha_node=NS(node_id=node.node_id)
            )
            for i, node in enumerate(binding.nodes)
        }
        targets = {
            name: (node.compute_id, "transport-" + name)
            for name, node in zip(configs, binding.nodes, strict=True)
        }
        paths = {}
        for name, cfg in configs.items():
            path = tmp_path / name
            path.mkdir()
            paths[name] = path
            owner = cfg.vm_ha_node.node_id == "node-a"
            _publish_inhibited_status(
                path,
                binding,
                node_id=cfg.vm_ha_node.node_id,
                operation=operation,
                boot="boot",
                epoch="7",
            )
            (path / "boot-id").write_text("boot\n")
            (path / "config.yaml").write_text(
                yaml.safe_dump(
                    {
                        "vm_ha": {
                            "enabled": True,
                            "runtime_binding": binding.model_dump(mode="json"),
                            "node": {"node_id": cfg.vm_ha_node.node_id},
                        }
                    }
                )
            )
            if owner:
                (path / STANDBY_REPLACEMENT_INHIBITION_FILENAME).write_text(
                    json.dumps(
                        {
                            "schema": STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
                            "cluster_id": binding.cluster_id,
                            "generation_id": binding.generation_id,
                            "node_id": "node-a",
                            "operation_id": operation,
                        }
                    )
                )
            else:
                lock, _ = SSHPush._vm_ha_apply_lock_payload(
                    cluster_id=binding.cluster_id,
                    node_id="node-b",
                    generation_id=binding.generation_id,
                    operation_id=operation,
                )
                (path / "apply.lock").write_bytes(lock)
        lost = False

        def observe(_ssh, _target, cfg, _local, script):
            nonlocal lost
            calls = [
                node
                for node in ast.walk(ast.parse(script))
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "loads"
            ]
            proposal = json.loads(ast.literal_eval(calls[0].args[0]))
            assert journal.read_transaction()["owner_continuity"] == proposal
            assert state.effects == 0
            from nebius_vpngw.agent import main

            path = paths[cfg.hostname]
            read_config = main._read_vm_ha_config
            # Execute the actual emitted guest program; redirect only fixture paths.
            program = script.replace("/var/lib/nebius-vpngw/vm-ha", str(path)).replace(
                "/proc/sys/kernel/random/boot_id", str(path / "boot-id")
            )
            output = io.StringIO()
            with monkeypatch.context() as context, redirect_stdout(output):
                context.setattr(
                    main, "_read_vm_ha_config", lambda: read_config(path / "config.yaml")
                )
                exec(compile(program, "<emitted-attachment-preparation>", "exec"), {})
            receipt = json.loads(output.getvalue())
            if cfg.hostname == lost_reply and not lost:
                lost = True
                raise TimeoutError("lost guest preparation reply")
            return receipt

        monkeypatch.setattr(apply, "_guest_observation", observe)
        arguments = dict(
            binding=binding,
            owner_name="gateway-a-0",
            operation=operation,
            configs=configs,
            targets=targets,
            local={},
        )
        with pytest.raises(TimeoutError):
            apply.prepare_owner_continuity(manager, NS(), journal, **arguments)
        assert state.effects == 0
        saved = dict(journal.state["transaction"]["owner_continuity"])
        apply.prepare_owner_continuity(manager, NS(), journal, **arguments)
        assert journal.state["transaction"]["owner_continuity"] == saved
        assert (paths["gateway-a-0"] / continuity.FILENAME).read_bytes() == (
            paths["gateway-a-1"] / continuity.FILENAME
        ).read_bytes()
        apply.update_member(manager, journal, "gateway-a-0")
        assert state.effects == 1 and state.instance.metadata.resource_version == 8
        for path in paths.values():
            assert continuity.observe_epoch(state.instance, state_dir=path, binding=binding) == "7"


@pytest.mark.parametrize("drift", ["revision", "spec-hash", "operation"])
def test_prepared_command_drift_is_rejected_before_guest_or_cloud_effects(
    cloud, tmp_path, monkeypatch, drift
):
    from unittest.mock import Mock

    from nebius_vpngw.deploy import security_group_apply as apply

    state, manager = cloud
    binding = _binding()
    plan = apply.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    journal = apply.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "journal")
    with journal:
        journal.begin(plan, scope="a" * 64, approval="b" * 64)
        wanted = apply.member_request(
            state.instance, plan["members"]["gateway-a-0"], manager._get_client()
        )
        proposal = {
            "schema": continuity.SCHEMA,
            "cluster_id": binding.cluster_id,
            "generation_id": binding.generation_id,
            "owner_node_id": "node-a",
            "compute_id": "compute-a",
            "operation_id": "d" * 64,
            "before_revision": "7",
            "after_revision": "8",
            "before_spec": message_digest(state.instance.spec),
            "after_spec": message_digest(wanted.spec),
        }
        if drift == "revision":
            state.instance.metadata.resource_version = 8
        elif drift == "spec-hash":
            proposal["after_spec"] = "f" * 64
        else:
            proposal["operation_id"] = "e" * 64
        journal.state["transaction"]["owner_continuity"] = proposal
        journal.save()
        observer = Mock(side_effect=AssertionError("No guest mutation may occur"))
        monkeypatch.setattr(apply, "_guest_observation", observer)
        with pytest.raises(RuntimeError, match="Attachment continuity"):
            apply.prepare_owner_continuity(
                manager,
                NS(),
                journal,
                binding=binding,
                owner_name="gateway-a-0",
                operation="d" * 64,
                configs={"gateway-a-0": NS(vm_ha_node=NS(node_id="node-a"))},
                targets={"gateway-a-0": ("compute-a", "target")},
                local={},
            )
        observer.assert_not_called()
        assert state.effects == 0
