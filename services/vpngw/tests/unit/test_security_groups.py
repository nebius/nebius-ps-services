from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from nebius.api.nebius.vpc import v1

from nebius_vpngw.config_loader import merge_with_peer_configs
from nebius_vpngw.deploy import security_groups as sg
from nebius_vpngw.schema import GatewayGroup


def group(identifier, network="network-a", default=False):
    return NS(
        metadata=NS(id=identifier),
        spec=NS(network_id=network),
        status=NS(default=default, state=NS(name="READY")),
    )


@pytest.fixture
def cloud(monkeypatch):
    inventory = {"sg-a": group("sg-a"), "sg-default": group("sg-default", default=True)}
    service = NS(
        get=lambda request: NS(wait=lambda: inventory[request.id]),
        list_by_network=lambda request: NS(
            wait=lambda: NS(items=list(inventory.values()), next_page_token="")
        ),
        create=Mock(side_effect=AssertionError("Group mutation is forbidden")),
        update=Mock(side_effect=AssertionError("Group mutation is forbidden")),
        delete=Mock(side_effect=AssertionError("Group mutation is forbidden")),
    )
    subnet = NS(metadata=NS(id="subnet-a"), spec=NS(network_id="network-a"))
    monkeypatch.setattr(v1, "SecurityGroupServiceClient", lambda client: service)
    monkeypatch.setattr(
        v1, "SubnetServiceClient", lambda client: NS(get=lambda request: NS(wait=lambda: subnet))
    )
    return inventory


def instance(*ids):
    return NS(
        spec=NS(
            network_interfaces=[
                NS(
                    name="eth0",
                    subnet_id="subnet-a",
                    security_groups=[NS(id=value) for value in ids],
                )
            ]
        )
    )


@pytest.mark.parametrize("explicit,effective", [((), ("sg-default",)), (("sg-a",), ("sg-a",))])
def test_live_membership_freezes_effective_default_and_explicit_groups(cloud, explicit, effective):
    snapshot = sg.capture(None, instance(*explicit))
    assert snapshot[0].explicit_ids == explicit
    assert snapshot[0].create_ids == effective
    sg.require_unchanged(None, instance(*explicit), snapshot)
    sg.verify_candidate(None, instance(*effective), snapshot)
    with pytest.raises(RuntimeError, match="changed"):
        sg.require_unchanged(None, instance("sg-a" if not explicit else "sg-default"), snapshot)
    with pytest.raises(RuntimeError, match="differ"):
        sg.verify_candidate(None, instance("sg-a" if not explicit else "sg-default"), snapshot)


def test_default_change_cannot_silently_change_replacement(cloud):
    snapshot = sg.capture(None, instance())
    cloud["sg-default"].status.default = False
    cloud["sg-a"].status.default = True
    with pytest.raises(RuntimeError, match="changed"):
        sg.require_unchanged(None, instance(), snapshot)
    sg.verify_candidate(None, instance("sg-default"), snapshot)


def test_missing_ambiguous_or_foreign_group_blocks_capture(cloud):
    cloud["sg-a"].status.default = True
    with pytest.raises(ValueError, match="unique default"):
        sg.capture(None, instance())
    cloud["sg-a"].spec.network_id = "network-b"
    with pytest.raises(ValueError, match="different VPC"):
        sg.capture(None, instance("sg-a"))


def test_initial_customer_groups_are_creation_intent_only(sample_config):
    sample_config["gateway_group"]["initial_security_group_ids"] = ["sg-a"]
    plan = merge_with_peer_configs(sample_config, [])
    assert plan.gateway_group.initial_security_group_ids == ("sg-a",)
    assert all("initial_security_group_ids" not in item.config_yaml for item in plan.per_instance)
    for bad in (None, "sg-a", ["sg-a", "sg-a"], ["bad id"]):
        candidate = dict(sample_config["gateway_group"], initial_security_group_ids=bad)
        with pytest.raises(ValueError):
            GatewayGroup.model_validate(candidate)
