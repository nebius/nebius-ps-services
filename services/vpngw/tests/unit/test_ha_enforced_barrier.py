"""A stopped table cutover retires only the guest's former target bindings."""

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from nebius_vpngw import ha_repair
from nebius_vpngw.agent.vm_ha import forwarding
from nebius_vpngw.agent.vm_ha.runtime import RuntimeStateStore
from nebius_vpngw.deploy import ha_enforced_barrier as barrier
from nebius_vpngw.deploy import stopped_upgrade
from nebius_vpngw.deploy.vm_ha_routes import ManagedRouteKind, ManagedRouteOwnership
from nebius_vpngw.ordinary_operations import Manager
from nebius_vpngw.schema import VMHARouteTarget, VMHARuntimeBinding, VMHARuntimeNodeBinding


def binding(targets, generation):
    return VMHARuntimeBinding(
        cluster_id="cluster",
        shared_allocation_id="allocation",
        nodes=tuple(
            VMHARuntimeNodeBinding(
                node_id=f"node-{i}",
                role=role,
                compute_id=f"compute-{i}",
                network_interface_name="eth0",
                peer_endpoint=f"192.0.2.{i + 1}:9443",
            )
            for i, role in enumerate(("active", "passive"))
        ),
        route_targets=targets,
        route_runtime_id=VMHARuntimeBinding.derive_route_runtime_id(
            "cluster", "allocation", targets
        ),
        generation_id=generation,
        configuration_digest=generation,
        static_routes_digest="c" * 64,
        bgp_policy_digest="d" * 64,
    )


@pytest.fixture
def cutover(tmp_path, monkeypatch):
    targets = tuple(
        VMHARouteTarget(
            project_id="project",
            network_id="network",
            workload_subnet_id=f"subnet-{i}",
            route_table_id=f"table-{i}",
        )
        for i in range(3)
    )
    old = binding(targets, "a" * 64)
    new = binding(
        tuple(
            t.model_copy(update={"route_table_id": f"private-{i}"}) if i < 2 else t
            for i, t in enumerate(targets)
        ),
        "b" * 64,
    )
    root = tmp_path / "state"
    root.mkdir()
    store = RuntimeStateStore(root)
    ledger = {
        f"route-{i}": ManagedRouteOwnership("cluster", ManagedRouteKind.BGP, t)
        for i, t in enumerate(targets)
    }
    store.save_ledger(ledger)
    installed = tmp_path / "installed.yaml"
    staged = tmp_path / "staged.yaml"

    def config(value):
        return {
            "gateway": {"local_prefixes": ["10.0.0.0/16"]},
            "vm_ha": {
                "cluster_id": "cluster",
                "node": {"node_id": "node-0"},
                "generation": {"generation_id": value.generation_id},
                "runtime_binding": value.model_dump(mode="json"),
            },
        }

    installed.write_text(yaml.safe_dump(config(old)))
    staged.write_text(yaml.safe_dump(config(new)))
    boot = tmp_path / "boot"
    boot.write_text("boot")
    forwarding_path = tmp_path / "forwarding"
    forwarding_path.write_text("0")
    guard = {
        "schema": ha_repair.STATUS_SCHEMA,
        "guard_boot_id": "boot",
        "data_plane_mode": "blocked",
    }
    (root / "guard.json").write_text(json.dumps(guard))
    (root / "guard.json").chmod(0o600)
    expected = {
        "cluster_id": "cluster",
        "node_id": "node-0",
        "generation_id": new.generation_id,
        "operation_id": "operation",
    }
    request = {
        "expected": expected,
        "staged_path": str(staged),
        "staged_sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
        "allocation_id": "allocation",
        "route_runtime_id": new.route_runtime_id,
        "install": True,
        "units": ["controller", "agent"],
    }
    monkeypatch.setattr(ha_repair, "STATE", root)
    monkeypatch.setattr(ha_repair, "BOOT", boot)
    monkeypatch.setattr(ha_repair, "FORWARDING", forwarding_path)
    monkeypatch.setattr(ha_repair, "writer_locks", lambda **_: nullcontext())
    monkeypatch.setattr(
        ha_repair,
        "exact_lock",
        lambda value: dict(expected) if value == expected else pytest.fail("wrong operation"),
    )
    monkeypatch.setattr(
        ha_repair,
        "verified_file",
        lambda path, digest: (
            path.read_bytes()
            if hashlib.sha256(path.read_bytes()).hexdigest() == digest
            else pytest.fail("wrong file digest")
        ),
    )
    monkeypatch.setattr(
        barrier,
        "Path",
        lambda value: (
            installed if str(value) == "/etc/nebius-vpngw/config-resolved.yaml" else Path(value)
        ),
    )
    monkeypatch.setattr(stopped_upgrade, "verify_continuity", lambda *args, **kwargs: None)
    monkeypatch.setattr(Manager, "stopped", lambda self, unit: True)
    protection = SimpleNamespace(install=lambda: None, verify=lambda: None)
    monkeypatch.setattr(
        forwarding,
        "DestinationProtection",
        lambda *args, **kwargs: SimpleNamespace(enforced=lambda: protection),
    )
    return SimpleNamespace(
        store=store,
        ledger=ledger,
        old=old,
        new=new,
        root=root,
        request=request,
        installed=installed,
        staged=staged,
        forwarding=forwarding_path,
        guard=guard,
    )


@pytest.mark.parametrize("kind", list(ManagedRouteKind))
def test_cutover_retires_old_selected_targets_and_preserves_unselected(cutover, kind):
    cutover.store.save_ledger(
        {
            key: ManagedRouteOwnership(value.cluster_id, kind, value.route_target)
            for key, value in cutover.ledger.items()
        }
    )
    barrier.observe(cutover.request, continuity=stopped_upgrade.verify_continuity)
    assert cutover.store.load_ledger() == {
        "route-2": ManagedRouteOwnership("cluster", kind, cutover.old.route_targets[2])
    }
    before = cutover.store.ledger_path.read_bytes()
    barrier.observe(cutover.request, continuity=stopped_upgrade.verify_continuity)
    assert cutover.store.ledger_path.read_bytes() == before


def test_cutover_readback_does_not_retire_ledger(cutover):
    barrier.observe(
        {**cutover.request, "install": False}, continuity=stopped_upgrade.verify_continuity
    )
    assert cutover.store.load_ledger() == cutover.ledger


@pytest.mark.parametrize("drift", ["cluster", "target", "pending", "forwarding", "writer"])
def test_cutover_rejects_unsafe_ledger_retirement(cutover, monkeypatch, drift):
    if drift in {"cluster", "target"}:
        row = cutover.ledger["route-0"]
        cutover.ledger["route-0"] = ManagedRouteOwnership(
            "foreign" if drift == "cluster" else row.cluster_id,
            row.kind,
            row.route_target.model_copy(update={"route_table_id": "foreign"})
            if drift == "target"
            else row.route_target,
        )
        cutover.store.save_ledger(cutover.ledger)
    elif drift == "pending":
        cutover.store.pending_path.write_text("{}")
    elif drift == "forwarding":
        cutover.forwarding.write_text("1")
    else:
        monkeypatch.setattr(Manager, "stopped", lambda self, unit: False)
    before = cutover.store.ledger_path.read_bytes()
    with pytest.raises(RuntimeError):
        barrier.observe(cutover.request, continuity=stopped_upgrade.verify_continuity)
    assert cutover.store.ledger_path.read_bytes() == before


@pytest.mark.parametrize(
    "drift", ["allocation", "cluster", "node", "missing-binding", "unsafe-file"]
)
def test_cutover_requires_exact_predecessor_authority(cutover, drift):
    installed = yaml.safe_load(cutover.installed.read_text())
    old = installed["vm_ha"]["runtime_binding"]
    if drift == "allocation":
        old["shared_allocation_id"] = "foreign"
    elif drift == "cluster":
        old["cluster_id"] = "foreign"
    elif drift == "node":
        installed["vm_ha"]["node"]["node_id"] = "other-node"
    elif drift == "missing-binding":
        del installed["vm_ha"]["runtime_binding"]
    else:
        cutover.store.ledger_path.chmod(0o644)
    old["route_runtime_id"] = VMHARuntimeBinding.derive_route_runtime_id(
        old["cluster_id"], old["shared_allocation_id"], cutover.old.route_targets
    )
    cutover.installed.write_text(yaml.safe_dump(installed))
    before = cutover.store.ledger_path.read_bytes()
    with pytest.raises((RuntimeError, ValueError)):
        barrier.observe(cutover.request, continuity=stopped_upgrade.verify_continuity)
    assert cutover.store.ledger_path.read_bytes() == before


@pytest.mark.parametrize("empty", ["missing", "empty"])
def test_fresh_host_has_no_predecessor_ledger_to_retire(cutover, empty):
    if empty == "missing":
        cutover.store.ledger_path.unlink()
    else:
        cutover.store.save_ledger({})
    cutover.installed.unlink()
    barrier.observe(cutover.request, continuity=stopped_upgrade.verify_continuity)
    assert cutover.store.load_ledger() == {}


def test_unchanged_target_binding_preserves_all_ledger_rows(cutover):
    config = yaml.safe_load(cutover.staged.read_text())
    config["vm_ha"]["runtime_binding"] = binding(cutover.old.route_targets, "b" * 64).model_dump(
        mode="json"
    )
    cutover.staged.write_text(yaml.safe_dump(config))
    request = {
        **cutover.request,
        "staged_sha256": hashlib.sha256(cutover.staged.read_bytes()).hexdigest(),
        "route_runtime_id": cutover.old.route_runtime_id,
    }
    before = cutover.store.ledger_path.read_bytes()
    barrier.observe(request, continuity=stopped_upgrade.verify_continuity)
    assert cutover.store.ledger_path.read_bytes() == before


def test_cutover_cannot_relinquish_an_undeclared_subnet(cutover):
    config = yaml.safe_load(cutover.staged.read_text())
    desired = binding(cutover.new.route_targets[1:], "b" * 64)
    config["vm_ha"]["runtime_binding"] = desired.model_dump(mode="json")
    cutover.staged.write_text(yaml.safe_dump(config))
    request = {
        **cutover.request,
        "staged_sha256": hashlib.sha256(cutover.staged.read_bytes()).hexdigest(),
        "route_runtime_id": desired.route_runtime_id,
    }
    before = cutover.store.ledger_path.read_bytes()
    with pytest.raises(RuntimeError, match="predecessor binding"):
        barrier.observe(request, continuity=stopped_upgrade.verify_continuity)
    assert cutover.store.ledger_path.read_bytes() == before
