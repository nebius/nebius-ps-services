from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_vm_ha_corrective as corrective_tests
import yaml
from test_ssh_push import _vm_ha_manifest_and_binding
from test_vm_ha_upgrade_recovery import checkpoint

from nebius_vpngw.apply_approval import digest
from nebius_vpngw.cli import _vm_ha_apply_operation_id, _vm_ha_mtls_operation_id
from nebius_vpngw.deploy import ha_replan
from nebius_vpngw.deploy.ssh_push import SSHPush
from nebius_vpngw.schema import VMHARuntimeBinding

reservation_guest = corrective_tests.reservation_guest


def project(raw):
    tree = ast.parse(ha_replan.PREDECESSOR_INSPECTION)
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "project"
    )
    scope = dict(
        yaml=yaml,
        hashlib=hashlib,
        VMHARuntimeBinding=VMHARuntimeBinding,
        validate_installed_credential_bundle=lambda *args: SimpleNamespace(bundle_digest="d" * 64),
        _vm_ha_mtls_operation_id=_vm_ha_mtls_operation_id,
        _vm_ha_apply_operation_id=_vm_ha_apply_operation_id,
    )
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "<predecessor-inspection>", "exec"),
        scope,
    )
    return scope["project"](raw)


def test_bound_guest_configuration_recovers_exact_original_package_hash():
    manifest, binding = _vm_ha_manifest_and_binding()
    raw = SSHPush._render_vm_ha_config(manifest, binding).encode()
    observed = project(raw)
    assert observed["sha256"] == hashlib.sha256(raw).hexdigest()
    assert observed["unbound_sha256"] == hashlib.sha256(manifest.config_yaml.encode()).hexdigest()
    assert observed["unbound_sha256"] != observed["sha256"]
    changed = yaml.safe_load(raw)
    changed["gateway"]["changed"] = "sensitive-sentinel"
    assert (
        project(yaml.safe_dump(changed, sort_keys=False).encode())["unbound_sha256"]
        != observed["unbound_sha256"]
    )
    assert "sensitive-sentinel" not in json.dumps(observed)


def predecessor():
    manifest, binding = _vm_ha_manifest_and_binding()
    observed = project(SSHPush._render_vm_ha_config(manifest, binding).encode())
    state = checkpoint()
    manifests = {
        member.instance_name: {
            "config_sha256": observed["unbound_sha256"],
            "files": {"nebius_vpngw/cli.py": "e" * 64},
        }
        for member in state.members
    }
    state = replace(
        state,
        transaction=replace(
            state.transaction,
            resource_bindings=tuple(
                sorted(
                    {
                        **dict(state.transaction.resource_bindings),
                        "runtime-package-manifests": json.dumps(manifests),
                    }.items()
                )
            ),
        ),
    )
    evidence = {
        member.node_id: {
            "installed": deepcopy(observed),
            "staged": deepcopy(observed),
            "source_sha256": {"cli.py": "e" * 64},
        }
        for member in state.members
    }
    return state, evidence


def test_old_generation_comes_from_verified_predecessor_not_new_settings():
    state, evidence = predecessor()
    generations = ha_replan.predecessor_generations(state, evidence)
    desired = {
        "cluster": "unchanged",
        "generation": {
            "generation_id": "a" * 64,
            "configuration_digest": "a" * 64,
            "static_routes_digest": hashlib.sha256(b"[]").hexdigest(),
            "bgp_policy_digest": hashlib.sha256(b"[]").hexdigest(),
        },
    }
    state = replace(
        state, transaction=replace(state.transaction, desired_state_digest=digest(desired))
    )
    changed = {**desired, "generation": {key: "c" * 64 for key in desired["generation"]}}
    ha_replan.require_same_infrastructure(state, changed, generations)
    with pytest.raises(ValueError, match="infrastructure identity"):
        ha_replan.require_same_infrastructure(state, {**changed, "cluster": "other"}, generations)


@pytest.mark.parametrize("projection", ["installed", "staged"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("unbound_sha256", "b" * 64),
        ("generation_id", "../../not-a-generation"),
        ("digests", {"configuration": "b" * 64}),
    ],
)
def test_drifted_predecessor_is_not_eligible(projection, field, value):
    state, evidence = predecessor()
    evidence[state.members[0].node_id][projection][field] = value
    with pytest.raises(ValueError, match="predecessor"):
        ha_replan.predecessor_generations(state, evidence)


def test_predecessor_requires_both_approved_member_manifests():
    state, evidence = predecessor()
    with pytest.raises(ValueError, match="incomplete"):
        ha_replan.predecessor_generations(
            state, {state.members[0].node_id: evidence[state.members[0].node_id]}
        )
    state = replace(state, transaction=replace(state.transaction, resource_bindings=()))
    with pytest.raises(ValueError, match="incomplete"):
        ha_replan.predecessor_generations(state, evidence)


def settlement_request(guest, request):
    return {
        "action": "replan-settle",
        "operation": "8" * 64,
        "previous_lock": request["previous_lock"],
        "children": request["children"],
        "boot_id": guest.ops.BOOT.read_text().strip(),
        "adoption_sha256": None,
        "package_reservation": None,
    }


def test_settlement_archives_and_quiesces_without_new_package_or_generation(reservation_guest):
    guest, original = reservation_guest
    request = settlement_request(guest, original)
    before = guest.corrective_children()
    lock = (guest.repair.STATE / "apply.lock").read_bytes()
    result = guest.settle_replan(request)
    assert result["settlement"]["state"] == "complete"
    assert result["children"] == before == guest.corrective_children()
    assert (guest.repair.STATE / "apply.lock").read_bytes() == lock
    assert not (guest.repair.STATE / "corrective").exists()
    assert all(
        effect["kind"] == "unit" and effect["action"] == "stop"
        for effect in result["settlement"]["effects"]
    )
    assert guest.settle_replan(request) == result


@pytest.mark.parametrize(
    "producer",
    [
        "nebius-vpngw-fix-routes.timer",
        "nebius-vpngw-fix-routes.service",
        "nebius-vpngw-health-monitor.service",
    ],
)
def test_settlement_drains_restart_producers_before_controller_stop(
    reservation_guest, monkeypatch, producer
):
    import time

    guest, original = reservation_guest
    # The streamed entry point supplies this budget before calling settlement.
    monkeypatch.setattr(guest.remote, "DEADLINE", time.monotonic() + 10)
    request = settlement_request(guest, original)
    controller = "nebius-vpngw-vm-ha.service"
    active = {producer, controller, "frr.service", "strongswan.service"}
    submitted = []

    def system(_self, method, *args):
        assert method == "EnqueueUnitJob"
        _signature, unit, action, mode = args
        assert action == "stop" and mode == "fail"
        submitted.append(unit)
        if unit == controller and producer in active:
            # A timer or its running worker can request Wants=FRR/strongSwan,
            # cancelling the controller stop through their Requires= chain.
            raise TimeoutError("route maintenance restarted controller dependencies")
        active.discard(unit)
        return {
            "type": "uososa(uosos)",
            "data": [len(submitted), "/job/1", unit, "/unit/1", action, []],
        }

    monkeypatch.setattr(guest.ops.Manager, "system", system)
    monkeypatch.setattr(guest.ops.Manager, "stopped", lambda _self, unit: unit not in active)
    result = guest.settle_replan(request)
    assert result["settlement"]["state"] == "complete"
    assert not active
    assert result["children"] == request["children"]
    assert guest.settle_replan(request) == result
    assert len(submitted) == len(set(submitted))


@pytest.mark.parametrize("boundary", ["intent", "archive", "operation", "receipt"])
def test_settlement_replays_original_identity_after_lost_reply(
    reservation_guest, monkeypatch, boundary
):
    guest, original = reservation_guest
    request = settlement_request(guest, original)
    publish, write = guest.repair.publish, guest.ops.Journal.write
    fired = False

    def crash():
        nonlocal fired
        if not fired:
            fired = True
            raise RuntimeError("injected settlement interruption")

    def publish_fault(path, raw, mode=0o600):
        publish(path, raw, mode)
        if path.name == (
            "intent.json"
            if boundary == "intent"
            else "runtime.json"
            if boundary == "archive"
            else ""
        ):
            crash()

    def write_fault(self, value, **kwargs):
        write(self, value, **kwargs)
        if self.path.parent.name == request["operation"] and (
            boundary == "operation"
            and value["state"] == "active"
            or boundary == "receipt"
            and value["state"] == "complete"
        ):
            crash()

    monkeypatch.setattr(guest.repair, "publish", publish_fault)
    monkeypatch.setattr(guest.ops.Journal, "write", write_fault)
    with pytest.raises(RuntimeError, match="injected settlement interruption"):
        guest.settle_replan(request)
    monkeypatch.setattr(
        guest.ops,
        "identity",
        lambda pid=None: {"pid": 222, "start": "2", "boot": guest.ops.BOOT.read_text()},
    )
    monkeypatch.setattr(guest.ops, "alive", lambda identity: identity["pid"] == 222)
    result = guest.settle_replan(request)
    assert result["settlement"]["id"] == request["operation"][:32]
    assert result["settlement"]["state"] == "complete"
    assert result["children"] == request["children"]


@pytest.mark.parametrize("field,value", [("boot_id", "different"), ("adoption_sha256", "a" * 64)])
def test_settlement_refuses_changed_identity_without_mutation(reservation_guest, field, value):
    guest, original = reservation_guest
    request = settlement_request(guest, original)
    request[field] = value
    before = guest.corrective_children()
    with pytest.raises(RuntimeError, match="predecessor changed"):
        guest.settle_replan(request)
    assert guest.corrective_children() == before
    assert not (guest.repair.STATE / "replanning").exists()


def test_settlement_reconciles_lost_service_reply_without_resubmitting(
    reservation_guest, monkeypatch
):
    guest, original = reservation_guest
    request = settlement_request(guest, original)
    unit = guest.ops.HA_WRITERS[0]
    stopped, calls = set(), []

    def system(_self, method, *args):
        assert method == "EnqueueUnitJob"
        assert args[1:] == (unit, "stop", "fail")
        calls.append(args)
        stopped.add(unit)
        raise ConnectionError("injected lost service acceptance")

    monkeypatch.setattr(guest.ops.Manager, "system", system)
    monkeypatch.setattr(
        guest.ops.Manager, "stopped", lambda _self, name: name != unit or name in stopped
    )
    with pytest.raises(ConnectionError, match="injected lost service acceptance"):
        guest.settle_replan(request)
    pending = guest.ops.Journal(
        guest.repair.STATE / "replanning" / request["operation"] / "operation.json"
    ).read()
    assert pending["effects"][0]["state"] == "intent"
    monkeypatch.setattr(
        guest.ops,
        "identity",
        lambda pid=None: {"pid": 222, "start": "2", "boot": guest.ops.BOOT.read_text()},
    )
    monkeypatch.setattr(guest.ops, "alive", lambda identity: identity["pid"] == 222)
    result = guest.settle_replan(request)
    assert result["settlement"]["id"] == pending["id"]
    assert result["settlement"]["state"] == "complete"
    assert result["settlement"]["effects"][0]["state"] == "settled"
    assert len(calls) == 1
    assert result["children"] == request["children"]


@pytest.fixture
def local_settlement(tmp_path, monkeypatch, request):
    from test_vm_ha_corrective import record
    from test_vm_ha_upgrade_recovery import evidence_fixture

    from nebius_vpngw.apply_approval import SCHEMA
    from nebius_vpngw.deploy import ha_command, ha_created_packages, ha_members
    from nebius_vpngw.deploy.apply_command import CommandJournal
    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStore

    monkeypatch.setenv("HOME", str(tmp_path))
    state, evidence, generations = evidence_fixture(tmp_path)
    old_desired = {
        "fixed": "identity",
        "generation": {
            "generation_id": generations["node-0"][0],
            **{key + "_digest": value for key, value in generations["node-0"][1].items()},
        },
    }
    desired = deepcopy(old_desired)
    desired["generation"]["generation_id"] = "9" * 64
    desired["generation"]["configuration_digest"] = "8" * 64
    journal = CommandJournal(state.project_id, state.gateway_name)
    initial = getattr(request, "param", False) == "initial"
    member_intent = ha_members.intent(
        state.members, created={m.instance_name for m in state.members}
    )
    for item in member_intent.values():
        item["compute_id"] = item["disk_id"] = None
    command = ha_command.create(
        journal,
        scope="1" * 64,
        artifact="a" * 64,
        action_scope=digest(
            {
                "recreate": False,
                "prepare_peer_rotation": False,
                "destructive": False,
                "managed_ssh": None,
                "managed_credentials": "reuse",
            }
        ),
        targets=ha_members.targets(member_intent)
        if initial
        else tuple(sorted(member.compute_id for member in state.members)),
        members=member_intent if initial else None,
        artifact_key="heartbeat-initial-artifact" if initial else "heartbeat-upgrade-artifact",
        approval="4" * 64,
        runtime_scope="5" * 64,
        report={"kind": "apply-convergence"},
        attachment_plan=None,
    )
    with journal:
        command.begin()
        command.start_runtime()
    manifests = {}
    for member in state.members:
        item = evidence[member.node_id]
        item["source_sha256"] = {"cli.py": "e" * 64}
        for projection in (item["installed"], item["staged"]):
            projection["unbound_sha256"] = "d" * 64
        manifests[member.instance_name] = {
            "wheel_sha256": "a" * 64,
            "config_sha256": "d" * 64,
            "files": {"nebius_vpngw/cli.py": "e" * 64},
        }
    state = replace(
        state,
        transaction=replace(
            state.transaction,
            desired_state_digest=digest(old_desired),
            resource_bindings=tuple(
                sorted(
                    {
                        **dict(state.transaction.resource_bindings),
                        "runtime-command-execution": command.execution_id,
                        "runtime-command-approval": json.dumps(
                            {"schema": SCHEMA, "approval": command.approval, "scope": "5" * 64}
                        ),
                        "runtime-package-manifests": json.dumps(manifests),
                    }.items()
                )
            ),
        ),
    )
    children = record(state)["children"]
    if initial:
        tx = state.transaction
        raw = dict(tx.resource_bindings)
        artifact = raw.pop("heartbeat-upgrade-artifact")
        raw.update(
            {
                "heartbeat-initial-artifact": artifact,
                "runtime-package-manifests": "{}",
                ha_created_packages.PREFIX + tx.operation_id: json.dumps(
                    {
                        "authority": {
                            "execution": command.execution_id,
                            "operation": tx.operation_id,
                            "artifact": artifact,
                            "lineage": {
                                m.instance_name: {
                                    "compute": m.compute_id,
                                    "disk": m.disk_id,
                                    "node": m.node_id,
                                    "creation": f"provision-{m.instance_name}-compute",
                                    "configuration": "d" * 64,
                                }
                                for m in state.members
                            },
                        },
                        "manifests": manifests,
                    }
                ),
            }
        )
        state = replace(
            state,
            transaction=replace(
                tx,
                resource_bindings=tuple(sorted(raw.items())),
                completed_effects=tuple(
                    dict.fromkeys(
                        (
                            *tx.completed_effects,
                            *(f"provision-{m.instance_name}-compute" for m in state.members),
                            "select-created-package-manifests",
                        )
                    )
                ),
            ),
        )
    elif getattr(request, "param", False):
        from test_vm_ha_replacement_history_recovery import with_replacement_history

        state = with_replacement_history(state, (1, 2, 10))
    store = VMHALifecycleStore(tmp_path / "gateway.yaml")
    store.write_verified(state)
    guests = {
        member.node_id: {
            "children": children[member.node_id],
            "boot_id": "current-boot",
            "adoption_sha256": None,
            "package_worker": None,
            "intent": None,
            "settlement": None,
        }
        for member in state.members
    }
    options = dict(
        desired=desired,
        artifact="c" * 64,
        cloud={
            "exact": "cloud",
            "members": [{"present": True, "state": "running"} for _ in state.members],
        },
        evidence=evidence,
        credential_plan=SimpleNamespace(
            credentials=SimpleNamespace(resource_bindings=lambda: {}),
            action="reuse",
            observation_record=lambda: {"iam_resources": {}},
        ),
        ssh_policy=SimpleNamespace(
            managed_action=None, assert_current=lambda: None, known_hosts_sha256="e" * 64
        ),
        inspect_guest=lambda node, _: deepcopy(guests[node]),
    )
    return SimpleNamespace(
        journal=journal,
        store=store,
        state=state,
        options=options,
        inspect=lambda: ha_replan.inspect(journal, store, state, **options)[0],
        guests=guests,
        old_desired=old_desired,
    )


@pytest.mark.parametrize("local_settlement", [False, "initial"], indirect=True)
def test_local_settlement_archives_all_evidence_without_completing_old_steps(local_settlement):
    ctx = local_settlement
    before = ctx.journal.path.read_bytes(), ctx.store.path.read_bytes()
    settlement = ctx.inspect()
    assert not settlement.path.exists()
    seen = []

    def settle(node, request):
        assert (
            ctx.store.path.parent
            / (ctx.store.path.name + ".history")
            / (ctx.state.record_sha256 + ".json")
        ).exists()
        seen.append(node)
        return {"settlement": {"state": "complete"}, "request": request}

    settlement.execute(
        refresh=ctx.inspect, settle_guest=settle, verify_stopped=lambda: seen.append("verified")
    )
    saved = settlement.read()
    assert saved["phase"] == "complete"
    assert seen[-1] == "verified"
    assert (ctx.journal.path.read_bytes(), ctx.store.path.read_bytes()) == before
    assert ctx.journal.pending()["progress"]["runtime"] == "started"
    assert ha_replan.validate_record(saved, previous=ctx.state) == settlement.proof


@pytest.mark.parametrize("local_settlement", [False, "initial"], indirect=True)
def test_local_settlement_resumes_first_receipt_without_replacing_approval(local_settlement):
    ctx = local_settlement
    settlement = ctx.inspect()
    calls = []
    failed = False

    def settle(node, request):
        nonlocal failed
        calls.append(node)
        if node == "node-1" and not failed:
            failed = True
            raise RuntimeError("lost reply")
        return {"settlement": {"state": "complete"}, "request": request}

    with pytest.raises(RuntimeError, match="lost reply"):
        settlement.execute(refresh=ctx.inspect, settle_guest=settle, verify_stopped=lambda: None)
    original = settlement.read()
    assert original["phase"] == "settling" and set(original["receipts"]) == {"node-0"}
    ctx.inspect().execute(refresh=ctx.inspect, settle_guest=settle, verify_stopped=lambda: None)
    assert settlement.read()["proof"] == original["proof"]
    assert settlement.read()["phase"] == "complete"


@pytest.mark.parametrize("fault", ["package", "cloud-request", "identity", "source", "local-child"])
@pytest.mark.parametrize("local_settlement", [False, "initial"], indirect=True)
def test_unsettled_or_changed_ha_refuses_without_archival(local_settlement, fault):
    ctx = local_settlement
    before = ctx.journal.path.read_bytes(), ctx.store.path.read_bytes()
    if fault == "package":
        ctx.guests["node-0"]["children"]["ordinary"]["state"] = "unresolved"
    elif fault == "cloud-request":
        ctx.state = replace(
            ctx.state,
            transaction=replace(
                ctx.state.transaction,
                accepted_cloud_operation_id="accepted",
                accepted_cloud_operation_effect=ctx.state.transaction.pending_effect,
            ),
        )
    elif fault == "identity":
        ctx.options["desired"]["fixed"] = "other"
    elif fault == "source":
        ctx.options["evidence"]["node-0"]["source_sha256"]["cli.py"] = "f" * 64
    else:
        from nebius_vpngw.deploy.security_group_apply import AttachmentJournal

        owner = AttachmentJournal(ctx.journal.project, ctx.journal.gateway)
        # A live native owner lock is checked even when no child record exists.
        with owner:
            settlement = ctx.inspect()
            with pytest.raises(RuntimeError, match="local writer"):
                settlement.execute(
                    refresh=ctx.inspect,
                    settle_guest=lambda *_: pytest.fail("guest mutated"),
                    verify_stopped=lambda: None,
                )
        return
    with pytest.raises(ValueError):
        ha_replan.inspect(ctx.journal, ctx.store, ctx.state, **ctx.options)
    assert (ctx.journal.path.read_bytes(), ctx.store.path.read_bytes()) == before
    assert not (ctx.journal.root / "ha-replanning").exists()


def test_guest_successor_changes_generation_only_after_exact_settlement(reservation_guest):
    guest, original = reservation_guest
    request = settlement_request(guest, original)
    settled = guest.settle_replan(request)
    before = guest.corrective_children()
    successor = {
        **original,
        "settlement": {
            "operation": request["operation"],
            "receipt": digest(settled),
            "generation": "9" * 64,
        },
    }
    result = guest.reserve_corrective(successor)
    assert result["phase"] == "quiescent"
    assert guest.repair.read_json(guest.repair.STATE / "apply.lock") == {
        **original["previous_lock"],
        "generation_id": "9" * 64,
        "operation_id": original["operation"],
    }
    archived = guest.repair.read_json(
        guest.repair.STATE / "corrective" / (original["operation"] + "-runtime.json")
    )
    assert digest(archived) == before["runtime"]["digest"]
    assert guest.reserve_corrective(successor) == result


@pytest.mark.parametrize("field", ["generation_id", "operation_id", "cluster_id", "node_id"])
def test_reserved_successor_rejects_foreign_lock_before_runtime_changes(
    reservation_guest, monkeypatch, field
):
    guest, original = reservation_guest
    request = settlement_request(guest, original)
    settled = guest.settle_replan(request)
    successor = {
        **original,
        "settlement": {
            "operation": request["operation"],
            "receipt": digest(settled),
            "generation": "9" * 64,
        },
    }
    publish = guest.repair.publish

    def interrupt_after_reservation(path, raw, mode=0o600):
        publish(path, raw, mode)
        if path.name == original["operation"] + ".json":
            raise InterruptedError("observer lost after reservation")

    monkeypatch.setattr(guest.repair, "publish", interrupt_after_reservation)
    with pytest.raises(InterruptedError):
        guest.reserve_corrective(successor)
    monkeypatch.setattr(guest.repair, "publish", publish)
    lock = guest.repair.STATE / "apply.lock"
    foreign = {**original["previous_lock"], field: "e" * 64}
    publish(lock, json.dumps(foreign).encode())
    before = guest.corrective_children(), lock.read_bytes()
    with pytest.raises(RuntimeError, match="another operation"):
        guest.reserve_corrective(successor)
    assert (guest.corrective_children(), lock.read_bytes()) == before


@pytest.mark.parametrize("bad", ["receipt", "operation", "generation"])
def test_guest_successor_refuses_foreign_settlement_without_rewriting_lock(reservation_guest, bad):
    guest, original = reservation_guest
    request = settlement_request(guest, original)
    settled = guest.settle_replan(request)
    successor = {
        **original,
        "settlement": {
            "operation": request["operation"],
            "receipt": digest(settled),
            "generation": "9" * 64,
        },
    }
    successor["settlement"][bad] = "../../foreign" if bad != "receipt" else "f" * 64
    before = (guest.repair.STATE / "apply.lock").read_bytes(), guest.corrective_children()
    with pytest.raises((ValueError, RuntimeError)):
        guest.reserve_corrective(successor)
    assert ((guest.repair.STATE / "apply.lock").read_bytes(), guest.corrective_children()) == before
    assert not (guest.repair.STATE / "corrective").exists()


@pytest.mark.parametrize("same_artifact", [False, True])
def test_settled_ha_successor_uses_new_intent_and_keeps_old_effects_as_history(
    local_settlement, same_artifact
):
    from test_vm_ha_corrective import record

    from nebius_vpngw.deploy import vm_ha_corrective

    ctx = local_settlement
    if same_artifact:
        ctx.options["artifact"] = dict(ctx.state.transaction.resource_bindings)[
            "heartbeat-upgrade-artifact"
        ]
    proposal = ctx.inspect()
    proposal.execute(
        refresh=ctx.inspect,
        settle_guest=lambda node, request: {
            "settlement": {"state": "complete"},
            "request": request,
        },
        verify_stopped=lambda: None,
    )
    settled = proposal.read()
    value = record(ctx.state)
    value.update(
        replan=settled,
        operation=ha_replan.successor_operation(ctx.state, settled),
        artifact=ctx.options["artifact"],
        hosts=proposal.proof["hosts"],
        cloud=proposal.proof["cloud"],
        ssh_trust=proposal.proof["ssh_trust"],
        locks={node: request["previous_lock"] for node, request in proposal.requests.items()},
    )
    new = vm_ha_corrective.successor(ctx.state, value, bindings={})
    assert new.transaction.desired_state_digest == digest(ctx.options["desired"])
    assert new.transaction.operation_id != ctx.state.transaction.operation_id
    assert new.transaction.completed_effects == ()
    assert new.has_same_identity(ctx.state)
    ctx.store.write_verified(new, predecessor_sha256=ctx.state.record_sha256)
    history = (
        ctx.store.path.parent
        / (ctx.store.path.name + ".history")
        / (ctx.state.record_sha256 + ".json")
    )
    assert json.loads(history.read_bytes()) == ctx.state.to_dict()
    wrong = deepcopy(value)
    wrong["replan"]["phase"] = "settling"
    with pytest.raises(ValueError, match="completed settlement"):
        vm_ha_corrective.successor(ctx.state, wrong, bindings={})


@pytest.mark.parametrize("local_settlement", [False, True], indirect=True)
@pytest.mark.parametrize("interrupted_write", [1, 4, 9])
def test_cli_changed_generation_resumes_exact_successor_reservations(
    local_settlement, monkeypatch, interrupted_write
):
    ctx = local_settlement
    settlement = ctx.inspect()
    settlement.execute(
        refresh=ctx.inspect,
        settle_guest=lambda node, request: {
            "settlement": {"state": "complete"},
            "request": request,
        },
        verify_stopped=lambda: None,
    )
    completed = settlement.read()
    _execute_settled_successor(ctx, monkeypatch, completed, interrupted_write)


def _execute_settled_successor(ctx, monkeypatch, completed, interrupted_write):
    from nebius_vpngw import cli
    from nebius_vpngw.deploy import ordinary_handoff, vm_ha_corrective
    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleJournal

    desired = ctx.options["desired"]
    evidence = ctx.options["evidence"]
    instances = tuple(
        SimpleNamespace(
            hostname=member.instance_name,
            vm_ha_node=SimpleNamespace(node_id=member.node_id),
            vm_ha_generation=SimpleNamespace(
                generation_id=desired["generation"]["generation_id"],
                digests=SimpleNamespace(
                    **{
                        key: desired["generation"][key + "_digest"]
                        for key in ("configuration", "static_routes", "bgp_policy")
                    }
                ),
            ),
        )
        for member in reversed(ctx.state.members)
    )
    artifact = SimpleNamespace(
        path=Path("fixture-release.whl"),
        sha256=ctx.options["artifact"],
        dependency_plans=tuple(
            (
                instance.hostname,
                SimpleNamespace(
                    manifest={"wheel_sha256": ctx.options["artifact"]},
                    envelope={"desired": {"wheel_sha256": ctx.options["artifact"]}},
                ),
            )
            for instance in instances
        ),
    )
    approved = SimpleNamespace(artifact=artifact, digest="b" * 64)
    targets = {instance.hostname: "fixture" for instance in instances}
    monkeypatch.setattr(cli, "_vm_ha_desired_approval_state", lambda _: desired)
    monkeypatch.setattr(cli, "_vm_ha_observation_matches_bindings", lambda *_a, **_k: True)
    monkeypatch.setattr(cli, "validate_retained_route_scope", lambda *_a: None)
    monkeypatch.setattr(cli, "_vm_ha_apply_order_for_owner", lambda *_a: instances)
    monkeypatch.setattr(cli, "SSHPush", lambda **_k: object())
    monkeypatch.setattr(
        cli, "_upgrade_recovery_host_evidence", lambda *_a, **_k: deepcopy(evidence)
    )
    reservations = {}

    def remote(ssh, target, instance, local, request):
        node = instance.vm_ha_node.node_id
        if request["action"] == "corrective-inspect":
            return {
                "children": completed["requests"][node]["children"],
                "reservation": reservations.get(node),
            }
        assert request["action"] == "corrective-reserve"
        assert request["settlement"]["receipt"] == completed["receipts"][node]
        assert request["settlement"]["generation"] == desired["generation"]["generation_id"]
        reservations[node] = {
            "phase": "quiescent",
            "key": {key: request[key] for key in ("operation", "previous_lock", "children")},
        }
        evidence[node]["apply_lock"] = {
            **request["previous_lock"],
            "operation_id": request["operation"],
            "generation_id": request["settlement"]["generation"],
        }
        return reservations[node]

    monkeypatch.setattr(ordinary_handoff, "corrective_action", remote)
    original_write = ctx.store.write_verified
    writes = 0

    def write(state, **kwargs):
        nonlocal writes
        original_write(state, **kwargs)
        writes += 1
        if writes == interrupted_write:
            raise RuntimeError("lost successor acknowledgement")

    monkeypatch.setattr(ctx.store, "write_verified", write)
    state = ctx.state
    for _attempt in range(2):
        context = cli._plan_vm_ha_corrective(
            state=state,
            artifact=artifact,
            plan=object(),
            observation=ctx.options["cloud"],
            credentials=ctx.options["credential_plan"].credentials,
            local={},
            targets=targets,
            ssh_policy=ctx.options["ssh_policy"],
            settlement=completed if state == ctx.state else None,
        )
        if context is None:
            break
        try:
            state = cli._execute_vm_ha_corrective(
                context,
                approved=approved,
                journal=VMHALifecycleJournal(ctx.store, state),
                plan=object(),
                local={},
                targets=targets,
                credentials=ctx.options["credential_plan"].credentials,
                ssh_policy=ctx.options["ssh_policy"],
                observe=lambda: ctx.options["cloud"],
                command_scope={},
            )
        except RuntimeError as error:
            assert str(error) == "lost successor acknowledgement"
            state = ctx.store.read(
                expected_project_id=ctx.state.project_id,
                expected_gateway_name=ctx.state.gateway_name,
            )
    assert vm_ha_corrective.OBSERVED_EFFECT in state.transaction.completed_effects
    assert state.transaction.desired_state_digest == digest(desired)
    assert state.has_same_identity(ctx.state)
    assert set(reservations) == {member.node_id for member in state.members}
    from nebius_vpngw.deploy.vm_ha_lifecycle import vm_ha_replacement_ownership_bindings

    assert vm_ha_replacement_ownership_bindings(state) == vm_ha_replacement_ownership_bindings(
        ctx.state
    )


def test_unsettled_checkpoint_has_typed_repair_guidance_without_effects(local_settlement):
    from nebius_vpngw.deploy.recovery import Invocation, ReplanRejected, advice

    ctx = local_settlement
    before = ctx.store.path.read_bytes(), ctx.journal.path.read_bytes()
    state = replace(
        ctx.state,
        transaction=replace(
            ctx.state.transaction,
            pending_effect="create-compute",
            accepted_cloud_operation_effect="create-compute",
            accepted_cloud_operation_id="pending-provider-operation",
        ),
    )
    with pytest.raises(ReplanRejected) as caught:
        ha_replan.inspect(ctx.journal, ctx.store, state, **ctx.options)
    diagnostic = advice(Invocation("apply", ctx.store.path, dry_run=True), caught.value)
    assert diagnostic.reason_code == "predecessor-not-settled"
    assert "--repair" in diagnostic.repair_command
    assert (ctx.store.path.read_bytes(), ctx.journal.path.read_bytes()) == before


@pytest.mark.parametrize("decision", ["dry-run", "decline", "settle", "newer"])
@pytest.mark.parametrize("local_settlement", [False, "initial"], indirect=True)
def test_real_apply_command_separates_settlement_and_successor_approval(
    local_settlement, monkeypatch, tmp_path, decision
):
    from dataclasses import dataclass, field

    from typer.testing import CliRunner

    from nebius_vpngw import cli
    from nebius_vpngw.deploy import (
        apply_command,
        ha_command,
        ordinary_handoff,
        security_group_apply,
    )
    from nebius_vpngw.deploy.recovery import ApprovalDeclined, error_chain

    ctx = local_settlement
    state = ctx.state
    path = tmp_path / "gateway.yaml"
    path.write_text("version: 1\n")
    local = {
        "project_id": state.project_id,
        "region_id": "region",
        "gateway_group": {"name": state.gateway_name, "vm_spec": {}},
    }
    generation = SimpleNamespace(
        generation_id=ctx.options["desired"]["generation"]["generation_id"],
        digests=SimpleNamespace(
            **{
                key: ctx.options["desired"]["generation"][key + "_digest"]
                for key in ("configuration", "static_routes", "bgp_policy")
            }
        ),
    )
    members = tuple(
        SimpleNamespace(
            instance_index=m.instance_index, node_id=m.node_id, role=SimpleNamespace(value=m.role)
        )
        for m in state.members
    )
    instances = tuple(
        SimpleNamespace(
            hostname=m.instance_name,
            external_ip=m.public_ip,
            vm_ha_node=node,
            vm_ha_generation=generation,
        )
        for m, node in zip(state.members, members, strict=True)
    )
    ha = SimpleNamespace(cluster_id=state.cluster_id, members=members, generation=generation)
    plan = SimpleNamespace(
        vm_ha=ha,
        gateway_group=SimpleNamespace(
            name=state.gateway_name, region="region", vm_ha=ha, security_group_ids=()
        ),
        gateway={},
        validate=lambda: None,
        iter_instance_configs=lambda: iter(instances),
    )

    @dataclass
    class Credentials:
        credentials: object = field(
            default_factory=lambda: ctx.options["credential_plan"].credentials
        )
        command_intent: object = None
        action: str = "reuse"
        command_action: str = "reuse"

        def approval_record(self):
            return {"action": "reuse"}

    class Manager:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def discover_vm_ha_members(self, _):
            return {m.instance_name: m.public_ip for m in state.members}

        def observe_vm_ha_migration_state(self, *_):
            return ctx.options["cloud"]

        def verify_vm_ha_existing_identities(self, *args, **kwargs):
            pass

        def check_changes(self, _):
            return []

    artifact = SimpleNamespace(
        path=Path("fixture-release.whl"),
        sha256=ctx.options["artifact"],
        dependency_plans=(),
        capabilities=(),
    )
    monkeypatch.setattr(cli, "load_local_config", lambda _: local)
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr(cli, "VMHALifecycleStore", lambda *_: ctx.store)
    monkeypatch.setattr(apply_command, "CommandJournal", lambda *_a, **_k: ctx.journal)
    monkeypatch.setattr(cli, "inspect_managed_vm_ha_credentials", lambda **_: Credentials())
    monkeypatch.setattr(cli, "_validate_vm_ha_lifecycle_credential_transition", lambda *_: None)
    monkeypatch.setattr(cli, "_apply_operator_auth_token", lambda: None)
    monkeypatch.setattr(cli, "VMManager", Manager)
    monkeypatch.setattr(
        cli, "require_vm_ha_ssh_policy", lambda *_a, **_k: ctx.options["ssh_policy"]
    )
    monkeypatch.setattr(cli, "_vm_ha_missing_standby_replacement_plan", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "validate_retained_route_scope", lambda *_: None)
    monkeypatch.setattr(
        security_group_apply, "inspect_plan", lambda *_: {"desired_ids": [], "members": {}}
    )
    monkeypatch.setattr(cli, "_vm_ha_desired_approval_state", lambda _: ctx.options["desired"])
    from nebius_vpngw.deploy import release_policy

    def releases(*_a, **_k):
        if decision == "newer":
            raise release_policy.ReleaseBlocked("newer")

    monkeypatch.setattr(release_policy, "require_targets", releases)
    monkeypatch.setattr(cli, "_resolve_vm_ha_agent_artifact", lambda _: artifact)
    monkeypatch.setattr(cli, "_plan_vm_ha_package_dependencies", lambda artifact, **_: artifact)
    monkeypatch.setattr(cli, "_vm_ha_observation_matches_bindings", lambda *_a, **_k: True)
    monkeypatch.setattr(cli, "_vm_ha_provisioning_members", lambda *_: state.members)
    monkeypatch.setattr(cli, "_vm_ha_apply_order_for_owner", lambda *_: instances)
    monkeypatch.setattr(cli, "SSHPush", lambda **_: object())
    monkeypatch.setattr(
        cli, "_upgrade_recovery_host_evidence", lambda *_a, **_k: deepcopy(ctx.options["evidence"])
    )
    monkeypatch.setattr(
        cli,
        "_inspect_changed_ha_settlement",
        lambda *_a, **_k: (
            ctx.inspect(),
            ha_replan.predecessor_generations(state, ctx.options["evidence"]),
        ),
    )
    # The component suites validate cloud/SSH/credential proof separately. This
    # regression runs the real command, settlement journal, successor planner,
    # and approval boundary with explicit read-only transport observations.
    actions = []

    def remote(_ssh, _target, instance, _local, request):
        node = instance.vm_ha_node.node_id
        actions.append(request["action"])
        if request["action"] == "corrective-inspect":
            return {"children": ctx.guests[node]["children"], "reservation": None}
        assert request["action"] == "replan-settle"
        return {"settlement": {"state": "complete"}, "request": request}

    monkeypatch.setattr(ordinary_handoff, "corrective_action", remote)
    approvals = []

    def approve(value, *, supplied, dry_run):
        approvals.append((value, supplied, dry_run))
        if dry_run:
            return False
        if decision == "decline" or len(approvals) == 2:
            raise ApprovalDeclined()
        return True

    monkeypatch.setattr(cli, "_approve_disruption", approve)
    failures = []
    original_failed = cli.ApplyReport.failed

    def failed(report, error):
        failures.extend((type(item).__name__, str(item)) for item in error_chain(error))
        return original_failed(report, error)

    monkeypatch.setattr(cli.ApplyReport, "failed", failed)
    successors = []
    original_create = ha_command.create

    def create(*args, **kwargs):
        candidate = original_create(*args, **kwargs)
        successors.append(candidate)
        return candidate

    monkeypatch.setattr(ha_command, "create", create)
    before = ctx.journal.path.read_bytes(), ctx.store.path.read_bytes()
    args = ["apply", "--local-config-file", str(path)]
    if decision == "dry-run":
        args.append("--dry-run")
    result = CliRunner().invoke(cli.app, args)
    if decision == "newer":
        assert result.exit_code == 1
        assert "upgrade the CLI" in result.output
        assert not approvals and not actions and not successors
        assert (ctx.journal.path.read_bytes(), ctx.store.path.read_bytes()) == before
        return
    assert result.exit_code == 0, failures
    assert (ctx.journal.path.read_bytes(), ctx.store.path.read_bytes()) == before
    if decision == "settle":
        assert len(approvals) == 2
        assert approvals[0][0] != approvals[1][0]
        assert approvals[1][1] is None
        assert len(successors) == 1
        assert successors[0].intent["artifact_key"] == "heartbeat-upgrade-artifact"
        assert ctx.inspect().read()["phase"] == "complete"
        assert actions.count("replan-settle") == 2
        assert set(actions) <= {"replan-settle", "corrective-inspect"}
    else:
        assert len(approvals) == 1
        assert not actions
        assert not ctx.inspect().path.exists()
        assert not (ctx.journal.root / "history").exists()


@pytest.mark.parametrize("member_artifacts", [("a", "a"), ("b", "a"), ("b", "b")])
def test_unfinished_recovery_selects_each_original_package_without_installing(member_artifacts):
    from nebius_vpngw.deploy import vm_ha_upgrade_recovery as recovery

    state, evidence = predecessor()
    original = json.loads(dict(state.transaction.resource_bindings)["runtime-package-manifests"])
    for manifest in original.values():
        manifest["wheel_sha256"] = "a" * 64
    replacement = {
        **next(iter(original.values())),
        "wheel_sha256": "b" * 64,
        "files": {"nebius_vpngw/cli.py": "f" * 64},
        "expected_dependencies": {"nebius-vpngw": "0.6.6"},
        "expected_requirements": {"nebius-vpngw": []},
        "dependency_wheels": {},
        "runtime_plan": {"packages": []},
    }
    packages = {
        m.instance_name: {
            "closure": digest(
                {k: replacement[k] for k in ("expected_dependencies", "expected_requirements")}
            ),
            "wheels": {},
            "runtime": digest(replacement["runtime_plan"]),
        }
        for m in state.members
    }
    resources = dict(state.transaction.resource_bindings)
    resources[recovery.ARTIFACT] = "a" * 64
    resources[recovery.AUTHORITY] = json.dumps(
        {
            "approval": "c" * 64,
            "prior_record": "d" * 64,
            "prior_artifact": "a" * 64,
            "artifact": "b" * 64,
            "ssh_trust": "e" * 64,
            "hosts": "f" * 64,
            "owner": state.members[0].node_id,
            "superseded_effect": "prepare-heartbeat-upgrade",
            "packages": json.dumps(packages),
        }
    )
    state = replace(
        state,
        transaction=replace(
            state.transaction,
            pending_effect=None,
            resource_bindings=tuple(sorted(resources.items())),
        ),
    )
    selected = {}
    for member, letter in zip(state.members, member_artifacts, strict=True):
        children = {"runtime": {"artifact": letter * 64}}
        reservation = {"request": {"package": {"desired": replacement}}} if letter == "b" else None
        selected[member.instance_name] = ha_replan.recovery_member_manifest(
            state, member.instance_name, children, reservation, original
        )
        if letter == "b":
            evidence[member.node_id]["source_sha256"] = {"cli.py": "f" * 64}
    assert len(ha_replan.predecessor_generations(state, evidence, selected_manifests=selected)) == 2
    with pytest.raises(ValueError, match="unproven"):
        ha_replan.recovery_member_manifest(
            state,
            state.members[0].instance_name,
            {"runtime": {"artifact": "9" * 64}},
            None,
            original,
        )
    changed = deepcopy(replacement)
    changed["runtime_plan"] = {"packages": ["foreign"]}
    with pytest.raises(ValueError, match="closure"):
        ha_replan.recovery_member_manifest(
            state,
            state.members[0].instance_name,
            {"runtime": {"artifact": "b" * 64}},
            {"request": {"package": {"desired": changed}}},
            original,
        )


@pytest.mark.parametrize(
    ("member_artifacts", "lost_reply"),
    [(("a", "a"), False), (("b", "a"), False), (("b", "b"), False), (("b", "a"), True)],
)
def test_pre_ready_recovery_settlement_admits_fresh_artifact_successor(
    local_settlement, monkeypatch, member_artifacts, lost_reply
):
    from nebius_vpngw.apply_approval import manifest_identity
    from nebius_vpngw.deploy import ha_command, package_reservation
    from nebius_vpngw.deploy import vm_ha_upgrade_recovery as recovery
    from nebius_vpngw.deploy.apply_command import CommandJournal

    ctx = local_settlement
    ctx.options["desired"] = ctx.old_desired
    ctx.inspect = lambda: ha_replan.inspect(ctx.journal, ctx.store, ctx.state, **ctx.options)[0]
    original = ctx.journal.snapshot()
    original_bytes = ctx.journal.path.read_bytes()
    targets = tuple(sorted(member.compute_id for member in ctx.state.members))
    action_scope = original["bindings"]["runtime"]["action_scope"]
    b_command = ha_command.create(
        ctx.journal,
        scope="1" * 64,
        artifact="b" * 64,
        action_scope=action_scope,
        targets=targets,
        approval="7" * 64,
        runtime_scope="5" * 64,
        report={"kind": "upgrade-artifact-recovery"},
        attachment_plan=None,
        successor=ha_command.reviewed_successor(
            original,
            lifecycle=ctx.state,
            kind="upgrade-artifact-recovery",
            old_scope="1" * 64,
            action_scope=action_scope,
            targets=targets,
            attachment_child=None,
        ),
    )
    with ctx.journal:
        b_command.begin()
        b_command.start_runtime()
    old_state = ctx.state
    original_manifests = json.loads(
        dict(old_state.transaction.resource_bindings)["runtime-package-manifests"]
    )
    replacement = {
        **next(iter(original_manifests.values())),
        "wheel_sha256": "b" * 64,
        "files": {"nebius_vpngw/cli.py": "f" * 64},
        "expected_dependencies": {"nebius-vpngw": "0.6.5"},
        "expected_requirements": {"nebius-vpngw": []},
        "dependency_wheels": {},
        "runtime_plan": {"packages": []},
    }
    artifact = SimpleNamespace(
        dependency_plans=tuple(
            (m.instance_name, SimpleNamespace(manifest=replacement)) for m in old_state.members
        )
    )
    ctx.state = recovery.authorize(
        old_state,
        approval=b_command.approval,
        artifact="b" * 64,
        ssh_trust=ctx.options["ssh_policy"].known_hosts_sha256,
        hosts=recovery.host_digest(ctx.options["evidence"]),
        owner="node-0",
        packages=recovery.package_authority(artifact),
    )
    ctx.store.write_verified(ctx.state, predecessor_sha256=old_state.record_sha256)
    b_snapshot = ctx.journal.snapshot()
    b_bytes = ctx.journal.path.read_bytes()
    owner = (ctx.journal.root, b_command.execution_id, digest(b_command.intent))
    token = package_reservation.SCOPE.set(owner)
    pins = {}
    selected = {}
    try:
        for member, letter in zip(ctx.state.members, member_artifacts, strict=True):
            node, name = member.node_id, member.instance_name
            ctx.guests[node]["children"]["runtime"]["artifact"] = letter * 64
            selected[node] = replacement if letter == "b" else original_manifests[name]
            if letter == "b":
                ctx.options["evidence"][node]["source_sha256"] = {"cli.py": "f" * 64}
                package_reservation.admit(
                    {
                        "action": "prepare-guarded",
                        "package": {"host": name, "desired": replacement},
                    },
                    "6" * 64,
                )
                path = package_reservation.path_for(owner[0], owner[1], name)
                pins[path] = path.read_bytes()
    finally:
        package_reservation.SCOPE.reset(token)
    assert recovery.READY not in dict(ctx.state.transaction.resource_bindings)
    assert dict(ctx.state.transaction.resource_bindings)[recovery.ARTIFACT] == "a" * 64
    settlement = ctx.inspect()
    assert settlement.proof["recovery_frontier"] == {
        "lifecycle_artifact": "a" * 64,
        "command_artifact": "b" * 64,
        "manifests": {node: manifest_identity(manifest) for node, manifest in selected.items()},
    }
    failed = False
    requests = []

    def settle(node, request):
        nonlocal failed
        assert request["action"] == "replan-settle"  # Settlement must never install B.
        requests.append((node, deepcopy(request)))
        if lost_reply and node == "node-1" and not failed:
            failed = True
            raise RuntimeError("lost settlement reply")
        return {"settlement": {"state": "complete"}, "request": request}

    if lost_reply:
        with pytest.raises(RuntimeError, match="lost settlement reply"):
            settlement.execute(
                refresh=ctx.inspect, settle_guest=settle, verify_stopped=lambda: None
            )
        assert set(settlement.read()["receipts"]) == {"node-0"}
        ctx.journal = CommandJournal(ctx.state.project_id, ctx.state.gateway_name)
        resumed = ctx.inspect()
        assert resumed.proof == settlement.proof and resumed.path == settlement.path
        settlement = resumed
    settlement.execute(refresh=ctx.inspect, settle_guest=settle, verify_stopped=lambda: None)
    completed = settlement.read()
    assert completed["phase"] == "complete"
    assert all(request["children"] == ctx.guests[node]["children"] for node, request in requests)
    next_command = ha_command.create(
        ctx.journal,
        scope="1" * 64,
        artifact="c" * 64,
        action_scope=action_scope,
        targets=targets,
        approval="b" * 64,
        runtime_scope="8" * 64,
        report={"kind": "corrective-release"},
        attachment_plan=None,
        successor=ha_command.reviewed_successor(
            b_snapshot,
            lifecycle=ctx.state,
            kind="corrective-release",
            old_scope="1" * 64,
            action_scope=action_scope,
            targets=targets,
            attachment_child=None,
            settlement=completed,
        ),
    )
    with ctx.journal:
        next_command.begin()
        next_command.start_runtime()
    assert next_command.execution_id not in {
        original["transaction"]["execution_id"],
        b_snapshot["transaction"]["execution_id"],
    }
    _execute_settled_successor(ctx, monkeypatch, completed, interrupted_write=4)
    from nebius_vpngw.deploy import vm_ha_corrective

    current = ctx.store.read(
        expected_project_id=ctx.state.project_id, expected_gateway_name=ctx.state.gateway_name
    )
    tampered = deepcopy(vm_ha_corrective.authority(current))
    tampered["replan"]["proof"]["recovery_frontier"]["manifests"]["node-0"] = "0" * 64
    tampered["operation"] = ha_replan.successor_operation(ctx.state, tampered["replan"])
    with pytest.raises(ValueError, match="predecessor package manifest differs"):
        vm_ha_corrective.successor(ctx.state, tampered, bindings={})
    for snapshot, raw in ((original, original_bytes), (b_snapshot, b_bytes)):
        assert (ctx.journal.root / "history" / (digest(snapshot) + ".json")).read_bytes() == raw
    assert all(path.read_bytes() == raw for path, raw in pins.items())
