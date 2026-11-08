from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.compute import v1 as compute
from nebius.api.nebius.vpc import v1 as vpc
from test_ordinary_route_discovery import scope as scope
from test_provisioning_journal import operation

from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
from nebius_vpngw.deploy.ordinary_route_command import _observe_plan
from nebius_vpngw.deploy.ordinary_route_plan import intent
from nebius_vpngw.deploy.provisioning_journal import ProvisioningJournal, requests_from_plan
from nebius_vpngw.deploy.provisioning_plan import Reference, Request
from nebius_vpngw.deploy.provisioning_routes import bind, prepare
from nebius_vpngw.deploy.replacement_cloud import Cloud


def provision():
    allocation = Request.freeze(
        "private",
        vpc.CreateAllocationRequest(
            metadata=ResourceMetadata(parent_id="project", name="private"),
            spec=vpc.AllocationSpec(
                ipv4_private=vpc.IPv4PrivateAllocationSpec(subnet_id="gateway-subnet")
            ),
        ),
    )
    instance = Request.freeze(
        "compute",
        compute.CreateInstanceRequest(
            metadata=ResourceMetadata(parent_id="project", name="gateway"),
            spec=compute.InstanceSpec(
                network_interfaces=[
                    compute.NetworkInterfaceSpec(
                        name="eth0",
                        subnet_id="gateway-subnet",
                        ip_address=compute.IPAddress(allocation_id="@private"),
                    )
                ]
            ),
        ),
        references=(
            Reference(
                "spec.network_interfaces.0.ip_address.allocation_id", "private", "allocation"
            ),
        ),
        guest="gateway",
    )
    return {
        "project_id": "project",
        "gateway_name": "gateway",
        "configuration_sha256": "a" * 64,
        "artifact_sha256": "b" * 64,
        "requests": [allocation.mapping(), instance.mapping()],
        "observed": {},
        "inventories": [],
        "bootstraps": {"gateway": {"template_sha256": "c" * 64, "trust_sha256": "d" * 64}},
    }


def test_route_infrastructure_is_inspectable_before_compute_exists(scope, monkeypatch):
    state, discovery = scope
    monkeypatch.setattr(discovery.manager, "inspect_ordinary_targets", lambda names: {})
    observation = discovery.observe_infrastructure()
    assert not observation["allocations"] and not observation["infrastructure"]["gateways"]
    assert observation["active_tables"] == ("workload-table",)
    frozen = prepare(discovery, provision())
    assert frozen["routes"]["allocations"] == {"0": "@private"}
    assert frozen["routes"]["infrastructure"]["gateways"]["0"]["compute_id"] == "@compute"
    assert not discovery.journal.root.exists()
    with pytest.raises(RuntimeError, match="planned gateway identities"):
        discovery.observe()


@pytest.mark.parametrize("damage", [None, "unverified", "different-routes", "different-execution"])
def test_route_derivation_requires_this_commands_successful_creations(scope, tmp_path, damage):
    _state, discovery = scope
    desired = provision()
    frozen = prepare(discovery, desired)
    envelope = CommandPlan(
        "project",
        "gateway",
        "a" * 64,
        "b" * 64,
        (
            Effect(
                "provisioning",
                "ordinary-provisioning",
                "create",
                ("gateway",),
                digest(desired),
                "e" * 64,
                True,
            ),
            Effect(
                "routes",
                "ordinary-provisioned-routes",
                "reconcile",
                ("gateway",),
                digest(frozen),
                "f" * 64,
                True,
                ("provisioning",),
            ),
        ),
    )
    child = ProvisioningJournal("project", "gateway", root=tmp_path / "provision")
    with CommandJournal("project", "gateway", root=tmp_path / "command") as command:
        command.begin(envelope, intents={"provisioning": desired, "routes": frozen})
        command.started("provisioning")
        with child:
            child.begin(
                desired,
                approval=envelope.approval,
                execution_id=(
                    "f" * 32
                    if damage == "different-execution"
                    else command.state["transaction"]["execution_id"]
                ),
            )
            cloud = Cloud(
                object(), child, operation_scope=child.state["transaction"]["execution_id"]
            )
            for recipe in requests_from_plan(desired):
                request = recipe.resolve(
                    child.state["bindings"], bootstrap="private-bootstrap" if recipe.guest else None
                )
                child.stage(recipe, request)
                identifier = "allocation" if recipe.key == "private" else "compute"
                cloud.mutate(
                    recipe.key,
                    request,
                    lambda *a, _id=identifier, **kw: NS(wait=lambda: operation(_id)),
                )
                child.verified(recipe, request)
            receipt = child.complete()
        if damage != "unverified":
            command.verified("provisioning", receipt)
        if damage == "different-routes":
            frozen = deepcopy(frozen)
            frozen["routes"]["active_tables"].append("unreviewed-table")
        before = (command.path.read_bytes(), child.path.read_bytes())
        if damage is not None:
            with pytest.raises(RuntimeError, match="successful provisioning"):
                bind(frozen, child, command)
        else:
            resolved = bind(frozen, child, command)
            observed, _ = _observe_plan(discovery, discovery.observe(), defer_bgp=True)
            assert resolved == intent(observed)
            assert resolved["allocations"] == {"0": "allocation"}
        assert before == (command.path.read_bytes(), child.path.read_bytes())
