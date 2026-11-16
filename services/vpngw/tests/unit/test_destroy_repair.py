from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from google.protobuf.json_format import MessageToDict
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute import v1 as compute
from nebius.api.nebius.vpc import v1 as vpc

from nebius_vpngw.deploy import destroy_repair
from nebius_vpngw.deploy.repair_cloud import CloudJournal
from nebius_vpngw.deploy.repair_cloud_retry import RepairCloud
from nebius_vpngw.deploy.replacement_cloud import effect_key, message_digest


@pytest.fixture
def quarantine(tmp_path, monkeypatch):
    intent = {"project": "project-a", "gateway": "gateway", "targets": [{"compute": "old"}]}
    journal = CloudJournal(tmp_path / "repair", operation="a" * 32, intent=intent)
    cloud = RepairCloud(object(), journal, member_index=0, operation_scope=journal.operation)
    group_request = vpc.CreateSecurityGroupRequest(
        metadata=ResourceMetadata(parent_id="project-a", name="vpngw-repair-" + "a" * 16 + "-0"),
        spec=vpc.SecurityGroupSpec(network_id="network-a"),
    )
    rule_request = vpc.CreateSecurityRuleRequest(
        metadata=ResourceMetadata(parent_id="group-a", name="repair-0"),
        spec=vpc.SecurityRuleSpec(
            priority=1,
            access=vpc.RuleAccessAction.DENY,
            protocol=vpc.RuleProtocol.ANY,
            type=vpc.RuleType.STATELESS,
            egress=vpc.RuleEgress(destination_cidrs=["0.0.0.0/0"]),
        ),
    )
    for label, identifier, request in (
        ("group", "group-a", group_request),
        ("rule", "rule-a", rule_request),
    ):
        cloud.records[effect_key(journal.intent, 0, label, journal.operation)] = {
            "label": label,
            "resource_id": identifier,
            "status": "settled",
            "request_type": type(request).__name__,
            "request_sha256": message_digest(request),
            "request_metadata": MessageToDict(
                request.__pb2_message__, preserving_proto_field_name=True
            ),
        }
    journal.save()
    groups = {
        "group-a": vpc.SecurityGroup(
            metadata=ResourceMetadata(
                id="group-a", parent_id="project-a", name=group_request.metadata.name
            ),
            spec=group_request.spec,
        )
    }
    rules = {
        "rule-a": vpc.SecurityRule(
            metadata=ResourceMetadata(id="rule-a", parent_id="group-a", name="repair-0"),
            spec=rule_request.spec,
        )
    }
    instances, events, failures = [], [], []

    class Missing(Exception):
        pass

    def service(values):
        def get(request, **_):
            if request.id not in values:
                raise Missing()
            return NS(wait=lambda: values[request.id])

        def delete(request, **_):
            if failures:
                raise failures.pop()
            events.append(request.id)
            values.pop(request.id)
            operation = NS(
                id="delete-" + request.id,
                resource_id=request.id,
                sync_wait=lambda **_: None,
                successful=lambda: True,
                done=lambda: True,
            )
            return NS(wait=lambda: operation)

        return NS(
            get=get,
            delete=delete,
            list=lambda request, **_: NS(
                items=[
                    value
                    for value in values.values()
                    if value.metadata.parent_id == request.parent_id
                ],
                next_page_token="",
            ),
        )

    monkeypatch.setattr(vpc, "SecurityGroupServiceClient", lambda _: service(groups))
    monkeypatch.setattr(vpc, "SecurityRuleServiceClient", lambda _: service(rules))
    monkeypatch.setattr(
        compute,
        "InstanceServiceClient",
        lambda _: NS(list=lambda *_a, **_k: NS(items=instances, next_page_token="")),
    )
    monkeypatch.setattr(
        destroy_repair,
        "nebius_request_error_code_is",
        lambda error, code: isinstance(error, Missing) and code == "NOT_FOUND",
    )
    return cloud, groups, rules, instances, events, failures


def test_destroy_quarantine_uses_creation_receipts_without_quarantine_file(quarantine):
    cloud, groups, rules, _, events, _ = quarantine
    originals = deepcopy(cloud.records)
    destroy_repair.cleanup_quarantine(cloud, "project-a")
    assert not groups and not rules and events == ["rule-a", "group-a"]
    assert all(cloud.records[key] == value for key, value in originals.items())
    assert len(cloud.records) == 4
    destroy_repair.cleanup_quarantine(cloud, "project-a")
    assert events == ["rule-a", "group-a"]


@pytest.mark.parametrize(
    "change", ["foreign-rule", "attachment", "rule-policy", "group-network", "default"]
)
def test_destroy_quarantine_preserves_foreign_state(quarantine, change):
    cloud, groups, rules, instances, events, _ = quarantine
    if change == "foreign-rule":
        rules["foreign"] = vpc.SecurityRule(
            metadata=ResourceMetadata(id="foreign", parent_id="group-a", name="foreign")
        )
    elif change == "attachment":
        instances.append(
            NS(
                metadata=NS(id="foreign-vm"),
                spec=NS(network_interfaces=[NS(security_groups=[NS(id="group-a")])]),
            )
        )
    elif change == "rule-policy":
        rules["rule-a"].spec.priority = 2
    elif change == "group-network":
        groups["group-a"].spec.network_id = "foreign"
    else:
        groups["group-a"].status.default = True
    with pytest.raises(RuntimeError, match="foreign|attached|identity or policy"):
        destroy_repair.cleanup_quarantine(cloud, "project-a")
    assert not events


def test_destroy_quarantine_does_not_treat_permission_error_as_absence(quarantine, monkeypatch):
    cloud, _, _, _, events, _ = quarantine

    def denied(*_a, **_k):
        raise PermissionError("not authorized")

    monkeypatch.setattr(vpc, "SecurityGroupServiceClient", lambda _: NS(get=denied))
    with pytest.raises(PermissionError):
        destroy_repair.cleanup_quarantine(cloud, "project-a")
    assert not events


def test_destroy_quarantine_resumes_after_rule_deleted_before_group(quarantine, monkeypatch):
    cloud, groups, rules, _, events, _ = quarantine
    original = cloud.mutate

    def interrupted(label, request, submit, **kwargs):
        if isinstance(request, vpc.DeleteSecurityGroupRequest):
            raise OSError("interrupted cleanup")
        return original(label, request, submit, **kwargs)

    monkeypatch.setattr(cloud, "mutate", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        destroy_repair.cleanup_quarantine(cloud, "project-a")
    assert not rules and groups and events == ["rule-a"]
    monkeypatch.setattr(cloud, "mutate", original)
    destroy_repair.cleanup_quarantine(cloud, "project-a")
    assert not groups and events == ["rule-a", "group-a"]
