"""Retire failed reprovision admission without treating planned nodes as Compute IDs."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_destroy import _Backend, _lifecycle, _plan

from nebius_vpngw.deploy import destroy as destroy_module
from nebius_vpngw.deploy import ha_members, package_reservation
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
from nebius_vpngw.deploy.replacement import digest
from nebius_vpngw.deploy.vm_ha_lifecycle import (
    VMHALifecycleJournal,
    VMHALifecycleStatus,
    VMHALifecycleStore,
)


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "checkpoint",
        "name",
        "missing-member",
        "extra-member",
        "node",
        "index",
        "role",
        "compute",
        "disk",
        "kind",
        "targets",
        "extra-target",
        "package-token",
        "attachment-token",
        "absence",
        "unavailable-absence",
        "changing-absence",
    ],
)
def test_terminal_destroy_retires_only_exact_uncreated_admission(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plan = replace(_plan(topology="vm-ha"), expected_compute_names=("gateway-0", "gateway-1"))
    original = _lifecycle(VMHALifecycleStatus.ACTIVE, plan)
    destruction = original.start_destruction(
        original,
        operation_id="destroy-operation",
        approval_digest=plan.digest,
        desired_state_digest="e" * 64,
        current_state_digest="f" * 64,
        destroy_plan_json=plan.to_json(),
        current_observation={},
    )
    store = VMHALifecycleStore(tmp_path / "config.yaml")
    store.write_verified(original)
    store.write_verified(destruction, predecessor_sha256=original.record_sha256)
    journal = destroy_module.VMHADestroyJournal(VMHALifecycleJournal(store, destruction))
    backend = _Backend(plan)
    destroy_module.DestroyCoordinator(backend, journal).run()
    terminal = journal.journal.state
    command = CommandJournal(plan.project_id, plan.gateway_name)
    members = ha_members.intent(
        tuple(replace(m, compute_id="", disk_id="") for m in terminal.members),
        created={m.instance_name for m in terminal.members},
    )
    intent = {
        "members": members,
        "packages": {"checkpoint": terminal.record_sha256},
        "attachments": None,
    }
    if fault == "checkpoint":
        intent["packages"]["checkpoint"] = "1" * 64
    elif fault == "name":
        members["foreign"] = members.pop("gateway-0")
    elif fault == "missing-member":
        members.pop("gateway-1")
    elif fault == "extra-member":
        members["foreign"] = {**members["gateway-0"], "node_id": "foreign-node"}
    elif fault in {"node", "index", "role", "compute", "disk", "kind"}:
        key, value = {
            "node": ("node_id", "foreign-node"),
            "index": ("instance_index", 8),
            "role": ("role", "passive"),
            "compute": ("compute_id", "foreign-compute"),
            "disk": ("disk_id", "foreign-disk"),
            "kind": ("kind", "unknown"),
        }[fault]
        members["gateway-0"][key] = value
    targets = ha_members.targets(members)
    if fault == "targets":
        targets = ("create:foreign",)
    if fault == "extra-target":
        targets = (*targets, "create:foreign")
    if fault == "attachment-token":
        intent["attachments"] = {"members": {"gateway-0": {"compute_id": targets[0]}}}
    pending = CommandPlan(
        plan.project_id,
        plan.gateway_name,
        "a" * 64,
        "b" * 64,
        (Effect("runtime", "ha-lifecycle", "apply", targets, digest(intent), "c" * 64, True),),
    )
    with command:
        command.begin(pending, intents={"runtime": intent})
        command.started("runtime")
    before = command.path.read_bytes()
    execution = command.pending()["execution_id"]
    if fault == "package-token":
        token = package_reservation.SCOPE.set((command.root, execution, digest(intent)))
        try:
            package_reservation.admit(
                {"action": "reserve", "package": {"host": "gateway-0", "target": targets[0]}},
                "d" * 64,
            )
        finally:
            package_reservation.SCOPE.reset(token)
    backend.command_compute_absent = lambda _: pytest.fail("Creation token used as Compute ID")
    if fault == "absence":
        backend.compute.add("foreign-compute")
    elif fault == "unavailable-absence":

        def unavailable(_):
            raise RuntimeError("observation unavailable")

        backend.stable_verification = unavailable
    elif fault == "changing-absence":
        backend.stable_verification = lambda _: ("first", "changed")
    failure = command.root / "failures" / "retained.json"
    failure.parent.mkdir(mode=0o700)
    failure.write_text("original failure retained")
    if fault is not None:
        with pytest.raises(destroy_module.DestroyFailure):
            destroy_module._execute_ha_destroy(backend, journal)
        assert command.path.read_bytes() == before
        assert not (command.root / "retirements").exists()
    else:
        result = destroy_module._execute_ha_destroy(backend, journal)
        assert result.already_absent and command.pending() is None
        assert any(p.read_bytes() == before for p in (command.root / "history").glob("*.json"))
        receipt = json.loads((command.root / "retirements" / (execution + ".json")).read_text())
        assert receipt["targets"] == list(targets)
        assert journal.journal.state == terminal
        assert backend.verifications == 4
    assert failure.read_text() == "original failure retained"
