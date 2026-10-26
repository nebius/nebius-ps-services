from __future__ import annotations

import copy
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import nat_gateway_deploy as deployment
from nebius_cxcli.nat_gateway import (
    canonical_routes,
    policy_snapshot,
    terraform_policies,
    validate_nat_config,
)
from nebius_cxcli.nat_gateway_sdk import NatGatewaySDK
from nebius_cxcli.nat_gateway_state import NatState, assert_activation_plan, assert_quota_resolved
from nebius_cxcli.object_storage_errors import ObjectStorageError
from nebius_cxcli.object_storage_transport import ObjectStorageResponse
from nebius_cxcli.quota_checks import AggregatedQuotaRequirement, QuotaRecord, _evaluate_requirement


def config(*, owner="external", enabled=True):
    target = {
        "owner": "external",
        "subnet_id": "subnet-work",
        "policy": "default",
        "delegated": True,
    }
    if owner == "terraform":
        target = {
            "owner": owner,
            "vpc_instance": "network",
            "subnet_key": "work",
            "policy": "default",
        }
    row = {
        "id": "nat-gateway",
        "instance_id": "nat",
        "enabled": True,
        "inputs": {
            "parent_id": "project-test",
            "network_id": "network-test",
            "name": "nat",
            "platform": "cpu-d3",
            "preset": "4vcpu-16gb",
            "source_image_id": "image-test",
            "boot_disk_size_gib": 20,
            "boot_disk_type": "NETWORK_SSD",
            "ssh_user_name": "ubuntu",
            "ssh_public_key": "ssh-ed25519 public-test",
            "admin_cidrs": ["192.0.2.0/24"],
            "source_cidrs": ["10.0.0.0/8"],
            "gateway_subnet_cidr": "/28",
        },
        "routing": {
            "version": 1,
            "enabled": enabled,
            "targets": [target],
            "policies": {
                "default": policy_snapshot(
                    "table-original",
                    [
                        {"destination_cidr": "0.0.0.0/0", "default_egress_gateway": True},
                        {
                            "destination_cidr": "10.200.0.0/16",
                            "allocation_id": "allocation-exception",
                        },
                    ],
                )
            },
        },
    }
    vpc = {
        "id": "vpc",
        "instance_id": "network",
        "enabled": True,
        "inputs": {
            "parent_id": "project-test",
            "network": {"existing_id": "network-test"},
            "subnets": {"work": {"name": "work", "route_table_id": "table-original"}},
        },
    }
    return {
        "client_info": {
            "client_name": "example",
            "nebius": {
                "project_id": "project-test",
                "tenant_id": "tenant-test",
                "region_id": "eu-north1",
            },
        },
        "infra": {"components": [row, vpc] if owner == "terraform" else [row]},
        "apps": {"charts": []},
    }


def test_preserves_custom_routes_and_does_not_mutate_original():
    payload = config()
    original = copy.deepcopy(payload)
    validate_nat_config(payload)
    assert terraform_policies(payload["infra"]["components"][0]) == {
        "default": [{"destination_cidr": "10.200.0.0/16", "allocation_id": "allocation-exception"}]
    }
    assert payload == original


@pytest.mark.parametrize(
    "routes",
    [
        [{"destination_cidr": "::/0", "allocation_id": "a"}],
        [{"destination_cidr": "0.0.0.0/0", "allocation_id": "a", "type": "REDISTRIBUTED"}],
        [{"destination_cidr": "10.0.0.0/8", "default_egress_gateway": True}],
        [{"destination_cidr": "0.0.0.0/0", "allocation_id": "a", "default_egress_gateway": True}],
        [{"destination_cidr": "0.0.0.0/0"}],
        [{"destination_cidr": "10.0.0.0/8", "allocation_id": "a"}] * 2,
    ],
)
def test_rejects_unrepresentable_routes(routes):
    with pytest.raises(ValueError):
        canonical_routes(routes)


def test_duplicate_owner_and_missing_delegation_fail():
    payload = config()
    payload["infra"]["components"][0]["routing"]["targets"][0]["delegated"] = False
    with pytest.raises(ValueError, match="delegation"):
        validate_nat_config(payload)
    payload = config()
    second = copy.deepcopy(payload["infra"]["components"][0])
    second["instance_id"] = "other"
    payload["infra"]["components"].append(second)
    with pytest.raises(ValueError, match="multiple NAT owners"):
        validate_nat_config(payload)


def test_same_run_network_binding_and_no_consumer_reference_in_nat_module(monkeypatch):
    from nebius_cxcli import component_sources
    from nebius_cxcli.component_sources import (
        ComponentOutput,
        SourceProfile,
        reset_component_sources_cache,
    )
    from nebius_cxcli.components import reset_component_entry_cache
    from nebius_cxcli.infra_render import _build_module_plans, _render_module_block

    monkeypatch.setattr(
        component_sources,
        "_discover_terraform_outputs",
        lambda source: tuple(
            ComponentOutput(name=name, kind="terraform_output", source_path=name)
            for name in ("network_id", "subnet_ids")
            if "/vpc" in source
        ),
    )
    reset_component_sources_cache()
    reset_component_entry_cache()
    payload = config(owner="terraform")
    nat, vpc = payload["infra"]["components"]
    nat["inputs"].pop("network_id")
    nat["bindings"] = {
        "inputs.network_id": {
            "source_component": "vpc",
            "source_instance": "network",
            "source_output": "network_id",
        }
    }
    vpc["inputs"]["network"] = {"name": "network", "ipv4_private_cidrs": ["10.0.0.0/8"]}
    validate_nat_config(payload)
    plans = _build_module_plans(payload, source_profile=SourceProfile.LOCAL)
    rendered = {p.instance_id: _render_module_block(p) for p in plans}
    assert "network_id = module.network.network_id" in rendered["nat"]
    assert "module.network.subnet" not in rendered["nat"]
    assert "cxcli_nat_route_overrides" in rendered["network"]
    assert 'module.nat.route_table_ids["default"]' in rendered["network"]


@pytest.mark.parametrize(
    "usage_state,usage,state,expected",
    [
        ("USAGE_STATE_USED", 2, "STATE_ACTIVE", 8),
        ("USAGE_STATE_NOT_USED", 0, "STATE_ACTIVE", 10),
        ("USAGE_STATE_UNKNOWN", 0, "STATE_ACTIVE", None),
        ("USAGE_STATE_NOT_APPLICABLE", 0, "STATE_ACTIVE", None),
        ("USAGE_STATE_UNSPECIFIED", 0, "STATE_ACTIVE", None),
        ("USAGE_STATE_USED", None, "STATE_ACTIVE", None),
        ("USAGE_STATE_USED", 0, "STATE_UNSPECIFIED", None),
    ],
)
def test_quota_requires_active_known_usage(usage_state, usage, state, expected):
    record = QuotaRecord(
        "compute.instance.count",
        "eu-north1",
        10,
        usage,
        "compute",
        "VM count",
        "",
        state,
        usage_state,
        "",
    )
    requirement = AggregatedQuotaRequirement(
        "nat-gateway", "nat", "nat", record.name, record.region, 1, "new gateway"
    )
    check = _evaluate_requirement(
        requirement, tenant_quotas={(record.name, record.region): record}, project_quotas={}
    )
    assert check.available == expected


def test_live_quota_identifier_discovery_is_unambiguous():
    record = QuotaRecord(
        "vpc.routing_table.count",
        "eu-north1",
        10,
        0,
        "vpc",
        "Route tables",
        "",
        "STATE_ACTIVE",
        "USAGE_STATE_NOT_USED",
        "",
    )
    requirement = AggregatedQuotaRequirement(
        "nat-gateway", "nat", "nat", "vpc.route-table.count", record.region, 2, "new routing"
    )
    records = {(record.name, record.region): record}
    check = _evaluate_requirement(requirement, tenant_quotas=records, project_quotas={})
    assert check.quota_name == record.name and check.available == 10
    records[("vpc.other.count", record.region)] = replace(record, name="vpc.other.count")
    assert (
        _evaluate_requirement(requirement, tenant_quotas=records, project_quotas={}).available
        is None
    )


def test_unknown_quota_blocks_and_headroom_is_required():
    for field in ("errors", "coverage_gaps", "unknown_checks", "has_confirmed_insufficiency"):
        report = dict(
            errors=(), coverage_gaps=(), unknown_checks=(), has_confirmed_insufficiency=False
        )
        report[field] = ("unknown",)
        with pytest.raises(RuntimeError, match="confirmed live quota"):
            assert_quota_resolved(NS(**report))


class MemoryTransport:
    def __init__(self):
        self.objects = {}
        self.version = 0
        self.lose_ack = False

    def request(self, operation, key, *, body=b"", etag=None, create_only=False, **kwargs):
        if operation == "get_object":
            if key not in self.objects:
                raise ObjectStorageError("NoSuchKey")
            return self.objects[key]
        if (create_only and key in self.objects) or (
            etag and (key not in self.objects or self.objects[key].etag != etag)
        ):
            raise ObjectStorageError("PreconditionFailed")
        if operation == "delete_object":
            del self.objects[key]
            return ObjectStorageResponse()
        self.version += 1
        response = ObjectStorageResponse(str(self.version), body=body)
        self.objects[key] = response
        if self.lose_ack:
            self.lose_ack = False
            raise ObjectStorageError("transport read timeout")
        return response


def test_cross_host_lock_and_conditional_journal_lost_ack():
    transport = MemoryTransport()
    first = NatState(transport, "terraform.tfstate")
    second = NatState(transport, "terraform.tfstate")
    transport.lose_ack = True
    first.acquire()
    with pytest.raises(RuntimeError, match="NAT shared lock"):
        second.acquire()
    first.value["gateways"]["nat"] = {"phase": "prepared"}
    transport.lose_ack = True
    first.save()
    first.release()
    second.acquire()
    assert second.value["gateways"]["nat"]["phase"] == "prepared"
    second.release()


def test_dry_run_never_writes_or_unlocks():
    transport = MemoryTransport()
    state = NatState(transport, "state", readonly=True)
    state.acquire()
    state.save()
    state.release()
    assert transport.objects == {}
    with pytest.raises(RuntimeError, match="shared execution authority"):
        state.assert_held()


def test_unknown_bucket_is_not_an_empty_journal():
    transport = NS(
        request=lambda *a, **kw: (_ for _ in ()).throw(ObjectStorageError("NoSuchBucket"))
    )
    with pytest.raises(ObjectStorageError):
        NatState(transport, "state")


def activation_change(**changes):
    before = {"id": "subnet-work", "route_table_id": "old", "name": "work", "status": {}}
    return {
        "resource_changes": [
            {
                "type": "nebius_vpc_v1_subnet",
                "change": {
                    "actions": ["update"],
                    "before": before,
                    "after": {**before, "route_table_id": "new", **changes},
                },
            }
        ]
    }


def test_activation_admission_rejects_other_changes():
    assert_activation_plan(activation_change(), {"subnet-work": "new"})
    with pytest.raises(RuntimeError, match="more than"):
        assert_activation_plan(activation_change(name="other"), {"subnet-work": "new"})
    with pytest.raises(RuntimeError, match="unapproved"):
        assert_activation_plan(activation_change(), {"subnet-unselected": "new"})


def test_non_nat_null_labels_do_not_change_deployment_behavior():
    manager = deployment.NatDeployment(NS())
    manager.check_plan(
        {"resource_changes": [{"change": {"before": {"labels": None}, "actions": ["no-op"]}}]}
    )
    assert manager.state is None


@pytest.mark.parametrize("configured", [True, False])
def test_direct_mutations_cannot_bypass_nat_lifecycle(configured):
    payload = config()
    if not configured:
        payload["infra"]["components"] = []
    cli = NS(
        terraform_show_json=lambda *a, **kw: {},
        _terraform_state_resources=lambda raw: [
            {"values": {"labels": {"cxcli-component": "nat-gateway"}}}
        ],
    )
    with pytest.raises(RuntimeError, match="NAT"):
        deployment.guard_direct_mutation(cli, payload, NS(infra_dir="fixture"), {}, initialize=True)


def manager_fixture(monkeypatch, *, current="table-original", enabled=True, owner="external"):
    payload = config(owner=owner, enabled=enabled)
    row = payload["infra"]["components"][0]
    target = {
        **row["routing"]["targets"][0],
        "subnet_id": "subnet-work",
        "original": "table-original",
        "installed": "",
        "pending": None,
    }
    outputs = {
        "instance_id": "instance-test",
        "subnet_id": "subnet-gateway",
        "private_allocation_id": "allocation-nat",
        "route_table_ids": {"default": "table-nat"},
        "guest_config_sha256": "digest",
    }
    record = {
        "phase": "prepared",
        "module_name": "nat",
        "intent": "test",
        "project_id": "project-test",
        "network_id": "network-test",
        "targets": {"target": target},
        "outputs": outputs,
    }
    transport = MemoryTransport()
    state = NatState(transport, "state")
    state.acquire()
    state.value["gateways"]["nat"] = record
    state.save()
    effects = []
    live = {"route": current}

    class SDK:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def subnet(self, *args):
            return NS(
                spec=NS(route_table_id=live["route"]), status=NS(route_table=NS(id=live["route"]))
            )

        def network_cidrs(self, *args):
            return ["10.0.0.0/8"]

        def network(self, *args):
            return NS(spec=NS(ipv4_private_pools=NS(pools=[NS(id="pool")])))

        def address_pool(self, identity, **kwargs):
            from nebius_cxcli.nat_gateway_address_space import AddressBlock, AddressPool, IPv4

            return AddressPool(identity, (AddressBlock(IPv4("10.0.0.0/8")),))

        def check_source_policy(self, *args):
            effects.append("source")

        def workload_policy(self, *args):
            effects.append("policy")

        def gateway(self, *args):
            effects.append("gateway")

        def wait_operation(self, *args):
            effects.append("wait")

        def subnets(self, *args):
            return []

        def attach(self, *args, desired, record_operation, **kwargs):
            assert target["pending"]["desired"] == desired
            record_operation("operation-test")
            effects.append("attach")
            live["route"] = desired

    monkeypatch.setattr(deployment, "NatGatewaySDK", SDK)
    executor = NS(
        config=payload,
        paths=NS(infra_dir="fixture"),
        cli=NS(
            terraform_show_json=lambda *a, **kw: {},
            _terraform_state_resources=lambda raw: [],
            progress_console=NS(print=lambda *a, **kw: None),
        ),
        manifest={
            "render": {
                "module_sources": [
                    {"component_id": "nat-gateway", "instance_id": "nat", "module_name": "nat"},
                    {"component_id": "vpc", "instance_id": "network", "module_name": "network"},
                ]
            }
        },
        runtime_env={},
        options=NS(dry_run=False, ssh_known_hosts_file=None, ssh_private_key=None),
        lease=NS(assert_held=lambda: None),
        report_paths=NS(generated_dir=None),
    )
    manager = deployment.NatDeployment(executor)
    manager.rows = [row]
    manager.state = state
    manager.outputs = lambda: {
        **{f"nat_{key}": {"value": value} for key, value in outputs.items()},
        "network_subnet_ids": {"value": {"work": "subnet-work"}},
    }
    monkeypatch.setattr(deployment, "resolve_ssh_known_hosts_file", lambda *args: "trusted")
    monkeypatch.setattr(deployment, "verify_guest", lambda *args, **kwargs: effects.append("guest"))

    def plan(**kwargs):
        effects.append("plan")
        executor.observation_plan = {"resource_changes": []}
        return None, {}

    executor._terraform_plan = plan
    return manager, SDK(), record, target, live, effects


def test_no_attachment_before_guest_readiness_and_plan(monkeypatch):
    manager, _sdk, record, target, live, effects = manager_fixture(monkeypatch)
    manager.complete()
    assert (
        effects.index("policy")
        < effects.index("guest")
        < effects.index("plan")
        < effects.index("attach")
    )
    assert (
        live["route"] == "table-nat" and target["pending"] is None and record["phase"] == "active"
    )


def test_readiness_failure_leaves_original_route(monkeypatch):
    manager, _sdk, _record, target, live, effects = manager_fixture(monkeypatch)

    def fail(*args, **kwargs):
        raise RuntimeError("untrusted host")

    monkeypatch.setattr(deployment, "verify_guest", fail)
    with pytest.raises(RuntimeError, match="untrusted"):
        manager.complete()
    assert (
        live["route"] == "table-original" and "attach" not in effects and target["pending"] is None
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_attachment_drift_blocks_before_apply_including_restore(monkeypatch, enabled):
    manager, _sdk, _record, _target, _live, _effects = manager_fixture(
        monkeypatch, current="table-third-party", enabled=enabled, owner="terraform"
    )
    with pytest.raises(RuntimeError, match="refusing overwrite"):
        manager.assert_attachments()


def test_ambiguous_sdk_submission_is_never_replayed(monkeypatch):
    manager, sdk, record, target, _live, effects = manager_fixture(monkeypatch)
    target["pending"] = {"desired": "table-nat", "expected": "table-original", "operation_id": ""}
    with pytest.raises(RuntimeError, match="unknown submission"):
        manager._attach(sdk, record, target, "table-nat")
    assert "attach" not in effects


def test_known_operation_resumes_from_terminal_live_attachment(monkeypatch):
    manager, sdk, record, target, _live, effects = manager_fixture(monkeypatch, current="table-nat")
    target["pending"] = {
        "desired": "table-nat",
        "expected": "table-original",
        "operation_id": "operation-test",
    }
    manager._attach(sdk, record, target, "table-nat")
    assert effects == ["wait"] and target["installed"] == "table-nat" and target["pending"] is None


def test_restore_does_not_require_stale_source_routes_or_ssh(monkeypatch):
    manager, _sdk, record, target, live, effects = manager_fixture(
        monkeypatch, current="table-nat", enabled=False
    )
    target["installed"] = "table-nat"
    intent = copy.deepcopy(manager.rows[0])
    intent["routing"].pop("enabled")
    record["intent"] = deployment.fingerprint(intent)
    # The SDK fixture intentionally has no CPU/image/subnet allocation methods.
    # These are provisioning checks and must not block emergency restoration.
    monkeypatch.setattr(
        deployment.NatGatewaySDK, "network_cidrs", lambda *a: ["10.0.0.0/8", "172.16.0.0/12"]
    )
    manager.prepare()
    manager.complete()
    assert live["route"] == "table-original" and record["phase"] == "detached"
    assert "source" not in effects and "guest" not in effects


def test_terraform_detach_uses_historical_route_despite_changed_vpc_input(monkeypatch):
    from nebius_cxcli import nat_gateway_quota
    from nebius_cxcli.component_sources import SourceProfile
    from nebius_cxcli.infra_render import _build_module_plans, _render_module_block
    from nebius_cxcli.nat_gateway import target_key

    manager, _sdk, record, target, live, _effects = manager_fixture(
        monkeypatch, current="table-nat", enabled=False, owner="terraform"
    )
    row = manager.rows[0]
    intent = copy.deepcopy(row)
    intent["routing"].pop("enabled")
    record["intent"] = deployment.fingerprint(intent)
    target["installed"] = "table-nat"
    record["targets"] = {target_key(target): target}
    manager.executor.config["infra"]["components"][1]["inputs"]["subnets"]["work"][
        "route_table_id"
    ] = "table-new-authored"
    manager.prepare()
    assert manager.overrides == {"network:work": "table-original"}
    plans = _build_module_plans(manager.executor.config, source_profile=SourceProfile.LOCAL)
    vpc = next(plan for plan in plans if plan.instance_id == "network")
    assert (
        'lookup(var.cxcli_nat_route_overrides, "network:work", "table-new-authored")'
        in _render_module_block(vpc)
    )
    monkeypatch.setattr(nat_gateway_quota, "check_plan_quotas", lambda *a: None)
    bad_plan = activation_change()
    with pytest.raises(RuntimeError, match="recorded original"):
        manager.check_plan(bad_plan)
    assert target["pending"] is None and live["route"] == "table-nat"
    good_plan = activation_change()
    good_plan["resource_changes"][0]["change"]["after"]["route_table_id"] = "table-original"
    manager.check_plan(good_plan)
    assert target["pending"]["desired"] == "table-original"


def test_last_component_retirement_survives_config_removal(monkeypatch):
    manager, _sdk, record, _target, _live, _effects = manager_fixture(monkeypatch)
    record["phase"] = "detached"
    manager.executor.config["infra"]["components"] = []
    manager.executor.paths = NS(infra_dir="fixture")
    manager.executor.cli = NS(
        terraform_show_json=lambda *a, **kw: {}, _terraform_state_resources=lambda raw: []
    )
    with manager:
        assert manager.retirement_required is True
        manager.prepare()
        manager.complete()
        assert record["phase"] == "retired" and record["outputs"] == {}


def test_last_component_removal_cannot_bypass_detach(monkeypatch):
    manager, _sdk, record, _target, _live, _effects = manager_fixture(monkeypatch)
    record["phase"] = "active"
    manager.executor.config["infra"]["components"] = []
    with pytest.raises(RuntimeError, match="not restored"), manager:
        pytest.fail("Active NAT removal was admitted")


def test_sdk_real_oneofs_and_routes_are_normalized(monkeypatch):
    import nebius.api.nebius.vpc.v1 as vpc
    from nebius.api.nebius.common.v1 import ResourceMetadata

    sdk = NatGatewaySDK("project-test", sdk=object())
    table = vpc.RouteTable(
        metadata=ResourceMetadata(id="table", parent_id="project-test"),
        spec=vpc.RouteTableSpec(network_id="network"),
    )
    routes = [
        vpc.Route(
            metadata=ResourceMetadata(id="route", parent_id="table"),
            spec=vpc.RouteSpec(
                destination=vpc.DestinationMatch(cidr="0.0.0.0/0"),
                next_hop=vpc.NextHop(default_egress_gateway=True),
            ),
            status=vpc.RouteStatus(type=vpc.RouteStatus.Type.STATIC),
        )
    ]
    monkeypatch.setattr(vpc, "RouteTableServiceClient", lambda _: NS(get="get"))
    monkeypatch.setattr(vpc, "RouteServiceClient", lambda _: NS(list="list"))
    monkeypatch.setattr(sdk, "call", lambda method, request: table)
    monkeypatch.setattr(sdk, "pages", lambda *a: routes)
    assert sdk.route_policy("table", "network")["routes"][0]["default_egress_gateway"] is True


def test_inventory_repeated_tokens_fail_without_partial_inventory(monkeypatch):
    sdk = NatGatewaySDK("project-test", sdk=object())
    monkeypatch.setattr(sdk, "call", lambda *a: NS(items=[], next_page_token="repeat"))
    with pytest.raises(RuntimeError, match="Repeated"):
        sdk.pages(lambda: None, lambda token: token)
