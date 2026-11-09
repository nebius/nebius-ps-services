import json
from copy import deepcopy
from dataclasses import replace

import pytest
from test_vm_ha_upgrade_recovery import checkpoint

from nebius_vpngw.deploy import ha_preparation as prep
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleJournal, VMHALifecycleStore


@pytest.fixture
def prepared(tmp_path):
    state = checkpoint()
    state = replace(state, transaction=replace(state.transaction, pending_effect=None))
    store = VMHALifecycleStore(tmp_path / "config.yaml")
    store.path.write_text(json.dumps(state.to_dict()))
    store.path.chmod(0o600)
    journal = VMHALifecycleJournal(store, state)
    statuses = {
        member.node_id: {
            "node_id": member.node_id,
            "apply_locked": True,
            "apply_operation_id": "7" * 64,
            "data_plane_mode": "passive",
            "promotion_ready": False,
            "pending_operation_id": None,
            "guard_boot_id": "boot-" + member.node_id,
            "apply_convergence": {
                "fresh": True,
                "data_plane_mode": "passive",
                "boot_id": "boot-" + member.node_id,
            },
            "heartbeat": {"peer_v6_fresh": True},
        }
        for member in state.members
    }
    kwargs = dict(
        artifact="a" * 64,
        preparation_scope={"prepare": True},
        continuation_scope={"prepare": False},
        statuses=statuses,
        lock_operation="7" * 64,
    )
    return journal, kwargs


def test_success_receipt_and_explicit_continuation_are_durable_and_idempotent(prepared):
    journal, kwargs = prepared
    assert prep.continuation(journal.state, artifact="a" * 64, scope={"prepare": False}) is None
    prep.publish(journal, **kwargs)
    before = journal.store.path.read_bytes()
    prep.publish(journal, **kwargs)
    assert journal.store.path.read_bytes() == before
    proposed, resumed = prep.continuation(
        journal.state, artifact="a" * 64, scope={"prepare": False}
    )
    assert not resumed and journal.store.path.read_bytes() == before
    prep.authorize(journal, proposed, artifact="a" * 64, scope={"prepare": False})
    before = journal.store.path.read_bytes()
    assert prep.continuation(journal.state, artifact="a" * 64, scope={"prepare": False}) == (
        proposed,
        True,
    )
    prep.authorize(journal, proposed, artifact="a" * 64, scope={"prepare": False})
    assert journal.store.path.read_bytes() == before


@pytest.mark.parametrize(
    "mutation", ["missing", "stale", "boot", "operation", "active", "unlocked", "peer"]
)
def test_missing_or_stale_guard_proof_cannot_publish_success(prepared, mutation):
    journal, kwargs = prepared
    kwargs = deepcopy(kwargs)
    node = next(iter(kwargs["statuses"]))
    status = kwargs["statuses"][node]
    if mutation == "missing":
        del kwargs["statuses"][node]
    elif mutation == "stale":
        status["apply_convergence"]["fresh"] = False
    elif mutation == "boot":
        status["guard_boot_id"] = "old-boot"
    elif mutation == "operation":
        status["apply_operation_id"] = "foreign"
    elif mutation == "active":
        status["data_plane_mode"] = "active"
    elif mutation == "unlocked":
        status["apply_locked"] = False
    else:
        status["heartbeat"]["peer_v6_fresh"] = False
    before = journal.store.path.read_bytes()
    with pytest.raises(RuntimeError):
        prep.publish(journal, **kwargs)
    assert journal.store.path.read_bytes() == before


@pytest.mark.parametrize("changed", ["scope", "artifact", "members", "operation"])
def test_preparation_never_authorizes_changed_continuation(prepared, changed):
    journal, kwargs = prepared
    prep.publish(journal, **kwargs)
    state = journal.state
    artifact, scope = "a" * 64, {"prepare": False}
    if changed == "scope":
        scope = {"prepare": False, "extra_effect": True}
    elif changed == "artifact":
        artifact = "f" * 64
    elif changed == "members":
        state = replace(
            state,
            members=(replace(state.members[0], compute_id="different-compute"), state.members[1]),
        )
    else:
        state = replace(
            state, transaction=replace(state.transaction, operation_id="different-operation")
        )
    with pytest.raises(RuntimeError):
        prep.continuation(state, artifact=artifact, scope=scope)


@pytest.mark.parametrize(
    "effect", ["verify-peer-rotation-prepared", "authorize-peer-rotation-continuation"]
)
@pytest.mark.parametrize("boundary", ["before-", "after-"])
def test_metadata_boundary_response_loss_resumes_exact_preparation_or_continuation(
    prepared, monkeypatch, effect, boundary
):
    journal, kwargs = prepared
    if effect.startswith("authorize"):
        prep.publish(journal, **kwargs)
        record, _ = prep.continuation(journal.state, artifact="a" * 64, scope={"prepare": False})
    else:
        record = None

    def execute(current):
        if record is None:
            prep.publish(current, **kwargs)
        else:
            prep.authorize(current, record, artifact="a" * 64, scope={"prepare": False})

    write = journal.store.write_verified

    def lose_response(state, **options):
        write(state, **options)
        if state.transaction.checkpoint == boundary + effect:
            raise TimeoutError("metadata response loss")

    with monkeypatch.context() as patcher:
        patcher.setattr(journal.store, "write_verified", lose_response)
        with pytest.raises(TimeoutError):
            execute(journal)
    resumed = VMHALifecycleJournal(
        journal.store,
        journal.store.read(
            expected_project_id=journal.state.project_id,
            expected_gateway_name=journal.state.gateway_name,
        ),
    )
    if record is not None:
        assert prep.continuation(resumed.state, artifact="a" * 64, scope={"prepare": False}) == (
            record,
            True,
        )
    execute(resumed)
    assert effect in resumed.state.transaction.completed_effects


@pytest.mark.parametrize("mutation", [None, "execution", "previous", "approval", "scope"])
def test_command_handoff_preserves_preparation_and_rejects_corrupt_authority(prepared, mutation):
    from nebius_vpngw.apply_approval import bind
    from nebius_vpngw.deploy.vm_ha_lifecycle import vm_ha_effective_resource_bindings

    journal, kwargs = prepared
    original = {
        "runtime-command-execution": "1" * 32,
        "runtime-command-approval": json.dumps(bind("2" * 64, kwargs["preparation_scope"])),
    }
    effect = "bind-test-preparation-command"
    journal.begin(effect)
    journal.complete(effect, resource_updates=original)
    prep.publish(journal, **kwargs)
    preparation_record = dict(journal.state.transaction.resource_bindings)[prep.PREPARED]
    record, _ = prep.continuation(
        journal.state, artifact=kwargs["artifact"], scope=kwargs["continuation_scope"]
    )
    options = dict(
        artifact=kwargs["artifact"],
        scope=kwargs["continuation_scope"],
        execution="3" * 32,
        previous_execution="1" * 32,
    )
    prep.authorize(journal, record, **options)
    before = journal.store.path.read_bytes()
    prep.authorize(journal, record, **options)
    assert journal.store.path.read_bytes() == before
    assert prep.command_authority(journal.state) == ("3" * 32, record)
    bindings = dict(journal.state.transaction.resource_bindings)
    assert all(bindings[key] == value for key, value in original.items())
    assert bindings[prep.PREPARED] == preparation_record
    assert prep.COMMAND not in vm_ha_effective_resource_bindings(bindings)
    with pytest.raises(RuntimeError, match="execution authority"):
        prep.authorize(journal, record, **{**options, "execution": "4" * 32})
    assert journal.store.path.read_bytes() == before
    if mutation is not None:
        command = json.loads(bindings[prep.COMMAND])
        if mutation == "execution":
            command["execution"] = "bad"
        elif mutation == "previous":
            command["previous_execution"] = "5" * 32
        elif mutation == "approval":
            command["approval"]["approval"] = "6" * 64
        else:
            command["approval"]["scope"] = "7" * 64
        bindings[prep.COMMAND] = json.dumps(command)
        with pytest.raises(ValueError, match="inconsistent command authority"):
            vm_ha_effective_resource_bindings(bindings)
