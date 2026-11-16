"""Settlement must never create the unsubmitted remainder of an obsolete plan."""

from types import SimpleNamespace as NS

import pytest
from nebius.api.nebius.compute import v1 as compute
from test_provisioning_execute import bootstrap, cloud_fixture, parent
from test_provisioning_journal import operation, plan

from nebius_vpngw.deploy.provisioning_execute import execute, settle_submitted
from nebius_vpngw.deploy.provisioning_journal import ProvisioningJournal
from nebius_vpngw.deploy.replacement_cloud import message_digest


@pytest.fixture
def prefix(tmp_path, monkeypatch):
    desired = plan()
    resources, submissions = cloud_fixture(monkeypatch)
    command, envelope = parent(tmp_path, desired)
    child = ProvisioningJournal("project-a", "gateway", root=tmp_path / "child")
    with command:
        command.begin(envelope, intents={"provisioning": desired})
        command.started("provisioning")
        with monkeypatch.context() as patch:

            def interrupted(*_):
                raise TimeoutError("settled before prefix receipt publication")

            patch.setattr(child, "verified", interrupted)
            with pytest.raises(TimeoutError):
                execute(object(), child, command, desired, bootstrap=bootstrap)
    return NS(
        command=command,
        child=child,
        desired=desired,
        resources=resources,
        submissions=submissions,
    )


@pytest.mark.parametrize("status", ["intent", "accepted", "settled"])
def test_settles_original_disk_only_and_keeps_compute_unsubmitted(prefix, monkeypatch, status):
    item = prefix
    # Recreate each crash frontier at the same native service request/operation.
    with item.child:
        record = next(iter(item.child.state["transaction"]["operations"].values()))
        record["status"] = status
        if status == "intent":
            record["cloud_operation_id"] = record["resource_id"] = ""
        elif status == "accepted":
            record["resource_id"] = ""
        item.child.save()
    accepted = operation("disk-created")
    replays = []

    def replay(request, **options):
        replays.append((message_digest(request), options["metadata"]))
        return NS(wait=lambda: accepted)

    def get_operation(request, **_):
        assert request.id == accepted.id
        return NS(wait=lambda: accepted)

    monkeypatch.setattr(
        compute,
        "DiskServiceClient",
        lambda _: NS(
            create=replay,
            get=lambda request, **_: NS(wait=lambda: item.resources[request.id]),
            operation_service=lambda: NS(get=get_operation),
        ),
    )
    with item.command, item.child:
        settle_submitted(object(), item.child, item.command)
        before = item.child.path.read_bytes()
        settle_submitted(object(), item.child, item.command)
        assert before == item.child.path.read_bytes()
    assert item.child.snapshot()["transaction"]["phase"] == "pending"
    assert item.child.snapshot()["transaction"]["verified"] == ["disk"]
    assert set(item.resources) == {"disk-created"}
    assert len(replays) == (status == "intent")
    if replays:
        assert replays[0] == item.submissions[0][1:]
    assert item.command.pending() is not None


def test_changed_created_disk_refuses_settlement_before_receipt_or_compute(prefix):
    item = prefix
    item.resources["disk-created"].spec.size_gibibytes = 1
    before = item.child.path.read_bytes(), list(item.submissions)
    with item.command, item.child, pytest.raises(RuntimeError, match="Created resource differs"):
        settle_submitted(object(), item.child, item.command)
    assert before == (item.child.path.read_bytes(), item.submissions)


def test_compute_prefix_requires_its_guest_owner_instead_of_being_abandoned(prefix, monkeypatch):
    item = prefix
    native = item.child.verified

    def interrupted(recipe, request):
        if recipe.guest:
            raise TimeoutError("Compute receipt acknowledgement lost")
        native(recipe, request)

    with item.command:
        with monkeypatch.context() as patch:
            patch.setattr(item.child, "verified", interrupted)
            with pytest.raises(TimeoutError):
                execute(object(), item.child, item.command, item.desired, bootstrap=bootstrap)
        before = item.child.path.read_bytes(), list(item.submissions)
        with item.child, pytest.raises(ValueError, match="guest owner"):
            settle_submitted(object(), item.child, item.command)
    assert before == (item.child.path.read_bytes(), item.submissions)


def test_settlement_requires_both_original_owner_locks(prefix):
    item = prefix
    with pytest.raises(ValueError, match="active owner"):
        settle_submitted(object(), item.child, item.command)
    with item.child, pytest.raises(RuntimeError, match="active command writer"):
        settle_submitted(object(), item.child, item.command)
