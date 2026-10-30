"""Admission regressions using an offline Terraform 1.17 / Nebius 0.6.75 plan."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import nat_gateway_activation_inputs as activation_inputs
from nebius_cxcli.component_sources import ComponentDefault
from nebius_cxcli.nat_gateway_activation_inputs import SubnetActivationContract
from nebius_cxcli.nat_gateway_deploy import NatDeployment
from nebius_cxcli.nat_gateway_state import assert_activation_plan


@pytest.fixture
def route_plan():
    return json.loads((Path(__file__).parent / "fixtures/nat_activation_plan.json").read_text())


def admit(plan, *, inherited=True):
    assert_activation_plan(
        plan,
        {"subnet-fixture": "table-new"},
        subnet_contracts={
            "subnet-fixture": SubnetActivationContract(
                plan["resource_changes"][0]["address"], inherited
            )
        },
    )


def test_real_provider_route_only_plan_is_admitted(route_plan):
    admit(route_plan)


def test_inherited_public_pools_need_explicit_configuration_evidence(route_plan):
    with pytest.raises(RuntimeError, match="more than"):
        assert_activation_plan(route_plan, {"subnet-fixture": "table-new"})


@pytest.mark.parametrize(
    "path,value",
    [
        (("id",), "other"),
        (("parent_id",), "other"),
        (("network_id",), "other"),
        (("name",), "other"),
        (("labels",), {"new": "label"}),
        (("metadata",), {"new": "field"}),
        (("new_provider_field",), "unexpected"),
        (("ipv4_private_pools", "use_network_pools"), True),
        (("ipv4_private_pools", "pools", 0, "cidrs", 0, "cidr"), "198.51.100.0/24"),
        (("ipv4_private_pools", "pools", 0, "pool_id"), "other-pool"),
        (("ipv4_private_pools", "pools"), []),
    ],
)
def test_unrelated_changes_are_rejected(route_plan, path, value):
    after = route_plan["resource_changes"][0]["change"]["after"]
    for key in path[:-1]:
        after = after[key]
    after[path[-1]] = value
    with pytest.raises(RuntimeError, match="NAT activation plan"):
        admit(route_plan)


@pytest.mark.parametrize(
    "path",
    [
        ("id",),
        ("parent_id",),
        ("network_id",),
        ("name",),
        ("labels",),
        ("new_provider_field",),
        ("ipv4_private_pools",),
        ("ipv4_private_pools", "use_network_pools"),
        ("ipv4_private_pools", "pools"),
        ("ipv4_private_pools", "pools", 0, "cidrs"),
        ("ipv4_private_pools", "pools", 0, "cidrs", 0, "cidr"),
    ],
)
def test_unknown_authored_values_are_rejected_even_if_after_is_unchanged(route_plan, path):
    unknown = route_plan["resource_changes"][0]["change"]["after_unknown"]
    for key in path[:-1]:
        unknown = unknown[key]
    unknown[path[-1]] = True
    with pytest.raises(RuntimeError, match="NAT activation plan"):
        admit(route_plan)


def test_configured_public_pools_are_checked_recursively(route_plan):
    change = route_plan["resource_changes"][0]["change"]
    for field in ("before", "after", "after_unknown"):
        change[field]["ipv4_public_pools"] = copy.deepcopy(change[field]["ipv4_private_pools"])
    admit(route_plan, inherited=False)
    change["after_unknown"]["ipv4_public_pools"]["use_network_pools"] = True
    with pytest.raises(RuntimeError, match="more than"):
        admit(route_plan)


@pytest.mark.parametrize("actions", [["create"], ["delete"], ["delete", "create"]])
def test_non_update_actions_stay_blocked(route_plan, actions):
    route_plan["resource_changes"][0]["change"]["actions"] = actions
    with pytest.raises(RuntimeError, match="unapproved"):
        admit(route_plan)


@pytest.mark.parametrize("route", [None, "different-table"])
def test_wrong_route_stays_blocked(route_plan, route):
    route_plan["resource_changes"][0]["change"]["after"]["route_table_id"] = route
    with pytest.raises(RuntimeError, match="unapproved"):
        admit(route_plan)


def route_manager(plan, subnet):
    events = []
    target = {
        "owner": "terraform",
        "vpc_instance": "network",
        "subnet_key": "work",
        "subnet_id": "subnet-fixture",
    }
    config = {
        "infra": {
            "components": [
                {"id": "vpc", "instance_id": "network", "inputs": {"subnets": {"work": subnet}}}
            ]
        }
    }
    plan["resource_changes"][0]["address"] = 'module.network.nebius_vpc_v1_subnet.this["work"]'
    executor = NS(
        config=config,
        observation_plan=plan,
        runtime_env={},
        manifest={
            "render": {
                "source_profile": "portable",
                "module_sources": [
                    {
                        "component_id": "vpc",
                        "instance_id": "network",
                        "module_name": "network",
                        "source": "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/vpc?ref=main",
                    }
                ],
            }
        },
        paths=NS(infra_dir="fixture"),
        lease=NS(assert_held=lambda: None),
        cli=NS(
            _sha256_file=lambda path: "digest",
            _run_terraform_apply_with_status=lambda *args, **kwargs: events.append("apply"),
        ),
        _terraform_plan=lambda **kwargs: (Path("route.tfplan"), {}),
    )
    manager = NatDeployment(executor)
    manager.prepared = True
    manager.rows = []
    manager.state = NS(
        value={"gateways": {"nat": {"targets": {"work": target}}}},
        save=lambda: events.append("save"),
        lock_etag="held",
        assert_held=lambda: None,
    )
    return manager, events


@pytest.mark.parametrize(
    "subnet,allowed",
    [
        ({}, True),
        ({"use_network_public_pools": True, "ipv4_public_cidrs": [" "]}, True),
        ({"use_network_public_pools": False}, False),
        ({"ipv4_public_cidrs": ["198.51.100.0/24"]}, False),
        ({"ipv4_public_cidrs": None}, True),
        ({"use_network_public_pools": None, "ipv4_public_cidrs": None}, True),
    ],
)
def test_route_apply_uses_pool_intent_and_admits_before_mutation(route_plan, subnet, allowed):
    manager, events = route_manager(route_plan, subnet)
    if allowed:
        manager._apply_routes({"subnet-fixture": "table-new"}, "Activate")
        assert events == ["save", "apply"]
    else:
        with pytest.raises(RuntimeError, match="more than"):
            manager._apply_routes({"subnet-fixture": "table-new"}, "Activate")
        assert events == []
    assert manager.activating is False


def test_route_apply_still_blocks_cidr_edits(route_plan):
    manager, events = route_manager(route_plan, {})
    route_plan["resource_changes"][0]["change"]["after"]["ipv4_private_pools"]["pools"] = []
    with pytest.raises(RuntimeError, match="more than"):
        manager._apply_routes({"subnet-fixture": "table-new"}, "Activate")
    assert events == []


def test_custom_module_receives_no_computed_pool_exemptions(route_plan):
    manager, events = route_manager(route_plan, {})
    manager.executor.manifest["render"]["module_sources"][0]["source"] = "./custom-vpc"
    with pytest.raises(RuntimeError, match="more than"):
        manager._apply_routes({"subnet-fixture": "table-new"}, "Activate")
    assert events == []


def test_computed_exemptions_are_bound_to_exact_resource_address(route_plan):
    manager, events = route_manager(route_plan, {})
    route_plan["resource_changes"][0]["address"] = "module.custom.nebius_vpc_v1_subnet.other"
    with pytest.raises(RuntimeError, match="more than"):
        manager._apply_routes({"subnet-fixture": "table-new"}, "Activate")
    assert events == []


def test_catalog_defaults_use_the_rendered_pool_selection(monkeypatch, route_plan):
    from dataclasses import replace

    manager, events = route_manager(route_plan, {})
    entry = activation_inputs.component_lookup("infra")["vpc"]
    entry = replace(
        entry,
        defaults=(ComponentDefault("inputs.subnets.work.use_network_public_pools", False),),
    )
    monkeypatch.setattr(activation_inputs, "component_lookup", lambda *a, **kw: {"vpc": entry})
    with pytest.raises(RuntimeError, match="more than"):
        manager._apply_routes({"subnet-fixture": "table-new"}, "Activate")
    assert events == []


def test_bound_subnet_inputs_receive_no_computed_pool_exemptions(route_plan):
    manager, events = route_manager(route_plan, {})
    manager.executor.config["infra"]["components"][0]["bindings"] = {
        "inputs.subnets": {"source_component": "vpc", "source_output": "subnets"}
    }
    with pytest.raises(RuntimeError, match="more than"):
        manager._apply_routes({"subnet-fixture": "table-new"}, "Activate")
    assert events == []


def test_local_canonical_module_is_supported(route_plan):
    manager, events = route_manager(route_plan, {})
    module = (
        activation_inputs.DEFAULT_COMPONENT_SOURCES_FILE.parent / "../../platform-infra/modules/vpc"
    )
    manager.executor.manifest["render"]["source_profile"] = "local"
    manager.executor.manifest["render"]["module_sources"][0]["source"] = module.resolve().as_uri()
    manager._apply_routes({"subnet-fixture": "table-new"}, "Activate")
    assert events == ["save", "apply"]
