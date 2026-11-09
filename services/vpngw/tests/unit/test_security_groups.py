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
        get=lambda request, **kwargs: NS(wait=lambda: inventory[request.id]),
        list_by_network=lambda request, **kwargs: NS(
            wait=lambda: NS(items=list(inventory.values()), next_page_token="")
        ),
        create=Mock(side_effect=AssertionError("Group mutation is forbidden")),
        update=Mock(side_effect=AssertionError("Group mutation is forbidden")),
        delete=Mock(side_effect=AssertionError("Group mutation is forbidden")),
    )
    subnet = NS(metadata=NS(id="subnet-a"), spec=NS(network_id="network-a"))
    monkeypatch.setattr(v1, "SecurityGroupServiceClient", lambda client: service)
    monkeypatch.setattr(
        v1,
        "SubnetServiceClient",
        lambda client: NS(get=lambda request, **kwargs: NS(wait=lambda: subnet)),
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
def test_live_membership_preserves_explicit_and_effective_default_groups(
    cloud, explicit, effective
):
    snapshot = sg.capture(None, instance(*explicit))
    assert snapshot[0].explicit_ids == explicit
    assert snapshot[0].create_ids == explicit
    sg.require_unchanged(None, instance(*explicit), snapshot)
    sg.verify_candidate(None, instance(*explicit), snapshot)
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
    with pytest.raises(RuntimeError, match="differ"):
        sg.verify_candidate(None, instance(), snapshot)


def test_missing_ambiguous_or_foreign_group_blocks_capture(cloud):
    cloud["sg-a"].status.default = True
    with pytest.raises(ValueError, match="unique default"):
        sg.capture(None, instance())
    cloud["sg-a"].spec.network_id = "network-b"
    with pytest.raises(ValueError, match="different VPC"):
        sg.capture(None, instance("sg-a"))


def test_customer_groups_are_desired_cloud_intent(sample_config):
    sample_config["gateway_group"]["security_group_ids"] = ["sg-a"]
    plan = merge_with_peer_configs(sample_config, [])
    assert plan.gateway_group.security_group_ids == ("sg-a",)
    assert all("security_group_ids" not in item.config_yaml for item in plan.per_instance)
    for bad in (None, "sg-a", ["sg-a", "sg-a"], ["bad id"]):
        candidate = dict(sample_config["gateway_group"], security_group_ids=bad)
        with pytest.raises(ValueError):
            GatewayGroup.model_validate(candidate)


def test_renamed_creation_field_is_rejected_before_effects(sample_config):
    value = dict(sample_config["gateway_group"], initial_security_group_ids=["sg-a"])
    with pytest.raises(ValueError, match="was renamed.*security_group_ids"):
        GatewayGroup.model_validate(value)


def test_multiple_desired_groups_are_sorted_and_default_is_not_pinned(sample_config, cloud):
    cloud["sg-b"] = group("sg-b")
    value = dict(sample_config["gateway_group"], security_group_ids=["sg-b", "sg-a"])
    assert GatewayGroup.model_validate(value).security_group_ids == ["sg-a", "sg-b"]
    wanted = sg.desired(None, instance("sg-a"), ())
    assert wanted[0].explicit_ids == ()
    assert wanted[0].effective_ids == ("sg-default",)
    assert sg.desired(None, instance(), ("sg-b", "sg-a"))[0].explicit_ids == ("sg-a", "sg-b")
    with pytest.raises(ValueError):
        GatewayGroup.model_validate(dict(value, security_group_ids=[f"sg-{n}" for n in range(9)]))
