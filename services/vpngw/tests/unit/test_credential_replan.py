from types import SimpleNamespace as NS

import pytest
from tests.unit import test_credential_enrollment as enrollment_tests

from nebius_vpngw import vm_ha_managed_credentials as managed
from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import credential_replan
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect

graph = enrollment_tests.graph


@pytest.fixture(params=["ha", "ordinary"])
def prefix(graph, tmp_path, monkeypatch, request):
    cloud, child, observe, execute = graph
    initial = observe()
    intent = initial.approval_record()
    parent = CommandJournal(child.project, child.gateway, root=tmp_path / "parent")
    runtime = {"kind": "unstarted-runtime"}
    runtime_key = "runtime" if request.param == "ha" else "provisioning"
    runtime_owner = "ha-lifecycle" if request.param == "ha" else "ordinary-provisioning"
    plan = CommandPlan(
        child.project,
        child.gateway,
        "a" * 64,
        "b" * 64,
        (
            Effect(
                "credentials",
                "managed-credentials",
                "create",
                ("a", "b"),
                digest(intent),
                "c" * 64,
                False,
            ),
            Effect(
                runtime_key,
                runtime_owner,
                "apply",
                ("a", "b"),
                digest(runtime),
                "d" * 64,
                True,
                ("credentials",),
            ),
        ),
    )
    with parent:
        parent.begin(plan, intents={"credentials": intent, runtime_key: runtime})
        parent.started("credentials")
    with child:
        child.begin(intent, approval=plan.approval, execution_id=parent.pending()["execution_id"])
        cloud.fail = ("group", "reply")
        with pytest.raises(managed.VMHAManagedCredentialError):
            execute(initial)
    monkeypatch.setattr(credential_replan, "EnrollmentJournal", lambda *_: child)
    for name in (
        "TrustJournal",
        "AttachmentJournal",
        "ProvisioningJournal",
        "RouteJournal",
        "ReplacementJournal",
    ):
        factory = getattr(credential_replan, name)
        monkeypatch.setattr(
            credential_replan,
            name,
            lambda p, g, factory=factory, name=name: factory(p, g, root=tmp_path / name),
        )
    options = dict(
        local={"changed": True}, lifecycle=None, artifact="e" * 64, observe=lambda _: observe()
    )

    def settle(owner, observed):
        managed.settle_managed_vm_ha_enrollment(
            observed,
            enrollment=owner,
            tenant_id=None,
            region_id=None,
            client_factory=lambda *_: (cloud, True),
        )

    def inspect():
        return credential_replan.inspect(parent, **options)

    return NS(
        cloud=cloud,
        child=child,
        parent=parent,
        initial=initial,
        options=options,
        inspect=inspect,
        settle=settle,
        observe=lambda _: observe(),
        ensure=execute,
        runtime_key=runtime_key,
    )


def test_settlement_preserves_partial_iam_graph_and_key_for_fresh_approval(prefix):
    item = prefix
    before = item.child.path.read_bytes(), item.parent.path.read_bytes()
    key = item.initial.source_path.parent / managed._PENDING_KEY_NAME
    original_key = key.read_bytes()
    proposed = item.inspect()
    assert not proposed.path.exists()
    assert before == (item.child.path.read_bytes(), item.parent.path.read_bytes())
    proposed.execute(refresh=item.inspect, observe=item.observe, settle=item.settle)
    assert item.parent.pending() is None
    assert item.child.snapshot()["transaction"] is None
    assert set(item.cloud.operations) == {"service-account", "group"}
    assert key.read_bytes() == original_key
    assert not item.initial.source_path.exists()
    assert item.parent.retirement_completed(proposed.proof)
    assert before[0] in [p.read_bytes() for p in (item.child.root / "history").glob("*.json")]
    fresh = item.observe(item.child)
    assert fresh.iam_plan.service_account_action == fresh.iam_plan.group_action == "reuse"
    assert fresh.iam_plan.membership_action == "create"
    # Only a new command may approve and execute the remaining current graph.
    with item.child:
        item.child.begin(fresh.observation_record(), approval="f" * 64, execution_id="1" * 32)
        item.ensure(fresh)
    assert len(item.cloud.operations) == 5
    assert item.initial.source_path.exists()


@pytest.mark.parametrize("boundary", ["cloud", "child-retired", "parent-receipt"])
def test_credential_settlement_resumes_original_approval_after_lost_ack(
    prefix, monkeypatch, boundary
):
    item = prefix
    proposed = item.inspect()
    fired = False
    if boundary in {"cloud", "child-retired"}:
        owner, attribute = item.child, "save"
    else:
        owner, attribute = item.parent, "save"

    def condition():
        if boundary == "cloud":
            return all(
                record["status"] == "settled"
                for record in item.child.state["transaction"]["operations"].values()
            )
        return owner.state["transaction"] is None

    original = getattr(owner, attribute)

    def lost_reply():
        nonlocal fired
        if boundary == "parent-receipt" and condition() and not fired:
            fired = True
            raise OSError("injected before parent publication")
        original()
        if condition() and not fired:
            fired = True
            raise OSError("injected acknowledgement loss")

    with monkeypatch.context() as patch:
        patch.setattr(owner, attribute, lost_reply)
        with pytest.raises(OSError, match="injected"):
            proposed.execute(refresh=item.inspect, observe=item.observe, settle=item.settle)
    resumed = item.inspect()
    assert resumed.resumed and resumed.approval == proposed.approval
    resumed.execute(refresh=item.inspect, observe=item.observe, settle=item.settle)
    assert item.parent.pending() is None
    assert set(item.cloud.operations) == {"service-account", "group"}


@pytest.mark.parametrize(
    "name", ["credentials", "ssh", "attachments", "routes", "provisioning", "replacement"]
)
def test_every_owner_is_excluded_before_credential_settlement(prefix, name):
    item = prefix
    proposed = item.inspect()
    owner = proposed.owners[name]
    competitor = type(owner)(owner.project, owner.gateway, root=owner.root)
    with competitor, pytest.raises(RuntimeError, match="still running"):
        proposed.execute(refresh=item.inspect, observe=item.observe, settle=item.settle)
    assert not proposed.path.exists()
    assert item.parent.pending() is not None


def test_started_runtime_and_package_work_cannot_use_credential_only_retirement(prefix):
    item = prefix
    with item.parent:
        item.parent.verified("credentials", {"fixture": "receipt"})
        item.parent.started(item.runtime_key)
    assert item.inspect() is None


def test_changed_native_key_blocks_before_iam_replay_and_has_repair_prerequisite(
    prefix, monkeypatch
):
    from nebius_vpngw.deploy.recovery import Invocation, ReplanRejected, advice

    item = prefix
    observed = item.observe(item.child)
    before = item.child.path.read_bytes(), list(item.cloud.calls)

    def unreadable(_):
        raise managed.VMHAManagedCredentialError("private retained-key detail")

    monkeypatch.setattr(managed, "_private_key_material", unreadable)
    with item.child, pytest.raises(ReplanRejected) as error:
        item.settle(item.child, observed)
    recovery = advice(Invocation("apply"), error.value)
    assert recovery.reason_code == "predecessor-credential-unproven"
    assert recovery.repair_prerequisite and "private retained-key" not in recovery.text()
    assert before == (item.child.path.read_bytes(), item.cloud.calls)


@pytest.mark.parametrize("boundary", ["parent", "child", "package", "lifecycle", "approval"])
def test_authority_drift_after_preview_refuses_before_settlement(prefix, boundary):
    import os

    from nebius_vpngw.deploy.operator_guest import publish
    from nebius_vpngw.deploy.operator_keys import protected_directory
    from nebius_vpngw.deploy.package_reservation import path_for
    from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus

    item = prefix
    proposed = item.inspect()
    requests = list(item.cloud.calls)
    if boundary in {"parent", "child"}:
        owner = item.parent if boundary == "parent" else item.child
        with owner:
            owner.save()
    elif boundary in {"package", "approval"}:
        path = (
            proposed.path
            if boundary == "approval"
            else path_for(item.parent.root, item.parent.pending()["execution_id"], "gateway-a-0")
        )
        protected_directory(path.parent)
        publish(path, None, dict(content="{}", uid=os.getuid(), gid=os.getgid(), mode=0o600))
    else:
        item.options["lifecycle"] = NS(
            status=VMHALifecycleStatus.ACTIVE, transaction=None, record_sha256="f" * 64
        )
    before = item.parent.path.read_bytes(), item.child.path.read_bytes()
    with pytest.raises((RuntimeError, ValueError, KeyError)):
        proposed.execute(refresh=item.inspect, observe=item.observe, settle=item.settle)
    assert before == (item.parent.path.read_bytes(), item.child.path.read_bytes())
    assert requests == item.cloud.calls
    assert not (item.parent.root / "history").exists()
    if boundary != "approval":
        assert not proposed.path.exists()


def test_changed_iam_identity_after_preview_cannot_be_adopted(prefix):
    item = prefix
    proposed = item.inspect()
    item.cloud.services["service-account"].item.metadata.id = "foreign-service-account"
    before = item.parent.path.read_bytes(), item.child.path.read_bytes(), list(item.cloud.calls)
    with pytest.raises((RuntimeError, ValueError)):
        proposed.execute(refresh=item.inspect, observe=item.observe, settle=item.settle)
    assert before == (item.parent.path.read_bytes(), item.child.path.read_bytes(), item.cloud.calls)
    assert not proposed.path.exists()


@pytest.mark.parametrize("mode", ["dry-run", "decline", "passive", "approve"])
def test_cli_credential_settlement_has_separate_approval_and_read_only_preview(
    prefix, monkeypatch, mode
):
    import click

    from nebius_vpngw import cli
    from nebius_vpngw.deploy.apply_command import ReplanReady
    from nebius_vpngw.deploy.apply_result import ApplyReport
    from nebius_vpngw.deploy.recovery import PredecessorSettlementRequired

    item = prefix
    proposed = item.inspect()
    before = item.child.path.read_bytes(), item.parent.path.read_bytes()
    original_requests = list(item.cloud.calls)

    def inspect_credentials(**kwargs):
        assert kwargs["node_ids"] == ("a", "b")
        assert kwargs["purpose"] == "ha-controller"
        assert kwargs["tenant_id"] == "selected-tenant" and kwargs["region_id"] == "selected-region"
        return item.observe(kwargs["enrollment"])

    monkeypatch.setattr(cli, "inspect_managed_vm_ha_credentials", inspect_credentials)
    native_settle = managed.settle_managed_vm_ha_enrollment

    def settle_credentials(observed, *, enrollment, tenant_id, region_id):
        assert (tenant_id, region_id) == ("selected-tenant", "selected-region")
        native_settle(
            observed,
            enrollment=enrollment,
            tenant_id=tenant_id,
            region_id=region_id,
            client_factory=lambda *_: (item.cloud, True),
        )

    monkeypatch.setattr(managed, "settle_managed_vm_ha_enrollment", settle_credentials)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.typer, "confirm", lambda *_a, **_kw: mode == "approve")
    error = (
        ReplanReady
        if mode == "approve"
        else PredecessorSettlementRequired
        if mode == "passive"
        else click.exceptions.Exit
    )
    report = ApplyReport()
    with pytest.raises(error):
        cli._review_unused_apply_plan(
            item.parent,
            local=item.options["local"],
            lifecycle_store=NS(read=lambda **_: None),
            supplied=None,
            dry_run=mode == "dry-run",
            passive=mode == "passive",
            report=report,
            artifact=item.options["artifact"],
            tenant_id="selected-tenant",
            region_id="selected-region",
        )
    assert report.mapping()["pending"] == ("settle-credential-predecessor",)
    if mode == "approve":
        assert item.parent.pending() is None
        assert set(item.cloud.operations) == {"service-account", "group"}
    else:
        assert before == (item.child.path.read_bytes(), item.parent.path.read_bytes())
        assert item.cloud.calls == original_requests
        assert not proposed.path.exists()
