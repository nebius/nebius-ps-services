from types import SimpleNamespace as NS

import pytest
from nebius.api.nebius.compute import v1 as compute
from tests.unit import test_security_group_apply

from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import ordinary_replan
from nebius_vpngw.deploy import security_group_apply as attachments
from nebius_vpngw.deploy.apply_command import CommandJournal, ReplanReady
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect
from nebius_vpngw.deploy.vm_ha_cloud import clone_nebius_sdk_message

cloud = test_security_group_apply.cloud


@pytest.fixture(params=[1, 2])
def predecessor(cloud, tmp_path, monkeypatch, request):
    state, manager = cloud
    reject = isinstance(request.param, tuple)
    monkeypatch.setenv("HOME", str(tmp_path))
    targets = {"gateway-a-0": ("compute-a", "transport-a")}
    member_count = request.param[0] if reject else request.param
    if member_count == 2:
        second = clone_nebius_sdk_message(state.instance)
        second.metadata.id, second.metadata.name = "compute-b", "gateway-a-1"
        targets["gateway-a-1"] = ("compute-b", "transport-b")
        native = compute.InstanceServiceClient(object())

        def get(resource_id):
            assert resource_id in {"compute-a", "compute-b"}
            return clone_nebius_sdk_message(
                state.instance if resource_id == "compute-a" else second
            )

        def update(request, **kwargs):
            assert request.metadata.id == "compute-a", "Obsolete unsubmitted suffix was executed"
            return native.update(request, **kwargs)

        monkeypatch.setattr(
            compute,
            "InstanceServiceClient",
            lambda _: NS(
                get=lambda request, **_: NS(wait=lambda: get(request.id)),
                update=update,
                operation_service=native.operation_service,
            ),
        )
        manager.get_ha_instance = get
        manager.inspect_ordinary_targets = lambda names: {name: targets[name] for name in names}
    if reject:
        from grpc import StatusCode
        from tests.unit.test_replacement_cloud_recovery import request_error

        current_service = compute.InstanceServiceClient(object())

        def rejected_update(request, **kwargs):
            assert kwargs["retries"] == 0
            state.instance.metadata.resource_version += 1
            raise request_error(StatusCode.ABORTED)

        monkeypatch.setattr(
            compute,
            "InstanceServiceClient",
            lambda _: NS(
                get=current_service.get,
                update=rejected_update,
                operation_service=current_service.operation_service,
            ),
        )
    plan = attachments.inspect_plan(manager, tuple(targets), ("sg-b",))
    parent = CommandJournal("project-a", "gateway-a", root=tmp_path / "command")
    envelope = CommandPlan(
        "project-a",
        "gateway-a",
        digest({"desired": {"version": 1}, "targets": targets}),
        "a" * 64,
        (
            Effect(
                "attachments",
                "security-groups",
                "reconcile",
                tuple(targets),
                digest(plan),
                digest(plan),
                True,
            ),
        ),
    )
    child = attachments.AttachmentJournal("project-a", "gateway-a", root=tmp_path / "attachments")
    guests = {
        name: {"boot": "current-boot", "config": "b" * 64, "binding": None} for name in targets
    }
    with parent:
        parent.begin(envelope, intents={"attachments": plan})
        parent.started("attachments")
    with child:
        child.begin(
            plan,
            scope="b" * 64,
            approval=envelope.approval,
            operation_id=parent.pending()["execution_id"],
        )
        child.state["transaction"]["guests"] = guests
        child.save()
        state.lose_receipt = True
        from nebius_vpngw.deploy.replacement_cloud import CloudOperationRejected

        with pytest.raises(CloudOperationRejected if reject else TimeoutError):
            attachments.update_member(manager, child, "gateway-a-0")
    monkeypatch.setattr(ordinary_replan, "AttachmentJournal", lambda *_: child)
    monkeypatch.setattr(
        ordinary_replan,
        "Handoff",
        lambda *_: NS(inspect=lambda: {"authority": None, "journal": None}),
    )
    monkeypatch.setattr(
        attachments, "guest_identity", lambda _s, _t, instance, _l: guests[instance.hostname]
    )
    refreshed = []

    def refresh(manager, ssh, owner, name, proof, callback):
        # Pin refresh itself has independent native owner regressions. Keep its
        # call and exact successful Compute proof visible at this coordinator.
        assert attachments.verified_update(manager, owner, name) == proof
        assert callback() == "same-pins-current-cloud"
        refreshed.append(name)

    monkeypatch.setattr(attachments, "refresh_guest_trust", refresh)
    instances = tuple(NS(hostname=name, vm_ha_node=None) for name in targets)
    ssh = object()
    options = dict(
        ssh=ssh, instances=instances, local={"version": 2}, targets=targets, artifact="c" * 64
    )
    options["attachment_factory"] = lambda _: dict(
        manager=manager,
        ssh=ssh,
        instances=instances,
        local=options["local"],
        targets=targets,
        refresh_ssh_policy=lambda: "same-pins-current-cloud",
    )
    return NS(
        state=state,
        manager=manager,
        parent=parent,
        child=child,
        options=options,
        guests=guests,
        refreshed=refreshed,
        inspect=lambda: ordinary_replan.inspect(parent, **options),
    )


def test_settles_exact_attachment_request_then_separately_retires_parent(predecessor):
    item = predecessor
    before = item.child.path.read_bytes()
    proposed = item.inspect()
    assert not proposed.pending_attachments.path.exists()
    assert item.child.path.read_bytes() == before
    with pytest.raises(ReplanReady):
        proposed.retire(item.inspect())
    assert item.parent.pending() is not None
    assert item.child.snapshot()["transaction"] is None
    assert item.state.effects == 1
    fresh = attachments.inspect_plan(item.manager, tuple(item.guests), ("sg-c",))
    assert fresh["members"]["gateway-a-0"]["before"][0]["explicit_ids"] == ["sg-b"]
    if "gateway-a-1" in item.guests:
        assert fresh["members"]["gateway-a-1"]["before"][0]["explicit_ids"] == ["sg-a"]
    assert len(item.state.requests) == 2 and item.state.requests[0][1] == item.state.requests[1][1]
    assert item.refreshed == ["gateway-a-0"]
    assert before in [p.read_bytes() for p in (item.child.root / "history").glob("*.json")]
    completed = item.inspect()
    assert completed.pending_attachments is None
    completed.retire(item.inspect())
    assert item.parent.pending() is None
    assert item.state.effects == 1


@pytest.mark.parametrize(
    "name", ["credentials", "ssh", "attachments", "provisioning", "routes", "replacement"]
)
def test_every_owner_is_excluded_before_attachment_replay(predecessor, name):
    item = predecessor
    proposed = item.inspect()
    owner = proposed.owners[name]
    with (
        type(owner)(owner.project, owner.gateway, root=owner.root),
        pytest.raises((RuntimeError, BlockingIOError)),
    ):
        proposed.retire(item.inspect())
    assert not proposed.pending_attachments.path.exists()
    assert len(item.state.requests) == 1


@pytest.mark.parametrize("boundary", ["cloud", "retirement"])
def test_lost_attachment_acknowledgement_keeps_original_settlement_approval(
    predecessor, monkeypatch, boundary
):
    item = predecessor
    proposed = item.inspect()
    native = item.child.save
    fired = False

    def fail_once():
        nonlocal fired
        native()
        current = item.child.state["transaction"]
        ready = (
            current is None
            if boundary == "retirement"
            else current is not None
            and all(record["status"] == "settled" for record in current["operations"].values())
        )
        if ready and not fired:
            fired = True
            raise OSError("lost owned publication acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(item.child, "save", fail_once)
        with pytest.raises(OSError):
            proposed.retire(item.inspect())
    resumed = item.inspect()
    assert resumed.resumed and resumed.approval == proposed.approval
    with pytest.raises(ReplanReady):
        resumed.retire(item.inspect())
    assert item.child.snapshot()["transaction"] is None
    assert item.state.effects == 1


@pytest.mark.parametrize("drift", ["compute", "guest", "ha"])
def test_foreign_or_ha_authority_is_not_retired_by_ordinary_path(predecessor, drift):
    item = predecessor
    if drift == "compute":
        item.state.instance.spec.resources.preset = "8vcpu-32gb"
    elif drift == "guest":
        item.guests["gateway-a-0"]["config"] = "f" * 64
    else:
        with item.child:
            item.child.state["transaction"]["guests"]["gateway-a-0"]["binding"] = {"ha": True}
            item.child.save()
    before = item.child.path.read_bytes(), len(item.state.requests)
    with pytest.raises((RuntimeError, ValueError)):
        item.inspect()
    assert before == (item.child.path.read_bytes(), len(item.state.requests))


@pytest.mark.parametrize("dry_run, approved", [(True, False), (False, False), (False, True)])
def test_cli_requires_separate_settlement_approval(predecessor, monkeypatch, dry_run, approved):
    import click

    from nebius_vpngw import cli
    from nebius_vpngw.deploy.apply_result import ApplyReport

    item = predecessor
    original = item.parent.path.read_bytes(), item.child.path.read_bytes()
    proposal = item.inspect()
    report = ApplyReport(dry_run=dry_run)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.typer, "confirm", lambda *_a, **_kw: approved)
    with pytest.raises(ReplanReady if approved else click.exceptions.Exit) as stopped:
        cli._prepare_ordinary_apply(
            item.manager,
            item.options["ssh"],
            item.options["instances"],
            item.options["local"],
            supplied=None,
            dry_run=dry_run,
            recreate=False,
            command_journal=item.parent,
            artifact_sha256=item.options["artifact"],
            refresh_ssh_policy=lambda: "same-pins-current-cloud",
            result_report=report,
        )
    assert report.mapping()["approval"] == proposal.approval
    assert item.state.effects == 1
    if approved:
        assert item.child.snapshot()["transaction"] is None
        assert item.parent.path.read_bytes() == original[0]
    else:
        assert stopped.value.exit_code == 0
        assert original == (item.parent.path.read_bytes(), item.child.path.read_bytes())
        assert len(item.state.requests) == 1
        assert not proposal.pending_attachments.path.exists()


@pytest.mark.parametrize("resumed", [False, True])
def test_attachment_approval_cas_preserves_competing_evidence(predecessor, monkeypatch, resumed):
    from nebius_vpngw.deploy.attachment_settlement import Cloud
    from nebius_vpngw.deploy.recovery import ReplanRejected

    item = predecessor
    proposal = item.inspect()
    if resumed:
        with monkeypatch.context() as patch:

            def stopped(*_):
                raise TimeoutError("injected before settlement")

            patch.setattr(Cloud, "settle", stopped)
            with pytest.raises(TimeoutError):
                proposal.retire(item.inspect())
        proposal = item.inspect()
        assert proposal.resumed
    fresh = item.inspect()
    path = proposal.pending_attachments.path
    path.parent.mkdir(mode=0o700, exist_ok=True)
    competing = '{"another": "approval"}'
    path.write_text(competing)
    path.chmod(0o600)
    before = item.parent.path.read_bytes(), item.child.path.read_bytes(), len(item.state.requests)
    with pytest.raises(ReplanRejected, match="changed"):
        proposal.retire(fresh)
    assert before == (
        item.parent.path.read_bytes(),
        item.child.path.read_bytes(),
        len(item.state.requests),
    )
    assert path.read_text() == competing


def test_attachment_completion_cas_cannot_retire_after_approval_drift(predecessor, monkeypatch):
    from nebius_vpngw.deploy.attachment_settlement import Cloud

    item = predecessor
    proposal = item.inspect()
    path = proposal.pending_attachments.path
    native = Cloud.settle

    def settle_then_drift(*args):
        native(*args)
        path.write_text('{"another": "approval"}')

    monkeypatch.setattr(Cloud, "settle", settle_then_drift)
    with pytest.raises((RuntimeError, ValueError), match="changed"):
        proposal.retire(item.inspect())
    assert path.read_text() == '{"another": "approval"}'
    assert item.parent.pending() is not None
    assert item.child.pending() is not None
    assert item.state.effects == 1


@pytest.mark.parametrize("predecessor", [(1, "rejected"), (2, "rejected")], indirect=True)
def test_changed_intent_retires_proven_no_effect_rejection_without_retry(predecessor):
    item = predecessor
    before = item.child.path.read_bytes()
    with pytest.raises(ReplanReady):
        item.inspect().retire(item.inspect())
    assert item.child.snapshot()["transaction"] is None
    assert item.state.effects == 0 and item.state.requests == []
    item.inspect().retire(item.inspect())
    assert item.parent.pending() is None
    assert before in [p.read_bytes() for p in (item.child.root / "history").glob("*.json")]
    fresh = attachments.inspect_plan(item.manager, tuple(item.guests), ("sg-c",))
    assert all(
        member["before"][0]["explicit_ids"] == ["sg-a"] for member in fresh["members"].values()
    )


@pytest.mark.parametrize("predecessor", [(1, "rejected"), (2, "rejected")], indirect=True)
@pytest.mark.parametrize("drift", ["second-attempt", "provider-operation", "unrelated-spec"])
def test_rejected_request_without_no_effect_proof_preserves_authority(predecessor, drift):
    item = predecessor
    if drift == "unrelated-spec":
        item.state.instance.spec.resources.preset = "8vcpu-32gb"
    else:
        with item.child:
            record = next(iter(item.child.state["transaction"]["operations"].values()))
            if drift == "second-attempt":
                record["submission_count"] = 2
            else:
                record["cloud_operation_id"] = "operation-unverified"
            item.child.save()
    before = item.parent.path.read_bytes(), item.child.path.read_bytes()
    with pytest.raises((RuntimeError, ValueError)):
        item.inspect()
    assert before == (item.parent.path.read_bytes(), item.child.path.read_bytes())
    assert item.state.effects == 0
