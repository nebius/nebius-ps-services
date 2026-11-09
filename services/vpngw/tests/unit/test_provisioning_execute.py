from copy import deepcopy
from types import SimpleNamespace

import pytest
from nebius.api.nebius.compute import v1 as compute
from test_provisioning_journal import operation, plan

from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
from nebius_vpngw.deploy.provisioning_execute import Bootstrap, execute
from nebius_vpngw.deploy.provisioning_journal import ProvisioningJournal
from nebius_vpngw.deploy.replacement_cloud import message_digest
from nebius_vpngw.deploy.vm_ha_cloud import clone_nebius_sdk_message


def parent(tmp_path, desired):
    journal = CommandJournal("project-a", "gateway", root=tmp_path / "command")
    envelope = CommandPlan(
        "project-a",
        "gateway",
        "a" * 64,
        "b" * 64,
        (
            Effect(
                "provisioning",
                "ordinary-provisioning",
                "create",
                ("gateway-0",),
                digest(desired),
                digest(desired["observed"]),
                True,
            ),
        ),
    )
    return journal, envelope


def cloud_fixture(monkeypatch):
    resources = {}
    submissions = []

    def service(resource_type, kind):
        def create(request, **options):
            identifier = kind + "-created"
            submissions.append((kind, message_digest(request), options["metadata"]))
            assert options["retries"] == 0
            metadata = clone_nebius_sdk_message(request.metadata)
            metadata.id = identifier
            metadata.resource_version = 1
            resources[identifier] = resource_type(
                metadata=metadata, spec=clone_nebius_sdk_message(request.spec)
            )
            return SimpleNamespace(wait=lambda: operation(identifier))

        return SimpleNamespace(
            create=create,
            get=lambda request, **kwargs: SimpleNamespace(wait=lambda: resources[request.id]),
        )

    monkeypatch.setattr(compute, "DiskServiceClient", lambda _: service(compute.Disk, "disk"))
    monkeypatch.setattr(
        compute, "InstanceServiceClient", lambda _: service(compute.Instance, "compute")
    )
    return resources, submissions


def bootstrap(_):
    return Bootstrap("c" * 64, "d" * 64, "#cloud-config\nprivate-fixture-data")


def test_resources_resume_from_receipts_and_complete_without_repeating_create(
    tmp_path, monkeypatch
):
    desired = plan()
    resources, submissions = cloud_fixture(monkeypatch)
    command, envelope = parent(tmp_path, desired)
    child = ProvisioningJournal("project-a", "gateway", root=tmp_path / "child")
    verified = child.verified

    def interruption(recipe, request):
        if recipe.key == "disk":
            raise TimeoutError("operator interrupted after settlement, before binding")
        verified(recipe, request)

    with command:
        command.begin(envelope, intents={"provisioning": desired})
        command.started("provisioning")
        monkeypatch.setattr(child, "verified", interruption)
        with pytest.raises(TimeoutError):
            execute(object(), child, command, desired, bootstrap=bootstrap)
        assert list(resources) == ["disk-created"]
        assert child.state["bindings"] == {}
        monkeypatch.setattr(child, "verified", verified)
        receipt = execute(object(), child, command, desired, bootstrap=bootstrap)
        command.verified("provisioning", receipt)
        command.complete()
        before = child.path.read_bytes(), command.path.read_bytes()
        assert execute(object(), child, command, desired, bootstrap=bootstrap) == receipt
        assert before == (child.path.read_bytes(), command.path.read_bytes())
    assert [item[0] for item in submissions] == ["disk", "compute"]
    assert resources["compute-created"].spec.boot_disk.existing_disk.id == "disk-created"
    assert "private-fixture-data" not in child.path.read_text()
    assert "private-fixture-data" not in repr(bootstrap("gateway-0"))


def test_readback_failure_cannot_grant_creation_binding_or_continue(tmp_path, monkeypatch):
    desired = plan()
    resources, submissions = cloud_fixture(monkeypatch)
    command, envelope = parent(tmp_path, desired)
    child = ProvisioningJournal("project-a", "gateway", root=tmp_path / "child")
    from nebius_vpngw.deploy import provisioning_execute

    original = provisioning_execute.read

    def inconsistent(sdk, kind, identifier):
        result = deepcopy(original(sdk, kind, identifier))
        result.metadata.parent_id = "foreign-project"
        return result

    monkeypatch.setattr(provisioning_execute, "read", inconsistent)
    with command:
        command.begin(envelope, intents={"provisioning": desired})
        command.started("provisioning")
        with pytest.raises(RuntimeError, match="Created resource differs"):
            execute(object(), child, command, desired, bootstrap=bootstrap)
    assert list(resources) == ["disk-created"]
    assert len(submissions) == 1 and not child.state["bindings"]


def test_changed_bootstrap_cannot_create_compute(tmp_path, monkeypatch):
    desired = plan()
    _, submissions = cloud_fixture(monkeypatch)
    command, envelope = parent(tmp_path, desired)
    child = ProvisioningJournal("project-a", "gateway", root=tmp_path / "child")
    with command:
        command.begin(envelope, intents={"provisioning": desired})
        command.started("provisioning")
        with pytest.raises(RuntimeError, match="bootstrap or trust intent changed"):
            execute(
                object(),
                child,
                command,
                desired,
                bootstrap=lambda _: Bootstrap("e" * 64, "d" * 64, "different-bootstrap"),
            )
    assert submissions == [] and not child.path.exists()


def test_unapproved_child_cannot_publish_or_mutate(tmp_path, monkeypatch):
    desired = plan()
    _, submissions = cloud_fixture(monkeypatch)
    command, _ = parent(tmp_path, desired)
    child = ProvisioningJournal("project-a", "gateway", root=tmp_path / "child")
    with pytest.raises(RuntimeError, match="active command writer"):
        execute(object(), child, command, desired, bootstrap=bootstrap)
    assert not child.path.exists() and not submissions


def test_uncertain_network_update_replays_exact_cas_with_opaque_fields(tmp_path, monkeypatch):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc import v1 as vpc

    from nebius_vpngw.deploy.provisioning_inventory import snapshot
    from nebius_vpngw.deploy.provisioning_network import extend_pool
    from nebius_vpngw.deploy.provisioning_plan import Request

    pool = vpc.Pool(
        metadata=ResourceMetadata(
            id="pool-a", name="network-pool", parent_id="project-a", resource_version=7
        ),
        spec=vpc.PoolSpec(cidrs=[vpc.PoolCidr(cidr="192.0.2.0/24")]),
    )
    pool.spec.__pb2_message__.MergeFromString(b"\xf8\x07\x01")
    recipe = Request.freeze("extend-pool", extend_pool(pool, "project-a", "198.51.100.0/24"))
    desired = {
        **plan(),
        "requests": [recipe.mapping()],
        "observed": {"pool-a": snapshot("pool", pool)},
        "bootstraps": {},
    }
    requests = []

    def update(request, **options):
        requests.append((message_digest(request), options["metadata"]))
        assert request.metadata.resource_version == 7
        pool.spec = clone_nebius_sdk_message(request.spec)
        pool.metadata.resource_version = 8
        if len(requests) == 1:
            raise TimeoutError("server applied original request; reply lost")
        return SimpleNamespace(wait=lambda: operation("pool-a"))

    monkeypatch.setattr(
        vpc,
        "PoolServiceClient",
        lambda _: SimpleNamespace(
            update=update, get=lambda request, **options: SimpleNamespace(wait=lambda: pool)
        ),
    )
    command, envelope = parent(tmp_path, desired)
    child = ProvisioningJournal("project-a", "gateway", root=tmp_path / "child")
    with command:
        command.begin(envelope, intents={"provisioning": desired})
        command.started("provisioning")
        with pytest.raises(TimeoutError):
            execute(object(), child, command, desired, bootstrap=bootstrap)
        assert child.state["transaction"]["phase"] == "pending"
        execute(object(), child, command, desired, bootstrap=bootstrap)
    assert len(requests) == 2 and requests[0] == requests[1]
    assert child.state["bindings"] == {}  # An update never grants creation ownership.
    assert child.state["transaction"]["phase"] == "complete"


def test_new_foreign_inventory_member_blocks_every_cloud_submission(tmp_path, monkeypatch):
    desired = {**plan(), "inventories": [{"kind": "subnet", "parent": "network-a", "ids": []}]}
    _, submissions = cloud_fixture(monkeypatch)
    from nebius_vpngw.deploy import provisioning_execute

    monkeypatch.setattr(
        provisioning_execute,
        "inventory",
        lambda *args: (SimpleNamespace(metadata=SimpleNamespace(id="unreviewed-subnet")),),
    )
    command, envelope = parent(tmp_path, desired)
    child = ProvisioningJournal("project-a", "gateway", root=tmp_path / "child")
    with command:
        command.begin(envelope, intents={"provisioning": desired})
        command.started("provisioning")
        with pytest.raises(RuntimeError, match="inventory changed outside"):
            execute(object(), child, command, desired, bootstrap=bootstrap)
    assert not submissions and child.state["transaction"]["operations"] == {}


@pytest.mark.parametrize("state,assigned", [("ASSIGNED", True), ("CREATING", False)])
def test_existing_allocation_must_still_be_unassigned_before_creation(
    tmp_path, monkeypatch, state, assigned
):
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.api.nebius.vpc import v1 as vpc

    from nebius_vpngw.deploy.provisioning_inventory import snapshot

    allocation = vpc.Allocation(
        metadata=ResourceMetadata(id="allocation-a", parent_id="project-a", resource_version=2)
    )
    allocation.status.state = "ALLOCATED"
    desired = {**plan(), "observed": {"allocation-a": snapshot("allocation", allocation)}}
    _, submissions = cloud_fixture(monkeypatch)
    allocation.status.state = state
    if assigned:
        allocation.status.assignment.__pb2_message__.network_interface.instance_id = (
            "unrelated-compute"
        )
    monkeypatch.setattr(
        vpc,
        "AllocationServiceClient",
        lambda _: SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(wait=lambda: allocation)),
    )
    command, envelope = parent(tmp_path, desired)
    child = ProvisioningJournal("project-a", "gateway", root=tmp_path / "child")
    with command:
        command.begin(envelope, intents={"provisioning": desired})
        command.started("provisioning")
        with pytest.raises(RuntimeError, match="not proven unassigned"):
            execute(object(), child, command, desired, bootstrap=bootstrap)
    assert not submissions
