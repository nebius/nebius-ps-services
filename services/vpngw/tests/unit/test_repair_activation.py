import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from nebius_vpngw import cli, repair_guest, vm_ha_managed_credentials
from nebius_vpngw.deploy import repair_activation, vm_ha_lifecycle
from nebius_vpngw.deploy.ssh_push import SSHPush
from nebius_vpngw.deploy.vm_manager import VMManager


def bound_repair(tmp_path, monkeypatch):
    """Compose the real repair adapter with the canonical lock encoder."""
    stored, calls = [], []
    member = NS(instance_name="gateway-0", node_id="node-a", compute_id="vm-a", disk_id="disk-a")
    binding = NS(
        cluster_id="cluster",
        shared_allocation_id="allocation",
        route_runtime_id="routes",
        route_targets=(),
    )
    credentials = NS(resource_bindings=lambda: {})
    from nebius_vpngw.deploy import ordinary_route_migration, repair_routes

    monkeypatch.setattr(repair_routes, "prepare", lambda owner: None)
    monkeypatch.setattr(ordinary_route_migration, "capture", lambda *_a: None)
    monkeypatch.setattr(
        vm_ha_managed_credentials,
        "ensure_managed_vm_ha_credentials",
        lambda *_a, **_kw: NS(credentials=credentials),
    )
    monkeypatch.setattr(cli, "_vm_ha_initial_resource_bindings", lambda *_a, **_kw: {})
    monkeypatch.setattr(cli, "_vm_ha_desired_approval_state", lambda _plan: {"desired": "exact"})

    def fake_state(**kwargs):
        return NS(**kwargs, to_dict=lambda: {"operation": kwargs["transaction"].operation_id})

    monkeypatch.setattr(vm_ha_lifecycle, "VMHALifecycleState", fake_state)
    monkeypatch.setattr(
        vm_ha_lifecycle, "VMHALifecycleJournal", lambda _store, state: NS(state=state)
    )
    monkeypatch.setattr(
        vm_ha_lifecycle,
        "VMHALifecycleStore",
        lambda _path: NS(
            path=tmp_path / "lifecycle.json",
            read=lambda **_kw: stored[-1] if stored else None,
            write_verified=stored.append,
        ),
    )
    instance = NS(hostname="gateway-0", vm_ha_node=NS(node_id="node-a"))
    manager = VMManager(project_id="project", region="eu-north1")
    monkeypatch.setattr(
        manager,
        "observe_vm_ha_migration_state",
        lambda *_a: {
            "shared_allocation": {"allocation_id": "allocation"},
            "route_targets": [],
        },
    )
    monkeypatch.setattr(manager, "_build_vm_ha_runtime_binding", lambda *_a: binding)
    monkeypatch.setattr(manager, "build_vm_ha_lifecycle_members", lambda *_a: (member,))
    admission = NS(
        manager=manager,
        plan=NS(
            gateway_group=NS(
                name="gateway",
                region="region",
                vm_ha=NS(active_instance_index=0, cluster_id="cluster"),
            ),
            gateway={},
        ),
        credential_plan=object(),
        local={},
        instances=(instance,),
        targets={"gateway-0": "192.0.2.1"},
        intent={"artifact": "a" * 64, "configuration": "b" * 64},
        path=tmp_path / "config.yaml",
    )
    owner = NS(
        operation="c" * 32,
        resumed=False,
        directory=tmp_path,
        admission=admission,
        cloud=NS(sdk=object()),
        instances={"gateway-0": instance},
        context={
            "installed": {
                "gateway-0": {
                    "boot": "boot",
                    "guard": hashlib.sha256(repair_guest.GUARD).hexdigest(),
                }
            }
        },
        save=lambda: None,
        package_request=lambda _name: {"operation": "c" * 32},
        remote=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    return owner, stored, calls


def test_repair_lifecycle_identity_crosses_real_ha_lock_boundary(tmp_path, monkeypatch):
    owner, stored, calls = bound_repair(tmp_path, monkeypatch)
    _binding, journal, _credential = repair_activation.bind_ha(owner)
    operation = journal.state.transaction.operation_id
    _encoded, lock = SSHPush._vm_ha_apply_lock_payload(
        cluster_id="cluster",
        node_id="node-a",
        generation_id="b" * 64,
        operation_id=operation,
    )
    authority = repair_activation.Activation(owner)
    authority.verify_packages(NS(sha256="a" * 64), lock.operation_id)
    authority.allow_activation(owner.instances["gateway-0"], lock.operation_id)
    assert [args[1] for args, _kwargs in calls] == [
        "verify-installed",
        "prepare-activation",
        "grant",
    ]
    assert calls[0][1]["request"]["operation"] == owner.operation
    assert authority.snapshot()["transaction"]["execution_id"] == owner.operation
    assert authority.recovery().execution == owner.operation
    proof = json.loads(dict(stored[-1].transaction.resource_bindings)["repair-reset:gateway-0"])
    assert proof["operation"] == lock.operation_id
    assert repair_activation.bind_ha(owner)[1].state.transaction.operation_id == lock.operation_id
    assert len(stored) == 1
    with pytest.raises(RuntimeError, match="operation changed"):
        authority.allow_activation(owner.instances["gateway-0"], owner.operation)


@pytest.mark.parametrize("change", ["execution", "intent"])
def test_repair_ha_operation_cannot_cross_confirmed_authority(tmp_path, monkeypatch, change):
    owner, stored, _calls = bound_repair(tmp_path, monkeypatch)
    first = repair_activation.bind_ha(owner)[1].state.transaction.operation_id
    owner.context.pop("lifecycle_bound")
    if change == "execution":
        owner.operation = "d" * 32
    else:
        owner.admission.intent["configuration"] = "e" * 64
    second = repair_activation.ha_operation(owner)
    assert second != first
    with pytest.raises(RuntimeError, match="cannot cross confirmed authority"):
        repair_activation.bind_ha(owner)
    assert len(stored) == 1


def test_repair_persists_reconstruction_authority_before_retiring_lifecycle(tmp_path, monkeypatch):
    from nebius_vpngw import repair_state

    owner, stored, _ = bound_repair(tmp_path, monkeypatch)

    def interrupted(_path, _destination):
        record = json.loads((owner.directory / "reconstruction-authority.json").read_text())
        assert record == {
            "intent": repair_state.digest(owner.admission.intent),
            "state": {"operation": repair_activation.ha_operation(owner)},
        }
        raise OSError("interrupted lifecycle reset")

    monkeypatch.setattr(repair_activation, "archive_file", interrupted)
    with pytest.raises(OSError, match="interrupted lifecycle reset"):
        repair_activation.bind_ha(owner)
    assert not stored and not owner.context.get("lifecycle_bound")


@pytest.mark.parametrize("changed", [False, True])
def test_repair_binds_settled_ordinary_routes_before_ha_activation(tmp_path, monkeypatch, changed):
    from nebius_vpngw.apply_approval import digest
    from nebius_vpngw.deploy import ordinary_route_migration as migration
    from nebius_vpngw.deploy import repair_routes
    from nebius_vpngw.deploy.ordinary_route_ownership import RouteJournal, labels
    from nebius_vpngw.schema import VMHARouteTarget

    capture = migration.capture
    owner, stored, _ = bound_repair(tmp_path, monkeypatch)
    manager = owner.admission.manager
    target = VMHARouteTarget(
        project_id="project",
        network_id="network",
        workload_subnet_id="subnet",
        route_table_id="table",
    )
    snapshot = {
        "id": "route-owned",
        "parent_id": "table",
        "name": "vpngw-0123456789abcdef0123456789abcdef",
        "destination": "10.20.0.0/16",
        "allocation_id": "primary",
        "labels": labels("project", "gateway", "table", "10.20.0.0/16", 0),
        "spec_sha256": "a" * 64,
    }
    ledger = RouteJournal("project", "gateway", root=tmp_path / "routes")
    with ledger:
        ledger.state["bindings"] = {
            "route-owned": {
                "snapshot": snapshot,
                "instance_index": 0,
                "creation_operation": "original-op",
                "request_sha256": "b" * 64,
            }
        }
        ledger.save()
    monkeypatch.setattr(migration, "RouteJournal", lambda *_a: ledger)
    monkeypatch.setattr(migration, "capture", capture)
    steps = []
    monkeypatch.setattr(repair_routes, "prepare", lambda _owner: steps.append("settled"))
    observed = {
        "shared_allocation": {"allocation_id": "allocation"},
        "route_targets": [target.model_dump(mode="json")],
        "members": [{"instance_name": "gateway-0", "primary_allocation_id": "primary"}],
        "routes": [
            {
                "route_id": "route-owned",
                "route_table_id": "table",
                "name": snapshot["name"],
                "prefix": "10.20.0.0/16",
                "allocation_id": "primary",
                "revision": "7",
                "snapshot_sha256": digest(
                    {**snapshot, **({"spec_sha256": "c" * 64} if changed else {})}
                ),
            }
        ],
    }
    monkeypatch.setattr(manager, "observe_vm_ha_migration_state", lambda *_a: observed)

    def build(_sdk, spec):
        return NS(
            cluster_id="cluster",
            shared_allocation_id="allocation",
            route_runtime_id="routes",
            route_targets=(target,),
            migration_routes=manager._vm_ha_migration_route_bindings(spec, (target,), "allocation"),
        )

    monkeypatch.setattr(manager, "_build_vm_ha_runtime_binding", build)
    result, journal, _ = repair_activation.bind_ha(owner)
    assert [route.route_id for route in result.migration_routes] == (
        [] if changed else ["route-owned"]
    )
    assert steps == ["settled"]
    assert journal.state.transaction.observation
    assert migration.KEY in dict(journal.state.transaction.resource_bindings)
    # A resumed activation must keep the original approved source even if the
    # workstation receipt is subsequently absent. It never grants new ownership.
    monkeypatch.setattr(migration, "capture", lambda *_a: pytest.fail("recaptured authority"))
    repeated, same, _ = repair_activation.bind_ha(owner)
    assert same is journal and len(stored) == 1
    assert repeated.migration_routes == result.migration_routes


def test_ha_repair_waits_for_route_settlement_before_new_lifecycle(tmp_path, monkeypatch):
    from nebius_vpngw.deploy import repair_routes

    owner, stored, _ = bound_repair(tmp_path, monkeypatch)

    def unsettled(_owner):
        raise RuntimeError("Previous route effects are still settling")

    monkeypatch.setattr(repair_routes, "prepare", unsettled)
    with pytest.raises(RuntimeError, match="still settling"):
        repair_activation.bind_ha(owner)
    assert not stored and owner.admission.manager._vm_ha_journal is None
    assert not (owner.directory / "reconstruction-authority.json").exists()


@pytest.mark.parametrize("pending", [False, True])
def test_ordinary_activation_plans_after_policy_handoff_and_preserves_resume(
    tmp_path, monkeypatch, pending
):
    from contextlib import nullcontext

    from nebius_vpngw.deploy import (
        apply_command,
        ordinary_apply,
        ordinary_command,
        repair_routes,
    )

    class Inspected(Exception):
        pass

    name = "gateway-0"
    policy = {"owner": "ordinary" if pending else "repair"}
    manifests = {name: {"original": "approved"}} if pending else {}
    monkeypatch.setattr(repair_routes, "prepare", lambda _owner: None)
    monkeypatch.setattr(apply_command, "CommandJournal", lambda *_a, **_kw: nullcontext())
    monkeypatch.setattr(ordinary_command, "package_manifests", lambda *_a, **_kw: manifests)

    def remote(_name, action, **_payload):
        assert not pending, "An interrupted ordinary transaction owns its package-start policy"
        assert action == "prepare-activation", "Service startup must remain inhibited"
        policy["owner"] = "operator"

    def inspect(*_args, **kwargs):
        assert policy["owner"] == ("ordinary" if pending else "operator")
        assert kwargs.get("resume_manifest") == manifests.get(name)
        raise Inspected

    monkeypatch.setattr(ordinary_apply, "inspect_plan", inspect)
    owner = NS(
        directory=tmp_path,
        instances={name: NS(hostname=name)},
        context={"ordinary_route_handoff": {}},
        remote=remote,
        admission=NS(
            intent={"project": "project", "gateway": "gateway", "artifact": "a" * 64},
            manager=NS(inspect_ordinary_targets=lambda _: {name: ("vm-a", "192.0.2.1")}),
            bindings={name: {"compute": "vm-a"}},
            targets={name: "192.0.2.1"},
            instances=[NS(hostname=name)],
            local={},
            ssh=object(),
        ),
    )
    with pytest.raises(Inspected):
        repair_activation.activate_ordinary(owner)
