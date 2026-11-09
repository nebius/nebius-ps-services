"""Reviewed request recovery retains command and guest inhibition authority."""

import ast
import json
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from grpc import StatusCode
from nebius.api.nebius.compute import v1 as compute
from test_replacement_cloud_recovery import request_error
from test_security_group_apply import cloud  # noqa: F401

from nebius_vpngw.deploy import attachment_command, attachment_retry
from nebius_vpngw.deploy import security_group_apply as sg
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.replacement_cloud import CloudOperationRejected


@pytest.fixture
def rejected(request, tmp_path, monkeypatch):
    state, manager = request.getfixturevalue("cloud")
    original_service = compute.InstanceServiceClient(object())

    def reject(request, **kwargs):
        assert kwargs["retries"] == 0
        state.instance.metadata.resource_version += 1
        raise request_error(StatusCode.ABORTED)

    monkeypatch.setattr(
        compute, "InstanceServiceClient", lambda _: NS(get=original_service.get, update=reject)
    )
    plan = sg.inspect_plan(manager, ("gateway-a-0",), ("sg-b",))
    parent = CommandJournal("project-a", "gateway-a", root=tmp_path / "parent")
    child = sg.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "child")
    command = attachment_command.inspect(
        parent, observed=plan, child=None, scope="a" * 64, artifact="b" * 64
    )
    assert command is not None
    monkeypatch.setattr(sg, "guest_identity", lambda *a: {"boot": "boot-a", "config": "c" * 64})
    # This suite isolates request successor/lock authority; strict SSH refresh
    # has its own real Compute-revision regression in test_security_group_apply.
    monkeypatch.setattr(sg, "refresh_guest_trust", lambda *a: None)
    instances = (NS(hostname="gateway-a-0", vm_ha_node=None),)
    with pytest.raises(CloudOperationRejected):
        command.execute(manager, object(), {}, instances, child, verify_ha=lambda: None)
    monkeypatch.setattr(compute, "InstanceServiceClient", lambda _: original_service)
    return state, manager, parent, child, command, instances


def test_reviewed_rejection_archives_history_and_resumes_original_command(rejected):
    state, manager, parent, child, command, instances = rejected
    parent_before, child_before = parent.path.read_bytes(), child.path.read_bytes()
    previous = child.pending()
    proposal = attachment_retry.plan(manager, parent, previous)
    assert proposal is not None
    assert parent.path.read_bytes() == parent_before and child.path.read_bytes() == child_before
    with pytest.raises(CloudOperationRejected):
        command.execute(manager, object(), {}, instances, child, verify_ha=lambda: None)
    command.execute(
        manager, object(), {}, instances, child, verify_ha=lambda: None, request_retry=proposal
    )
    current = child.read_transaction()
    assert current["phase"] == "complete" and state.effects == 1
    assert current["operation_id"] == previous["operation_id"]
    assert current["approval"] == previous["approval"]
    assert current["locks"] == previous["locks"]
    assert set(current["operations"]).isdisjoint(previous["operations"])
    assert parent.snapshot()["transaction"]["execution_id"] == previous["operation_id"]
    assert parent.pending() is None
    history = (
        child.root / "history" / (current["request_successors"]["gateway-a-0"]["history"] + ".json")
    )
    assert json.loads(history.read_text())["transaction"] == previous
    assert "private-user-data" not in child.path.read_text() + history.read_text()


@pytest.mark.parametrize(
    "change", ["uncertain-replay", "invalid-count", "unrelated-spec", "identity", "revision"]
)
def test_successor_rejects_missing_no_effect_or_exact_resource_proof(rejected, change):
    state, manager, parent, child, _command, _instances = rejected
    previous = deepcopy(child.pending())
    if change == "uncertain-replay":
        next(iter(previous["operations"].values()))["submission_count"] = 2
    elif change == "invalid-count":
        next(iter(previous["operations"].values()))["submission_count"] = True
    elif change == "unrelated-spec":
        state.instance.spec.cloud_init_user_data = "changed-private-value"
    elif change == "identity":
        state.instance.metadata.id = "foreign-compute"
    else:
        state.instance.metadata.resource_version = 0
    before = child.path.read_bytes()
    with pytest.raises(RuntimeError):
        attachment_retry.plan(manager, parent, previous)
    assert child.path.read_bytes() == before and state.effects == 0


@pytest.mark.parametrize("status", ["intent", "accepted", "failed"])
def test_uncertain_or_failed_operations_remain_with_existing_owner(rejected, status):
    _state, manager, parent, child, _command, _instances = rejected
    previous = deepcopy(child.pending())
    next(iter(previous["operations"].values()))["status"] = status
    assert attachment_retry.plan(manager, parent, previous) is None


def test_reviewed_request_changed_before_dispatch_keeps_predecessor(rejected):
    state, manager, parent, child, command, instances = rejected
    proposal = attachment_retry.plan(manager, parent, child.pending())
    state.instance.metadata.resource_version += 1
    with pytest.raises(RuntimeError, match="successor|changed"):
        command.execute(
            manager, object(), {}, instances, child, verify_ha=lambda: None, request_retry=proposal
        )
    assert state.effects == 0
    assert next(iter(child.pending()["operations"].values()))["status"] == "rejected"


def test_interruption_after_successor_publication_resumes_exact_review(rejected, monkeypatch):
    state, manager, parent, child, command, instances = rejected
    proposal = attachment_retry.plan(manager, parent, child.pending())
    real_update = sg.update_member
    monkeypatch.setattr(sg, "update_member", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(TimeoutError):
        command.execute(
            manager, object(), {}, instances, child, verify_ha=lambda: None, request_retry=proposal
        )
    saved = deepcopy(child.pending()["request_successors"])
    assert attachment_retry.plan(manager, parent, child.pending()) is None
    monkeypatch.setattr(sg, "update_member", real_update)
    command.execute(manager, object(), {}, instances, child, verify_ha=lambda: None)
    assert state.effects == 1 and child.read_transaction()["request_successors"] == saved


def test_apply_requires_successor_approval_and_preserves_original_resume_authority(
    rejected, monkeypatch
):
    from nebius_vpngw import cli
    from nebius_vpngw.deploy.apply_result import ApplyReport
    from nebius_vpngw.deploy.replacement import digest

    _state, manager, parent, child, _command, _instances = rejected
    before = child.path.read_bytes()
    report = ApplyReport(dry_run=True)
    proposal, original = cli._review_attachment_request_retry(
        manager, parent, child.pending(), supplied=None, dry_run=True, result_report=report
    )
    assert proposal is not None and original == child.pending()["approval"]
    assert report.mapping()["approval"] == digest(proposal)
    assert json.loads(json.dumps(report.mapping()))["pending"] == ["attachments"]
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    for supplied in (None, original):
        with pytest.raises(RuntimeError, match="approve-disruption|stale"):
            cli._review_attachment_request_retry(
                manager,
                parent,
                child.pending(),
                supplied=supplied,
                dry_run=False,
                result_report=ApplyReport(),
            )
    approved, retained = cli._review_attachment_request_retry(
        manager,
        parent,
        child.pending(),
        supplied=digest(proposal),
        dry_run=False,
        result_report=ApplyReport(),
    )
    assert approved == proposal and retained == original
    assert child.path.read_bytes() == before


@pytest.mark.parametrize("unknown_quiescence", [False, True])
@pytest.mark.parametrize("revision_drift", [False, True])
def test_ha_rejected_owner_request_preserves_both_locks_and_settled_standby(
    request, tmp_path, monkeypatch, unknown_quiescence, revision_drift
):
    from test_vm_ha_rearm import _binding

    from nebius_vpngw.deploy import vm_ha_cloud
    from nebius_vpngw.deploy.ssh_push import SSHPush

    state, manager = request.getfixturevalue("cloud")
    clone = vm_ha_cloud.clone_nebius_sdk_message
    standby = clone(state.instance)
    standby.metadata.id, standby.metadata.name = "compute-b", "gateway-a-1"
    resources = {"compute-a": state.instance, "compute-b": standby}
    targets = {"gateway-a-0": ("compute-a", "target-a"), "gateway-a-1": ("compute-b", "target-b")}
    manager.inspect_ordinary_targets = lambda names: {name: targets[name] for name in names}
    manager.get_ha_instance = lambda identifier: clone(resources[identifier])
    manager.get_ha_allocation = lambda _: object()
    owner = vm_ha_cloud.AllocationOwner("compute-a", "eth0")
    monkeypatch.setattr(vm_ha_cloud, "allocation_observation", lambda *_: NS(owner=owner))
    rejected_once = False
    mutations, locks, lock_history = [], {}, []

    def update(value, **kwargs):
        nonlocal rejected_once
        instance = resources[value.metadata.id]
        assert kwargs["retries"] == 0
        assert value.metadata.resource_version == instance.metadata.resource_version
        if value.metadata.id == "compute-a" and not rejected_once:
            rejected_once = True
            if revision_drift:
                instance.metadata.resource_version += 1
            raise request_error(StatusCode.ABORTED)
        mutations.append(value.metadata.id)
        instance.spec = clone(value.spec)
        instance.metadata.resource_version += 1
        return NS(
            wait=lambda: NS(
                id="operation-" + value.metadata.id,
                resource_id=value.metadata.id,
                sync_wait=lambda **kw: None,
                successful=lambda: True,
            )
        )

    monkeypatch.setattr(
        compute,
        "InstanceServiceClient",
        lambda _: NS(
            get=lambda value, **kw: NS(wait=lambda: clone(resources[value.id])),
            update=update,
        ),
    )
    binding = _binding()
    configs = tuple(
        NS(hostname="gateway-a-" + str(i), vm_ha_node=NS(node_id=node.node_id))
        for i, node in enumerate(binding.nodes)
    )
    monkeypatch.setattr(
        sg,
        "guest_identity",
        lambda _s, _t, cfg, _l: {
            "boot": cfg.hostname + "-boot",
            "config": "c" * 64,
            "binding": binding.model_dump(mode="json"),
        },
    )
    monkeypatch.setattr(sg, "refresh_guest_trust", lambda *a: None)

    from test_vm_ha_attachment_continuity import _publish_inhibited_status

    from nebius_vpngw.agent.vm_ha import attachment_continuity as continuity
    from nebius_vpngw.agent.vm_ha.inhibition import (
        STANDBY_REPLACEMENT_INHIBITION_FILENAME,
        STANDBY_REPLACEMENT_INHIBITION_SCHEMA,
    )

    guest_paths = {}

    def observe(_ssh, _target, cfg, _local, script):
        calls = [
            node
            for node in ast.walk(ast.parse(script))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "loads"
        ]
        proposal = json.loads(ast.literal_eval(calls[0].args[0]))
        assert child.read_transaction()["owner_continuity"] == proposal
        path = tmp_path / cfg.hostname
        if cfg.hostname not in guest_paths:
            path.mkdir()
            guest_paths[cfg.hostname] = path
            operation = proposal["operation_id"]
            _publish_inhibited_status(
                path,
                binding,
                node_id=cfg.vm_ha_node.node_id,
                operation=operation,
                boot="boot",
                epoch=proposal["before_revision"],
            )
            if cfg.vm_ha_node.node_id == "node-a":
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
        return continuity.prepare(
            proposal,
            config={
                "runtime_binding": binding.model_dump(mode="json"),
                "node": {"node_id": cfg.vm_ha_node.node_id},
            },
            state_dir=path,
            boot_id="boot",
        )

    monkeypatch.setattr(sg, "_guest_observation", observe)

    def install(_target, cfg, _local, **kwargs):
        operation = kwargs["operation_id"]
        assert cfg.hostname not in locks or locks[cfg.hostname] == operation
        locks[cfg.hostname] = operation
        lock_history.append((cfg.hostname, operation))
        return SSHPush._vm_ha_apply_lock_payload(
            cluster_id=binding.cluster_id,
            node_id=cfg.vm_ha_node.node_id,
            generation_id=binding.generation_id,
            operation_id=operation,
        )[1]

    def inhibit(_target, name, _local, **kwargs):
        assert name == "gateway-a-0"
        operation = kwargs["operation_id"]
        assert name not in locks or locks[name] == operation
        locks[name] = operation
        lock_history.append((name, operation))
        return {
            "schema": "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
            "cluster_id": binding.cluster_id,
            "node_id": configs[0].vm_ha_node.node_id,
            "generation_id": binding.generation_id,
            "operation_id": operation,
        }

    def clear(_target, cfg, _local, **kwargs):
        assert mutations == ["compute-b", "compute-a"]
        locks.pop(cfg.hostname)

    ssh = NS(
        install_vm_ha_apply_lock=install,
        inhibit_vm_ha_standby_replacement=inhibit,
        verify_vm_ha_standby_replacement_quiescent=lambda *a, **k: None,
        release_vm_ha_standby_replacement_inhibition=lambda _target, name, *a, **k: locks.pop(name),
        verify_vm_ha_apply_inhibition_quiescent=lambda *a, **k: None,
        clear_vm_ha_apply_lock=clear,
    )
    monkeypatch.setattr(sg, "guest_lock_present", lambda _s, _t, cfg, _l, _r: cfg.hostname in locks)
    parent = CommandJournal("project-a", "gateway-a", root=tmp_path / "ha-parent")
    child = sg.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "ha-child")
    command = attachment_command.inspect(
        parent,
        observed=sg.inspect_plan(manager, tuple(targets), ("sg-b",)),
        child=None,
        scope="a" * 64,
        artifact="b" * 64,
    )
    assert command is not None
    with pytest.raises(CloudOperationRejected):
        command.execute(manager, ssh, {}, configs, child, verify_ha=lambda: None)
    previous = child.pending()
    assert previous["verified"] == ["gateway-a-1"] and len(locks) == 2
    settled = {
        k: deepcopy(v) for k, v in previous["operations"].items() if v["status"] == "settled"
    }
    if revision_drift:
        parent_before, child_before = parent.path.read_bytes(), child.path.read_bytes()
        guests_before = {
            str(p): p.read_bytes() for path in guest_paths.values() for p in path.iterdir()
        }
        with pytest.raises(RuntimeError, match="prepared revision and spec"):
            attachment_retry.plan(manager, parent, previous)
        assert parent.path.read_bytes() == parent_before
        assert child.path.read_bytes() == child_before
        assert {
            str(p): p.read_bytes() for path in guest_paths.values() for p in path.iterdir()
        } == guests_before
        assert len(locks) == 2 and mutations == ["compute-b"]
        assert all(child.pending()["operations"][key] == value for key, value in settled.items())
        assert "request_successors" not in child.pending()
        return
    proposal = attachment_retry.plan(manager, parent, previous)
    if unknown_quiescence:
        ssh.verify_vm_ha_apply_inhibition_quiescent = lambda *a, **k: (_ for _ in ()).throw(
            TimeoutError()
        )
        with pytest.raises(TimeoutError):
            command.execute(
                manager, ssh, {}, configs, child, verify_ha=lambda: None, request_retry=proposal
            )
        assert "request_successors" not in child.pending()
        assert len(locks) == 2 and mutations == ["compute-b"]
        ssh.verify_vm_ha_apply_inhibition_quiescent = lambda *a, **k: None
    command.execute(
        manager, ssh, {}, configs, child, verify_ha=lambda: None, request_retry=proposal
    )
    current = child.read_transaction()
    assert current["phase"] == "complete" and not locks
    assert current["locks"] == previous["locks"]
    assert mutations == ["compute-b", "compute-a"]
    assert all(current["operations"][key] == value for key, value in settled.items())
    assert len({operation for _, operation in lock_history}) == 1
