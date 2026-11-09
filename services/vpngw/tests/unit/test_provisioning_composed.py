"""Exercise the resource graph with generated requests and server-side key replay."""

from types import SimpleNamespace

import pytest
from nebius.api.nebius.compute import v1 as compute_api
from nebius.api.nebius.vpc import v1 as vpc
from test_provisioning_execute import bootstrap, parent
from test_provisioning_journal import operation, plan
from test_provisioning_plan import compute, disk

from nebius_vpngw.deploy import provisioning_execute
from nebius_vpngw.deploy.provisioning_journal import ProvisioningJournal
from nebius_vpngw.deploy.provisioning_network import (
    create_allocation,
    create_default_route,
    create_subnet,
    create_table,
)
from nebius_vpngw.deploy.provisioning_plan import Reference, Request
from nebius_vpngw.deploy.replacement_cloud import message_digest
from nebius_vpngw.deploy.vm_ha_cloud import clone_nebius_sdk_message


def resource_plan():
    subnet = create_subnet("project-a", "gateway-subnet", "network-a", "192.0.2.0/24")
    subnet.spec.route_table_id = "@table"
    instance = compute().template()
    instance.spec.network_interfaces = [
        compute_api.NetworkInterfaceSpec(
            name="eth0",
            subnet_id="@subnet",
            ip_address=compute_api.IPAddress(allocation_id="@private"),
            public_ip_address=compute_api.PublicIPAddress(allocation_id="@public", static=True),
        )
    ]
    requests = [
        Request.freeze("table", create_table("project-a", "gateway-table", "network-a")),
        Request.freeze(
            "egress",
            create_default_route("@table"),
            references=(Reference("metadata.parent_id", "table", "table"),),
        ),
        Request.freeze(
            "subnet", subnet, references=(Reference("spec.route_table_id", "table", "table"),)
        ),
        disk(),
    ]
    for public in (True, False):
        label = "public" if public else "private"
        requests.append(
            Request.freeze(
                label,
                create_allocation("project-a", label, "@subnet", public=public),
                references=(Reference("spec.ipv4_" + label + ".subnet_id", "subnet", "subnet"),),
            )
        )
    requests.append(
        Request.freeze(
            "compute",
            instance,
            guest="gateway-0",
            references=(
                *compute().references,
                Reference("spec.network_interfaces.0.subnet_id", "subnet", "subnet"),
                Reference(
                    "spec.network_interfaces.0.ip_address.allocation_id", "private", "allocation"
                ),
                Reference(
                    "spec.network_interfaces.0.public_ip_address.allocation_id",
                    "public",
                    "allocation",
                ),
            ),
        )
    )
    return {
        **plan(),
        "requests": [r.mapping() for r in requests],
        "inventories": [
            {"kind": "subnet", "parent": "network-a", "ids": []},
            {"kind": "route", "parent": "@table", "ids": []},
        ],
    }


@pytest.mark.parametrize("lost_reply", [False, True])
def test_full_network_and_member_graph_replays_without_duplicate_resources(
    tmp_path, monkeypatch, lost_reply
):
    desired = resource_plan()
    resources = {}
    accepted = {}
    submissions = []
    lost = False

    def service(kind, resource_type):
        def create(request, **options):
            nonlocal lost
            key = options["metadata"]
            signature = message_digest(request)
            submissions.append((kind, key, signature))
            assert options["retries"] == 0
            if key in accepted:
                old_signature, result = accepted[key]
                assert old_signature == signature
                return SimpleNamespace(wait=lambda: result)
            identifier = kind + "-" + request.metadata.name
            assert identifier not in resources
            metadata = clone_nebius_sdk_message(request.metadata)
            metadata.id = identifier
            metadata.resource_version = 1
            resource = resource_type(metadata=metadata, spec=clone_nebius_sdk_message(request.spec))
            if kind == "allocation":
                resource.status.state = "ALLOCATED"
            if kind == "compute":
                for nic in request.spec.network_interfaces:
                    for address in (nic.ip_address, nic.public_ip_address):
                        allocation = resources[address.allocation_id][1]
                        allocation.status.state = "ASSIGNED"
                        allocation.status.assignment.__pb2_message__.network_interface.instance_id = identifier
                        allocation.status.assignment.__pb2_message__.network_interface.name = (
                            nic.name
                        )
            resources[identifier] = (kind, resource)
            result = operation(identifier)
            accepted[key] = (signature, result)
            if kind == "subnet" and lost_reply and not lost:
                lost = True
                raise TimeoutError("server created subnet; submission response lost")
            return SimpleNamespace(wait=lambda: result)

        return SimpleNamespace(
            create=create,
            get=lambda request, **options: SimpleNamespace(wait=lambda: resources[request.id][1]),
        )

    for module, client, kind, resource_type in [
        (vpc, "RouteTableServiceClient", "table", vpc.RouteTable),
        (vpc, "RouteServiceClient", "route", vpc.Route),
        (vpc, "SubnetServiceClient", "subnet", vpc.Subnet),
        (vpc, "AllocationServiceClient", "allocation", vpc.Allocation),
        (compute_api, "DiskServiceClient", "disk", compute_api.Disk),
        (compute_api, "InstanceServiceClient", "compute", compute_api.Instance),
    ]:
        monkeypatch.setattr(module, client, lambda _, k=kind, t=resource_type: service(k, t))

    def inventory(sdk, kind, parent):
        return tuple(
            resource
            for actual_kind, resource in resources.values()
            if actual_kind == kind
            and (resource.spec.network_id if kind == "subnet" else resource.metadata.parent_id)
            == parent
        )

    monkeypatch.setattr(provisioning_execute, "inventory", inventory)
    command, envelope = parent(tmp_path, desired)
    journal = ProvisioningJournal("project-a", "gateway", root=tmp_path / "child")
    with command:
        command.begin(envelope, intents={"provisioning": desired})
        command.started("provisioning")
        if lost_reply:
            with pytest.raises(TimeoutError):
                provisioning_execute.execute(
                    object(), journal, command, desired, bootstrap=bootstrap
                )
            assert len(resources) == 3
        receipt = provisioning_execute.execute(
            object(), journal, command, desired, bootstrap=bootstrap
        )
        command.verified("provisioning", receipt)
        command.complete()
        before = journal.path.read_bytes(), command.path.read_bytes(), len(submissions)
        assert (
            provisioning_execute.execute(object(), journal, command, desired, bootstrap=bootstrap)
            == receipt
        )
        assert before == (journal.path.read_bytes(), command.path.read_bytes(), len(submissions))
    assert len(resources) == 7
    assert len(submissions) == 7 + int(lost_reply)
    assert len(journal.state["bindings"]) == 7
    instance = resources[journal.state["bindings"]["compute"]["resource_id"]][1]
    nic = instance.spec.network_interfaces[0]
    assert nic.subnet_id == journal.state["bindings"]["subnet"]["resource_id"]
    assert (
        resources[nic.subnet_id][1].spec.route_table_id
        == journal.state["bindings"]["table"]["resource_id"]
    )
