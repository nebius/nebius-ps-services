from types import SimpleNamespace

import pytest
from nebius.api.nebius.vpc.v1 import (
    RuleAccessAction,
    RuleEgress,
    RuleIngress,
    RuleProtocol,
    RuleType,
    SecurityRuleSpec,
)

from nebius_vpngw.deploy import repair_cloud


def rule(
    *,
    access=RuleAccessAction.ALLOW,
    protocol=RuleProtocol.ANY,
    ports=(),
    priority=500,
    kind=RuleType.STATEFUL,
    source="192.0.2.0/24",
):
    return SimpleNamespace(
        spec=SecurityRuleSpec(
            access=access,
            protocol=protocol,
            priority=priority,
            type=kind,
            ingress=RuleIngress(source_cidrs=[source], destination_ports=list(ports)),
        )
    )


def test_quarantine_narrows_existing_management_access_and_preserves_denies():
    values = (
        rule(),
        rule(access=RuleAccessAction.DENY, source="192.0.2.42/32", priority=100),
        rule(protocol=RuleProtocol.UDP, ports=(500, 4500)),
        rule(protocol=RuleProtocol.TCP, ports=(443,)),
        SimpleNamespace(
            spec=SecurityRuleSpec(
                access=RuleAccessAction.ALLOW,
                protocol=RuleProtocol.ANY,
                egress=RuleEgress(destination_cidrs=["0.0.0.0/0"]),
            )
        ),
    )
    result = repair_cloud.management_rules(values)
    assert len(result) == 2
    assert all(
        item.protocol == RuleProtocol.TCP and list(item.ingress.destination_ports) == [22]
        for item in result
    )
    assert list(result[0].ingress.source_cidrs) == ["192.0.2.0/24"]
    assert result[1].access == RuleAccessAction.DENY and result[1].priority == 100
    assert list(result[1].ingress.source_cidrs) == ["192.0.2.42/32"]
    assert values[0].spec.protocol == RuleProtocol.ANY


def test_quarantine_does_not_broaden_stateless_ssh_policy():
    from nebius_vpngw.deploy.recovery import Invocation, RepairBlocked, advice

    with pytest.raises(RepairBlocked) as failure:
        repair_cloud.management_rules((rule(kind=RuleType.STATELESS),))
    recovery = advice(Invocation("apply", repair=True), failure.value)
    assert recovery.reason_code == "repair-quarantine-access-unproven"
    assert "stateless" in recovery.repair_prerequisite


@pytest.mark.parametrize("orphan_effect", [False, True, "retry"])
def test_partial_quarantine_cleanup_uses_exact_created_group_receipt(monkeypatch, orphan_effect):
    import nebius.api.nebius.vpc.v1 as api

    calls = []
    key = "node-a-0"
    records = {"group": {"label": "quarantine-group-" + key}}
    if orphan_effect is True:
        records["rule"] = {"label": "quarantine-rule-" + key + "-0"}
    if orphan_effect == "retry":
        records["rule"] = {"label": "quarantine-rule-" + key + "-0-repair-retry-1"}
    owner = SimpleNamespace(
        records=records,
        settle=lambda: calls.append("settle"),
        completed=lambda label: (
            "owned-group"
            if label == "quarantine-group-" + key
            else "owned-rule"
            if orphan_effect == "retry"
            else None
        ),
        mutate=lambda label, request, invoke: calls.append((label, request.id)),
    )
    quarantine = repair_cloud.Quarantine.__new__(repair_cloud.Quarantine)
    quarantine.name = "node-a"
    quarantine.cloud = SimpleNamespace(owner=owner, sdk=object())
    quarantine.inspect = lambda: {
        "restored": True,
        "original": [{"name": "eth0"}],
        "groups": {"eth0": "owned-group"},
        "rules": {"eth0": [{}]} if orphan_effect == "retry" else {},
    }
    quarantine._attach = lambda value, **kwargs: calls.append("verify-original")
    monkeypatch.setattr(api, "SecurityGroupServiceClient", lambda sdk: SimpleNamespace(delete=None))
    monkeypatch.setattr(api, "SecurityRuleServiceClient", lambda sdk: SimpleNamespace(delete=None))
    if orphan_effect is True:
        with pytest.raises(RuntimeError, match="lacks its manifest"):
            quarantine.cleanup()
        assert calls == ["settle", "verify-original"]
    else:
        quarantine.cleanup()
        if orphan_effect == "retry":
            assert calls.pop(2) == ("quarantine-delete-rule-" + key + "-0", "owned-rule")
        assert calls == [
            "settle",
            "verify-original",
            ("quarantine-delete-group-" + key, "owned-group"),
        ]


@pytest.mark.parametrize("kind", ["rule", "group"])
@pytest.mark.parametrize("status", ["failed", "rejected"])
def test_quarantine_cleanup_never_treats_failed_creation_as_absence(monkeypatch, kind, status):
    calls = []
    label = "quarantine-" + kind + "-node-a-0" + ("-0" if kind == "rule" else "")
    quarantine = repair_cloud.Quarantine.__new__(repair_cloud.Quarantine)
    quarantine.name = "node-a"
    quarantine.cloud = SimpleNamespace(
        owner=SimpleNamespace(
            records={"attempt": {"label": label, "status": status}},
            settle=lambda: calls.append("settle"),
            completed=lambda _: None,
            mutate=lambda *_: pytest.fail("ambiguous creation must not authorize cleanup"),
        )
    )
    quarantine.inspect = lambda: {
        "restored": True,
        "original": [{"name": "eth0"}],
        "groups": {},
        "rules": {"eth0": [{}]} if kind == "rule" else {},
    }
    quarantine._attach = lambda *_a, **_k: calls.append("verify-original")
    with pytest.raises(RuntimeError, match=kind + " creation outcome is unproven"):
        quarantine.cleanup()
    assert calls == ["settle", "verify-original"]


def test_cloud_journal_inspection_does_not_write(tmp_path):
    journal = repair_cloud.CloudJournal(
        tmp_path / "repair",
        operation="a" * 32,
        intent={"project": "project-fixture", "fixture": True},
    )
    assert not journal.path.parent.exists()
    journal.save()
    assert (
        repair_cloud.CloudJournal(
            tmp_path / "repair",
            operation="a" * 32,
            intent={"project": "project-fixture", "fixture": True},
        ).state
        == journal.state
    )
    with pytest.raises(RuntimeError, match="another confirmed operation"):
        repair_cloud.CloudJournal(
            tmp_path / "repair",
            operation="a" * 32,
            intent={"project": "project-fixture", "fixture": False},
        )


def test_resource_settlement_lists_every_page_and_does_not_require_old_success(monkeypatch):
    seen, waited = [], []

    class Endpoint:
        def list(self, request, **_kwargs):
            assert 1 <= request.page_size <= 999
            seen.append((request.resource_id, request.page_token))
            suffix = "2" if request.page_token else "1"
            return SimpleNamespace(
                operations=[SimpleNamespace(id="operation-" + suffix, resource_id="vm-a")],
                next_page_token="second" if not request.page_token else "",
            )

        def get(self, request, **_kwargs):
            return SimpleNamespace(wait=lambda: SimpleNamespace(id=request.id, done=lambda: True))

    monkeypatch.setattr(repair_cloud, "wait_vm_ha_operation", lambda value: waited.append(value.id))
    assert repair_cloud.settle_resource(SimpleNamespace(operation_service=Endpoint), "vm-a") == (
        "operation-1",
        "operation-2",
    )
    assert seen == [("vm-a", ""), ("vm-a", "second")]
    assert waited == ["operation-1", "operation-2"]


def test_foreign_operation_is_not_adopted_as_repair_history():
    class Endpoint:
        def list(self, _request, **_kwargs):
            return SimpleNamespace(
                operations=[SimpleNamespace(id="operation-1", resource_id="foreign-vm")],
                next_page_token="",
            )

    with pytest.raises(RuntimeError, match="ambiguous resource identity"):
        repair_cloud.settle_resource(SimpleNamespace(operation_service=Endpoint), "vm-a")


def test_repeated_power_cycles_never_reuse_a_completed_transition():
    cloud = repair_cloud.RetainedCloud.__new__(repair_cloud.RetainedCloud)
    cloud.owner = SimpleNamespace(records={})
    first = cloud.transition_label("repair-stop", "node-a")
    cloud.owner.records["first"] = {"label": first, "status": "settled"}
    second = cloud.transition_label("repair-stop", "node-a")
    assert second != first
    cloud.owner.records["second"] = {"label": second, "status": "accepted"}
    with pytest.raises(RuntimeError, match="still settling"):
        cloud.transition_label("repair-stop", "node-a")
    assert cloud.transition_label("repair-stop", "node-b").endswith("-1")
