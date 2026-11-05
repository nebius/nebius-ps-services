from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nebius.api.nebius.compute.v1 import IPAlias
from test_replacement_ordinary import instance
from test_vm_ha_prepared_replacement import (
    active_state,
    membership_evidence,
    plan,
    replacement_member,
)

from nebius_vpngw.deploy import replacement_ha as ha
from nebius_vpngw.deploy.replacement_cloud import Cloud
from nebius_vpngw.deploy.replacement_ha_runtime import Runtime
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStore


def test_policy_comparison_excludes_only_generation_metadata_and_runtime_binding():
    base = {
        "connections": [{"tunnels": [{"psk": "fixture-only", "remote_public_ip": "192.0.2.4"}]}],
        "vm_ha": {
            "cluster_id": "cluster",
            "node": {"node_id": "node"},
            "generation": {"generation_id": "old"},
            "readiness": {},
            "runtime_binding": {},
        },
    }
    changed = {
        **base,
        "vm_ha": {
            **base["vm_ha"],
            "generation": {"generation_id": "new"},
            "runtime_binding": {"nodes": [{"compute_id": "new"}]},
        },
    }
    assert ha.policy(base) == ha.policy(changed)
    changed["connections"] = [
        {"tunnels": [{"psk": "different-fixture", "remote_public_ip": "192.0.2.4"}]}
    ]
    assert ha.policy(base) != ha.policy(changed)
    changed = {**base, "vm_ha": {**base["vm_ha"], "node": {"node_id": "other-node"}}}
    assert ha.policy(base) != ha.policy(changed)


def test_cloud_source_digest_allows_only_exact_shared_alias_and_public_pin_changes():
    source = instance()
    before = ha.spec_digest(source, "shared")
    source.spec.network_interfaces[0].aliases = [IPAlias(allocation_id="shared")]
    assert ha.spec_digest(source, "shared") == before
    source.spec.network_interfaces[0].subnet_id = "foreign-subnet"
    assert ha.spec_digest(source, "shared") != before
    source.spec.network_interfaces[0].aliases = [IPAlias(allocation_id="foreign-alias")]
    with pytest.raises(RuntimeError, match="unrelated secondary"):
        ha.spec_digest(source, "shared")


def state_journal(base):
    mapping = plan(base)
    mapping["ha"] = {
        "lifecycle": base.to_dict(),
        "current_members": [item.to_dict() for item in base.members],
    }
    return SimpleNamespace(state={"transaction": {"plan": mapping, "results": {}}}, save=Mock())


def ports(path):
    obj = ha.HAReplacement.__new__(ha.HAReplacement)
    obj.store = VMHALifecycleStore(path)
    return obj


def test_ha_lifecycle_write_ahead_recovers_after_store_failure_and_on_new_operator(
    tmp_path, monkeypatch
):
    base = active_state()
    journal = state_journal(base)
    first = ports(tmp_path / "original.yaml")
    first.store.write_verified(base)
    first._lifecycle(journal)
    owner, old = first.state.members
    candidate = replacement_member(old)
    real_write = first.store.write_verified
    monkeypatch.setattr(
        first.store, "write_verified", Mock(side_effect=OSError("simulated disk interruption"))
    )
    with pytest.raises(OSError):
        first.transition(
            journal,
            revision=2,
            members=(owner, candidate),
            evidence=membership_evidence(old, candidate, owner),
        )
    assert len(journal.state["transaction"]["results"]["ha-lifecycle"]) == 2
    monkeypatch.setattr(first.store, "write_verified", real_write)
    first._lifecycle(journal)
    assert first.state.transaction.revision == 2
    second = ports(tmp_path / "new-operator.yaml")
    second._lifecycle(journal)
    assert second.state == first.state
    assert (
        second.store.read(
            expected_project_id=base.project_id, expected_gateway_name=base.gateway_name
        )
        == first.state
    )


def test_local_foreign_lifecycle_is_never_overwritten_by_bundle(tmp_path):
    base = active_state()
    journal = state_journal(base)
    target = ports(tmp_path / "gateway.yaml")
    foreign = replace(
        base, transaction=replace(base.transaction, operation_id="different-existing-operation")
    )
    target.store.write_verified(foreign)
    with pytest.raises(RuntimeError, match="differs"):
        target._lifecycle(journal)
    assert (
        target.store.read(
            expected_project_id=base.project_id, expected_gateway_name=base.gateway_name
        )
        == foreign
    )


def test_failed_pair_fence_cannot_stop_or_retire_source(monkeypatch):
    obj = ha.HAReplacement.__new__(ha.HAReplacement)
    obj.sdk = object()
    obj._prepared = Mock(return_value={})
    cloud = Mock(spec=Cloud)
    cloud.completed.return_value = None
    monkeypatch.setattr(ha, "Cloud", lambda *a, **kw: cloud)
    runtime = Mock(spec=Runtime)
    runtime.fence_sources.side_effect = RuntimeError("owner writer did not quiesce")
    monkeypatch.setattr(ha, "Runtime", lambda *args: runtime)
    base = active_state()
    journal = state_journal(base)
    journal.state["transaction"]["member"] = 0
    with pytest.raises(RuntimeError, match="quiesce"):
        obj.execute(journal, step="stop-old", effect_id="effect")
    cloud.stop.assert_not_called()
    cloud.retire.assert_not_called()


def test_source_locks_are_nonowner_first_and_reproved_on_retry():
    base = active_state()
    owner, standby = base.members
    events = []
    ssh = SimpleNamespace(
        install_vm_ha_apply_lock=lambda address, cfg, local, **kw: (
            events.append(("lock", cfg.hostname)) or SimpleNamespace()
        ),
        stop_vm_ha_mutation_services=lambda address, logical, local: events.append(
            ("drain", logical)
        ),
    )
    from nebius_vpngw.deploy.ssh_push import VMHAApplyLockReceipt

    ssh.install_vm_ha_apply_lock = lambda address, cfg, local, **kw: (
        events.append(("lock", cfg.hostname))
        or VMHAApplyLockReceipt("cluster", cfg.hostname, "b" * 64, "a" * 64, "c" * 64)
    )
    obj = Runtime.__new__(Runtime)
    obj.record = {}
    obj.operation = "a" * 64
    obj.journal = SimpleNamespace(save=Mock())
    obj.ports = SimpleNamespace(
        members=lambda *a, **kw: base.members,
        runtime_binding=lambda *a, **kw: object(),
        owner=lambda members: owner,
        access=lambda compute, name: (None, SimpleNamespace(address=name)),
        ssh=ssh,
        instances={
            item.instance_name: SimpleNamespace(hostname=item.instance_name)
            for item in base.members
        },
        local={},
    )
    obj.fence_sources()
    assert events == [
        ("lock", standby.instance_name),
        ("drain", standby.instance_name),
        ("lock", owner.instance_name),
        ("drain", owner.instance_name),
    ]
    events.clear()
    obj.fence_sources()
    assert events == [("drain", standby.instance_name), ("drain", owner.instance_name)]


def test_activation_retry_after_unlock_does_not_restart_or_relock_pair(monkeypatch):
    from dataclasses import asdict

    from nebius_vpngw.deploy import replacement_ha_runtime as runtime_module
    from nebius_vpngw.deploy.ssh_push import VMHAApplyLockReceipt

    base = active_state()
    owner, candidate = base.members
    obj = Runtime.__new__(Runtime)
    obj.record = {
        "owner-staged": True,
        "candidate-guard-released": True,
        "owner-adoption": True,
        "activated:" + owner.instance_name: True,
        "activated:" + candidate.instance_name: True,
        "mtls-finalized": True,
        "policy-verified": True,
        "locks": {},
    }
    for member in base.members:
        obj.record["locks"][member.instance_name] = asdict(
            VMHAApplyLockReceipt(base.cluster_id, member.node_id, "b" * 64, "c" * 64, "d" * 64)
        )
    obj.journal = SimpleNamespace(save=Mock())
    binding = SimpleNamespace(generation_id="b" * 64)
    ssh = Mock()
    ports = SimpleNamespace(
        members=lambda *a, **kw: base.members,
        runtime_binding=lambda *a, **kw: binding,
        owner=lambda members: owner,
        access=lambda compute, name: (None, SimpleNamespace(address=name)),
        ssh=ssh,
        instances={
            item.instance_name: SimpleNamespace(hostname=item.instance_name)
            for item in base.members
        },
        local={},
        require_owner=Mock(),
    )
    obj.ports = ports
    obj.status = Mock(return_value={"apply_locked": False})
    obj.wait = Mock(return_value={})
    mtls = Mock(return_value=SimpleNamespace(operation_id=None))
    monkeypatch.setattr(runtime_module.cli, "_prepare_vm_ha_managed_mtls", mtls)
    assert obj.activate()["compute_id"] == candidate.compute_id
    ssh.clear_vm_ha_apply_lock.assert_not_called()
    ssh.install_vm_ha_apply_lock.assert_not_called()
    ssh.push_config_and_reload.assert_not_called()
    assert ports.require_owner.call_count == 2
    assert obj.wait.call_count == 2
    assert all(obj.record["unlocked:" + member.instance_name] for member in base.members)


def test_activation_never_unlocks_candidate_before_owner_is_proven(monkeypatch):
    from dataclasses import asdict

    from nebius_vpngw.deploy import replacement_ha_runtime as runtime_module
    from nebius_vpngw.deploy.ssh_push import VMHAApplyLockReceipt

    base = active_state()
    owner, candidate = base.members
    obj = Runtime.__new__(Runtime)
    obj.record = {
        "owner-staged": True,
        "candidate-guard-released": True,
        "owner-adoption": True,
        "activated:" + owner.instance_name: True,
        "activated:" + candidate.instance_name: True,
        "mtls-finalized": True,
        "locks": {
            item.instance_name: asdict(
                VMHAApplyLockReceipt(base.cluster_id, item.node_id, "b" * 64, "c" * 64, "d" * 64)
            )
            for item in base.members
        },
    }
    obj.journal = SimpleNamespace(save=Mock())
    binding = SimpleNamespace(generation_id="b" * 64)
    ssh = Mock()
    obj.ports = SimpleNamespace(
        members=lambda *a, **kw: base.members,
        runtime_binding=lambda *a, **kw: binding,
        owner=lambda members: owner,
        access=lambda compute, name: (None, SimpleNamespace(address=name)),
        ssh=ssh,
        instances={
            item.instance_name: SimpleNamespace(hostname=item.instance_name)
            for item in base.members
        },
        local={},
        require_owner=Mock(),
    )
    obj.status = Mock(return_value={"apply_locked": True})
    obj.wait = Mock(side_effect=RuntimeError("route receipt not proven"))
    monkeypatch.setattr(runtime_module.cli, "_prepare_vm_ha_managed_mtls", Mock())
    with pytest.raises(RuntimeError, match="route receipt"):
        obj.activate()
    assert ssh.clear_vm_ha_apply_lock.call_count == 1
    assert ssh.clear_vm_ha_apply_lock.call_args.args[0] == owner.instance_name
    assert "unlocked:" + candidate.instance_name not in obj.record
