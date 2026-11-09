"""Migration authority follows creation receipts, never a route-name guess."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from tests.unit.test_ordinary_route_plan import world  # noqa: F401
from tests.unit.test_ordinary_route_runtime import bound
from tests.unit.test_vm_manager_allocations import (
    _ha_spec,
    _install_vm_ha_activating_journal,
    _route_targets,
)

from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import ordinary_route_migration as migration
from nebius_vpngw.deploy.ordinary_route_execute import execute
from nebius_vpngw.deploy.ordinary_route_ownership import labels
from nebius_vpngw.deploy.vm_ha_lifecycle import normalize_vm_ha_observation
from nebius_vpngw.deploy.vm_manager import VMManager


@pytest.mark.parametrize("runtime", (False, True))
def test_capture_uses_exact_current_writer_receipts(request, monkeypatch, runtime):
    _cloud, journal, _policy, plan, observe = request.getfixturevalue("world")
    execute(object(), journal, plan(), approval="d" * 64, observe=observe)
    expected = journal.snapshot()
    monkeypatch.setattr(migration, "RouteJournal", lambda *args: journal)
    admission = {"gateway-0": {"route_runtime": {"authority": None, "journal": None}}}
    if runtime:
        admission["gateway-0"]["route_runtime"] = {
            "authority": {"binding": bound()},
            "journal": expected,
        }
        monkeypatch.setattr(
            migration, "RouteJournal", lambda *args: pytest.fail("stale workstation read")
        )
    value = migration.capture("project", "gateway", admission)
    assert value["sources"] == {"gateway-0": expected}
    assert (
        migration.persisted({migration.KEY: json.dumps(value)}, "project", "gateway", "gateway-0")
        == expected["bindings"]
    )
    with pytest.raises((ValueError, RuntimeError), match="invalid|authority"):
        migration.capture("other-project", "gateway", admission)
    with pytest.raises(RuntimeError, match="identity"):
        migration.persisted({migration.KEY: json.dumps(value)}, "project", "gateway", "gateway-1")


def test_pending_route_effect_cannot_become_migration_authority(request, monkeypatch):
    _cloud, journal, _policy, plan, _observe = request.getfixturevalue("world")
    with journal:
        journal.begin(plan(), approval="d" * 64)
    monkeypatch.setattr(migration, "RouteJournal", lambda *args: journal)
    with pytest.raises(RuntimeError, match="unsettled"):
        migration.capture("project", "gateway", {"gateway-0": {}})


def test_existing_two_member_ordinary_conversion_preserves_both_source_receipts(
    request, monkeypatch
):
    _cloud, journal, _policy, plan, observe = request.getfixturevalue("world")
    execute(object(), journal, plan(), approval="d" * 64, observe=observe)
    monkeypatch.setattr(migration, "RouteJournal", lambda *args: journal)
    value = migration.capture("project", "gateway", {"gateway-0": {}, "gateway-1": {}})
    assert set(value["sources"]) == {"gateway-0", "gateway-1"}
    for node in value["sources"]:
        assert migration.records(value, "project", "gateway", node) == journal.inventory()


@pytest.mark.parametrize("mutation", (None, "spec_sha256", "labels", "missing-receipt"))
def test_ha_binding_only_adopts_exact_approved_hashed_route(tmp_path, mutation):
    manager = VMManager(project_id="project-1", region="eu-north1")
    journal, observation, _ = _install_vm_ha_activating_journal(manager, tmp_path)
    snapshot = {
        "id": "route-owned",
        "parent_id": "route-table-1",
        "name": "vpngw-0123456789abcdef0123456789abcdef",
        "destination": "10.20.0.0/16",
        "allocation_id": "primary-0",
        "labels": labels("project-1", "gateway", "route-table-1", "10.20.0.0/16", 0),
        "spec_sha256": "a" * 64,
    }
    receipt = {
        "snapshot": snapshot,
        "instance_index": 0,
        "creation_operation": "operation-original",
        "request_sha256": "b" * 64,
    }
    source = {
        "schema": migration.SCHEMA,
        "sources": {
            "gateway-0": {
                "schema": migration.RouteJournal.schema,
                "project_id": "project-1",
                "gateway_name": "gateway",
                "revision": 4,
                "transaction": None,
                "bindings": {} if mutation == "missing-receipt" else {snapshot["id"]: receipt},
            }
        },
    }
    actual = deepcopy(snapshot)
    if mutation == "spec_sha256":
        actual[mutation] = "c" * 64
    elif mutation == "labels":
        actual[mutation]["unreviewed"] = "change"
    observation["routes"] = [
        {
            "route_id": snapshot["id"],
            "route_table_id": snapshot["parent_id"],
            "prefix": snapshot["destination"],
            "name": snapshot["name"],
            "allocation_id": snapshot["allocation_id"],
            "revision": "7",
            "snapshot_sha256": digest(actual),
        }
    ]
    tx = journal.state.transaction
    journal.state = replace(
        journal.state,
        transaction=replace(
            tx,
            resource_bindings=tuple(
                sorted({**dict(tx.resource_bindings), migration.KEY: json.dumps(source)}.items())
            ),
            observation=normalize_vm_ha_observation(observation),
        ),
    )
    result = manager._vm_ha_migration_route_bindings(_ha_spec(), _route_targets(), "shared-private")
    assert [item.route_id for item in result] == ([] if mutation else ["route-owned"])
