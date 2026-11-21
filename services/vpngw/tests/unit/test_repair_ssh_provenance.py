"""Repair must use exact lifecycle authority for the retained ordinary member."""

from types import SimpleNamespace

import pytest

from nebius_vpngw.deploy import repair_apply, repair_ssh, vm_manager
from nebius_vpngw.deploy.recovery import RepairInspectionFailure
from nebius_vpngw.deploy.vm_ha_identity import (
    PROVISIONING_MARKER_SCHEMA,
    FormerVMHAProvenance,
)
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus, VMHALifecycleStore
from nebius_vpngw.schema import VMHARole


class PolicyBoundary(Exception):
    pass


@pytest.fixture
def admission(monkeypatch, tmp_path):
    manager = vm_manager.VMManager(project_id="project-a", region="region-a")
    members = tuple(
        SimpleNamespace(
            instance_index=i,
            node_id=f"node-{i}",
            role=VMHARole.ACTIVE if i == 0 else VMHARole.PASSIVE,
        )
        for i in (0, 1)
    )
    spec = SimpleNamespace(
        name="gateway",
        region="region-a",
        vm_ha=SimpleNamespace(cluster_id="cluster-a", members=members),
    )
    plan = SimpleNamespace(gateway_group=spec, vm_ha=spec.vm_ha)
    snapshot = {f"gateway-{i}": (SimpleNamespace(index=i), f"192.0.2.{i + 1}") for i in (0, 1)}
    manager._vm_ha_ssh_preflight_snapshot = snapshot
    monkeypatch.setattr(
        manager, "discover_vm_ha_members", lambda _: {n: a for n, (_, a) in snapshot.items()}
    )
    monkeypatch.setattr(manager, "_build_sdk_client", lambda _: object())
    monkeypatch.setattr(
        manager,
        "_vm_ha_ssh_member_signature",
        lambda v: (
            f"compute-{v.index}",
            f"gateway-{v.index}",
            "project-a",
            "7",
            f"192.0.2.{v.index + 1}",
            "bootstrap",
        ),
    )
    monkeypatch.setattr(
        manager,
        "_get_vm_by_id_for_vm_ha_preflight",
        lambda _, key: snapshot["gateway-" + key[-1]][0],
    )
    monkeypatch.setattr(
        vm_manager,
        "compute_provisioning_provenance",
        lambda v: None if v.index == 0 else FormerVMHAProvenance.CURRENT_MARKER,
    )
    monkeypatch.setattr(
        repair_ssh,
        "compute_provisioning_provenance",
        lambda v: vm_manager.compute_provisioning_provenance(v),
    )
    marker_members = [
        {
            "instance_index": i,
            "instance_name": f"gateway-{i}",
            "node_id": f"node-{i}",
            "role": "active" if i == 0 else "passive",
        }
        for i in (0, 1)
    ]
    monkeypatch.setattr(
        vm_manager,
        "parse_provisioning_marker",
        lambda v: {
            "schema": PROVISIONING_MARKER_SCHEMA,
            "cluster_id": "cluster-a",
            "shared_allocation_name": "gateway-cluster-a-shared-private-ip",
            "member": marker_members[v.index],
            "members": marker_members,
        },
    )
    state = SimpleNamespace(
        status=VMHALifecycleStatus.ACTIVATING,
        project_id="project-a",
        gateway_name="gateway",
        cluster_id="cluster-a",
        members=tuple(
            SimpleNamespace(
                instance_name=f"gateway-{i}",
                compute_id=f"compute-{i}",
                public_ip=f"192.0.2.{i + 1}",
                node_id=f"node-{i}",
                role="active" if i == 0 else "passive",
            )
            for i in (0, 1)
        ),
    )
    checked = []
    evidence = SimpleNamespace(state=state, assert_current=lambda: checked.append("current"))
    path = tmp_path / "gateway.yaml"

    def read(store, **kwargs):
        assert store.path == path.with_name(path.name + ".vm-ha-lifecycle.json")
        assert kwargs == {"expected_project_id": "project-a", "expected_gateway_name": "gateway"}
        return evidence

    monkeypatch.setattr(VMHALifecycleStore, "read_hardened", read)
    monkeypatch.setattr(repair_apply, "retained_binding", lambda _: {"project": "project-a"})
    monkeypatch.setattr(repair_ssh, "bindings", lambda *_: {})

    def policy(*args, **kwargs):
        raise PolicyBoundary

    monkeypatch.setattr(repair_apply, "require_vm_ha_ssh_policy", policy)

    def call():
        return repair_apply.inspect(
            manager=manager,
            plan=plan,
            local={"gateway_group": {}},
            path=path,
            instances=tuple(SimpleNamespace(hostname=n) for n in snapshot),
        )

    return call, state, evidence, checked


def test_repair_admits_exact_interrupted_ordinary_member_without_ha_bootstrap_marker(admission):
    call, _, _, checked = admission
    with pytest.raises(RepairInspectionFailure) as failure:
        call()
    assert failure.value.check == "ssh-policy"
    assert isinstance(failure.value.__cause__, PolicyBoundary)
    assert checked


@pytest.mark.parametrize("field", ["project_id", "gateway_name", "cluster_id"])
def test_repair_rejects_foreign_lifecycle_identity(admission, field):
    call, state, _, _ = admission
    setattr(state, field, "foreign")
    with pytest.raises(RepairInspectionFailure) as failure:
        call()
    assert failure.value.check == "ssh-authority"
    assert isinstance(failure.value.__cause__, RuntimeError)
    assert (
        str(failure.value.__cause__) == "VM-HA legacy SSH recovery lifecycle binding does not match"
    )


@pytest.mark.parametrize("field", ["compute_id", "public_ip", "node_id", "role"])
def test_repair_rejects_changed_retained_member_identity(admission, field):
    call, state, _, _ = admission
    setattr(state.members[0], field, "foreign")
    with pytest.raises(RepairInspectionFailure) as failure:
        call()
    assert failure.value.check == "ssh-authority"
    assert isinstance(failure.value.__cause__, RuntimeError)
    assert (
        str(failure.value.__cause__) == "VM-HA legacy SSH recovery lifecycle binding does not match"
    )


def test_repair_rejects_lifecycle_changed_during_admission(admission):
    call, _, evidence, _ = admission

    def changed():
        raise ValueError("private-sentinel")

    evidence.assert_current = changed
    with pytest.raises(RuntimeError, match="SSH identity") as failure:
        call()
    assert "private-sentinel" not in str(failure.value)


@pytest.mark.parametrize("kind", ["missing", "malformed"])
def test_repair_requires_hardened_lifecycle_for_unmarked_retained_member(
    admission, monkeypatch, kind
):
    call, _, _, _ = admission

    def unavailable(*args, **kwargs):
        if kind == "malformed":
            raise ValueError("private-sentinel")
        return None

    monkeypatch.setattr(VMHALifecycleStore, "read_hardened", unavailable)
    with pytest.raises(RuntimeError, match="SSH identity") as failure:
        call()
    assert "private-sentinel" not in str(failure.value)


def test_native_ha_marker_does_not_require_parsing_obsolete_lifecycle(admission, monkeypatch):
    call, _, _, _ = admission
    monkeypatch.setattr(
        vm_manager, "compute_provisioning_provenance", lambda _: FormerVMHAProvenance.CURRENT_MARKER
    )
    monkeypatch.setattr(
        VMHALifecycleStore,
        "read_hardened",
        lambda *_a, **_k: pytest.fail("obsolete lifecycle read"),
    )
    with pytest.raises(RepairInspectionFailure) as failure:
        call()
    assert failure.value.check == "ssh-policy"
    assert isinstance(failure.value.__cause__, PolicyBoundary)
