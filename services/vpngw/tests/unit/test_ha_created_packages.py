"""Durability and identity boundaries for dependency selection after creation."""

import json
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace as NS

import pytest

from nebius_vpngw.apply_approval import bind
from nebius_vpngw.deploy import ha_created_packages as subject


@dataclass
class Artifact:
    sha256: str = "f" * 64
    path: str = "candidate.whl"
    dependency_plans: tuple = ()


@pytest.fixture
def trial(monkeypatch):
    members = tuple(
        NS(
            instance_name=f"gw-{i}",
            node_id=f"node-{i}",
            instance_index=i,
            role="active" if i == 0 else "passive",
            compute_id=f"compute-{i}",
            disk_id=f"disk-{i}",
            binding_complete=True,
        )
        for i in range(2)
    )
    bindings = {
        "runtime-command-approval": json.dumps(bind("a" * 64, {})),
        "runtime-command-execution": "e" * 32,
        "heartbeat-initial-artifact": "f" * 64,
        **{f"{kind}:gw-{i}": f"{kind}-{i}" for i in range(2) for kind in ("compute", "disk")},
    }
    tx = NS(
        operation_id="operation",
        resource_bindings=bindings,
        pending_effect=None,
        accepted_cloud_operation_id=None,
        observation_guard=None,
        completed_effects=[f"provision-gw-{i}-compute" for i in range(2)],
    )
    events = []

    class Journal:
        state = NS(transaction=tx, members=members)

        def begin(self, effect):
            assert tx.pending_effect in {None, effect}
            tx.pending_effect = effect

        def complete(self, effect, *, resource_updates):
            assert tx.pending_effect == effect
            bindings.update(resource_updates)
            tx.completed_effects.append(effect)
            tx.pending_effect = None
            events.append("durable")

    selected = []

    def inspect(_ssh, _target, instance, _local, **kwargs):
        selected.append(kwargs)
        return NS(
            manifest=deepcopy(
                kwargs.get("resume_manifest")
                or {
                    "dependency_wheels": {"dependency.whl": "a" * 64},
                    "wheel_sha256": "f" * 64,
                    "name": instance.hostname,
                }
            ),
            close=lambda: None,
        )

    def stage(_ssh, package, _local, *, artifact):
        assert subject.PREFIX + "operation" in bindings
        events.append("stage:" + package.manifest["name"])

    monkeypatch.setattr(subject.vm_ha_package, "inspect_plan", inspect)
    monkeypatch.setattr(subject.vm_ha_package, "stage", stage)
    command = NS(
        execution_id="e" * 32,
        intent={
            "packages": {"parent": "operation"},
            "members": {
                m.instance_name: {
                    "node_id": m.node_id,
                    "instance_index": m.instance_index,
                    "role": m.role,
                    "kind": "created",
                }
                for m in members
            },
        },
    )
    args = dict(
        command=command,
        journal=Journal(),
        artifact=Artifact(),
        ssh=None,
        local={},
        staged=[
            (NS(hostname=m.instance_name, config_yaml="config"), "target", None) for m in members
        ],
    )
    return args, events, selected


def run(args):
    with ExitStack() as resources:
        return subject.prepare(**args, resources=resources)


def test_all_selections_are_durable_before_stage_and_exactly_restored(trial):
    args, events, selected = trial
    first = run(args)
    assert events == ["durable", "stage:gw-0", "stage:gw-1"]
    assert len(first.dependency_plans) == 2
    assert all(item["resume_manifest"] is None for item in selected)
    second = run(args)
    assert all(item["resume_manifest"] is not None for item in selected[2:])
    assert (
        dict(first.dependency_plans)["gw-0"].manifest
        == dict(second.dependency_plans)["gw-0"].manifest
    )
    assert events.count("durable") == 1


def test_lost_publication_reply_does_not_resolve_again(trial, monkeypatch):
    args, events, selected = trial
    journal = args["journal"]
    original = journal.complete

    def lost_reply(*a, **kw):
        original(*a, **kw)
        raise TimeoutError("lost durable reply")

    monkeypatch.setattr(journal, "complete", lost_reply)
    with pytest.raises(TimeoutError):
        run(args)
    assert events == ["durable"]
    monkeypatch.setattr(journal, "complete", original)
    run(args)
    assert all(item["resume_manifest"] is not None for item in selected[2:])


def test_lost_selection_begin_has_no_package_effect_and_can_resume(trial, monkeypatch):
    from nebius_vpngw.deploy.vm_ha_lifecycle import vm_ha_activation_effect_is_host_only

    args, events, _selected = trial
    journal = args["journal"]
    original = journal.begin

    def lost_begin(effect):
        original(effect)
        raise TimeoutError("lost metadata begin reply")

    monkeypatch.setattr(journal, "begin", lost_begin)
    with pytest.raises(TimeoutError):
        run(args)
    assert events == []
    assert vm_ha_activation_effect_is_host_only(journal.state.transaction.pending_effect)
    monkeypatch.setattr(journal, "begin", original)
    run(args)
    assert events == ["durable", "stage:gw-0", "stage:gw-1"]


@pytest.mark.parametrize("pending", ["install-apply-lock-node-1", "activate-node-1"])
def test_restore_keeps_later_pending_host_effect_untouched(trial, pending):
    args, events, selected = trial
    run(args)
    args["journal"].state.transaction.pending_effect = pending
    run(args)
    assert args["journal"].state.transaction.pending_effect == pending
    assert events.count("durable") == 1
    assert all(item["resume_manifest"] is not None for item in selected[2:])


@pytest.mark.parametrize(
    "change", ["compute", "disk", "node", "creation", "execution", "artifact", "manifest"]
)
def test_changed_authority_rejects_before_stage(trial, change):
    args, events, _selected = trial
    run(args)
    events.clear()
    state = args["journal"].state
    tx = state.transaction
    if change in {"compute", "disk", "node"}:
        setattr(state.members[0], change + "_id", "foreign")
    elif change == "creation":
        tx.completed_effects.remove("provision-gw-0-compute")
    elif change == "execution":
        args["command"].execution_id = "d" * 32
    elif change == "artifact":
        args["artifact"].sha256 = "b" * 64
    else:
        key = subject.PREFIX + "operation"
        record = json.loads(tx.resource_bindings[key])
        record["manifests"].pop("gw-1")
        tx.resource_bindings[key] = json.dumps(record)
    with pytest.raises(RuntimeError):
        run(args)
    assert events == []


def test_second_selection_failure_stages_nothing(trial, monkeypatch):
    args, events, _selected = trial
    original = subject.vm_ha_package.inspect_plan

    def fail_second(*a, **kw):
        if a[2].hostname == "gw-1":
            raise RuntimeError("repository unavailable")
        return original(*a, **kw)

    monkeypatch.setattr(subject.vm_ha_package, "inspect_plan", fail_second)
    with pytest.raises(RuntimeError):
        run(args)
    assert events == []
    assert subject.PREFIX + "operation" not in args["journal"].state.transaction.resource_bindings
