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
    with pytest.raises(RuntimeError, match="no existing stateful SSH"):
        repair_cloud.management_rules((rule(kind=RuleType.STATELESS),))


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
