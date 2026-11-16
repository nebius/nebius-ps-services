from dataclasses import replace
from types import SimpleNamespace as NS

import pytest
from nebius.api.nebius.compute import v1 as compute
from test_credential_enrollment import missing
from test_provisioning_execute import bootstrap, parent
from tests.unit import test_provisioning_settlement

from nebius_vpngw.deploy import provisioning_replan, provisioning_retirement
from nebius_vpngw.deploy.provisioning_execute import execute
from nebius_vpngw.deploy.provisioning_inventory import snapshot
from nebius_vpngw.deploy.provisioning_journal import requests_from_plan
from nebius_vpngw.deploy.provisioning_plan import Request

prefix = test_provisioning_settlement.prefix


@pytest.fixture
def replan(prefix, monkeypatch, tmp_path):
    item = prefix
    native = compute.InstanceServiceClient

    def absent(*_, **__):
        raise missing()

    def service(sdk):
        instance = native(sdk)
        instance.get_by_name = absent
        return instance

    monkeypatch.setattr(compute, "InstanceServiceClient", service)
    monkeypatch.setattr(provisioning_replan, "ProvisioningJournal", lambda *_: item.child)
    for name in (
        "EnrollmentJournal",
        "TrustJournal",
        "AttachmentJournal",
        "RouteJournal",
        "ReplacementJournal",
    ):
        factory = getattr(provisioning_replan, name)
        monkeypatch.setattr(
            provisioning_replan,
            name,
            lambda p, g, factory=factory, name=name: factory(p, g, root=tmp_path / name),
        )

    def unused(*_):
        raise AssertionError("No credential or SSH owner in this fixture")

    def observe(previous, owners):
        return provisioning_replan.observe_prerequisites(
            item.command,
            previous,
            owners,
            sdk=object(),
            ssh_observe=unused,
            credential_observe=unused,
        )

    item.options = dict(local={"changed": True}, artifact="f" * 64, lifecycle=None, observe=observe)
    item.inspect = lambda: provisioning_replan.inspect(item.command, **item.options)
    return item


def test_fresh_approval_reuses_owned_disk_without_running_obsolete_compute(replan, tmp_path):
    item = replan
    before = item.child.path.read_bytes()
    proposed = item.inspect()
    assert not proposed.path.exists()
    proposed.execute(sdk=object(), refresh=item.inspect)
    assert item.command.pending() is None
    assert item.child.snapshot()["transaction"]["phase"] == "pending"
    assert set(item.resources) == {"disk-created"}
    assert provisioning_retirement.receipt(item.child) is not None
    assert before in [p.read_bytes() for p in (item.child.root / "history").glob("*.json")]
    retained = provisioning_retirement.retained_disks(item.child, object())
    assert [disk.metadata.id for disk in retained.values()] == ["disk-created"]
    resource = item.resources["disk-created"]
    compute_recipe = requests_from_plan(item.desired)[1]
    request = compute_recipe.resolve(
        item.child.snapshot()["bindings"], bootstrap=bootstrap("").user_data
    )
    request.spec.cloud_init_user_data = ""
    desired = {
        **item.desired,
        "configuration_sha256": "1" * 64,
        "requests": [
            Request.freeze(compute_recipe.key, request, guest=compute_recipe.guest).mapping()
        ],
        "observed": {resource.metadata.id: snapshot("disk", resource)},
    }
    # New parent approval sees actual state and the retained ID; the old plan is
    # never completed or resumed to force its remaining Compute recipe to run.
    command, envelope = parent(tmp_path, desired)
    envelope = replace(envelope, configuration_sha256="1" * 64)
    assert envelope.approval != proposed.predecessor["transaction"]["approval"]
    with command:
        command.begin(envelope, intents={"provisioning": desired})
        command.started("provisioning")
        execute(object(), item.child, command, desired, bootstrap=bootstrap)
    assert [kind for kind, *_ in item.submissions] == ["disk", "compute"]
    assert item.resources["compute-created"].spec.boot_disk.existing_disk.id == "disk-created"


@pytest.mark.parametrize(
    "name", ["credentials", "ssh", "attachments", "provisioning", "routes", "replacement"]
)
def test_provisioning_settlement_excludes_every_writer(replan, name):
    item = replan
    proposed = item.inspect()
    owner = proposed.owners[name]
    with (
        type(owner)(owner.project, owner.gateway, root=owner.root),
        pytest.raises(RuntimeError, match="still running"),
    ):
        proposed.execute(sdk=object(), refresh=item.inspect)
    assert not proposed.path.exists()
    assert item.command.pending() is not None


@pytest.mark.parametrize("boundary", ["child-seal", "parent-receipt"])
def test_provisioning_retirement_resumes_after_acknowledgement_loss(replan, monkeypatch, boundary):
    item = replan
    proposed = item.inspect()
    fired = False
    with monkeypatch.context() as patch:
        if boundary == "child-seal":
            native = provisioning_retirement.seal

            def fail_once(*args):
                nonlocal fired
                native(*args)
                if not fired:
                    fired = True
                    raise OSError("lost child retirement acknowledgement")

            patch.setattr(provisioning_retirement, "seal", fail_once)
        else:
            native = item.command.save

            def fail_once():
                nonlocal fired
                if item.command.state["transaction"] is None and not fired:
                    fired = True
                    raise OSError("lost parent retirement acknowledgement")
                native()

            patch.setattr(item.command, "save", fail_once)
        with pytest.raises(OSError):
            proposed.execute(sdk=object(), refresh=item.inspect)
    resumed = item.inspect()
    assert resumed.resumed and resumed.approval == proposed.approval
    resumed.execute(sdk=object(), refresh=item.inspect)
    assert item.command.pending() is None
    assert set(item.resources) == {"disk-created"}


@pytest.mark.parametrize("drift", ["attached", "identity", "specification"])
def test_retained_disk_must_still_match_its_creation_and_be_unattached(replan, drift):
    item = replan
    proposed = item.inspect()
    proposed.execute(sdk=object(), refresh=item.inspect)
    disk = item.resources["disk-created"]
    if drift == "attached":
        disk.status.read_write_attachment = "foreign-compute"
    elif drift == "identity":
        disk.metadata.id = "same-name-foreign-disk"
    else:
        disk.spec.size_gibibytes = 1
    before = item.child.path.read_bytes(), list(item.submissions)
    with pytest.raises((ValueError, RuntimeError)):
        provisioning_retirement.retained_disks(item.child, object())
    assert before == (item.child.path.read_bytes(), item.submissions)


def test_successor_cannot_orphan_retired_creation_or_resume_obsolete_steps(replan):
    item = replan
    proposed = item.inspect()
    proposed.execute(sdk=object(), refresh=item.inspect)
    before = item.child.path.read_bytes(), list(item.submissions)
    with item.child:
        old = item.child.state["transaction"]
        with pytest.raises(RuntimeError, match="newly approved successor"):
            item.child.begin(
                old["plan"], approval=old["approval"], execution_id=old["execution_id"]
            )
        with pytest.raises(ValueError, match="owned provisioning resource"):
            item.child.begin(item.desired, approval="c" * 64, execution_id="d" * 32)
    assert before == (item.child.path.read_bytes(), item.submissions)


def test_guest_appearance_after_preview_refuses_before_archival_or_settlement(replan, monkeypatch):
    item = replan
    proposed = item.inspect()
    monkeypatch.setattr(
        compute,
        "InstanceServiceClient",
        lambda _: NS(get_by_name=lambda *_a, **_k: NS(wait=lambda: NS())),
    )
    before = item.command.path.read_bytes(), item.child.path.read_bytes(), list(item.submissions)
    with pytest.raises(ValueError, match="existing guest"):
        proposed.execute(sdk=object(), refresh=item.inspect)
    assert before == (
        item.command.path.read_bytes(),
        item.child.path.read_bytes(),
        item.submissions,
    )
    assert not proposed.path.exists()


@pytest.mark.parametrize("mode", ["dry-run", "decline", "approve"])
def test_cli_precompute_review_is_pure_until_separate_approval(replan, monkeypatch, mode):
    import click

    from nebius_vpngw import cli
    from nebius_vpngw.deploy.apply_command import ReplanReady
    from nebius_vpngw.deploy.apply_result import ApplyReport

    item = replan
    proposed = item.inspect()
    before = item.command.path.read_bytes(), item.child.path.read_bytes(), list(item.submissions)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.typer, "confirm", lambda *_a, **_k: mode == "approve")
    report = ApplyReport()
    with pytest.raises(ReplanReady if mode == "approve" else click.exceptions.Exit):
        cli._review_provisioning_prefix(
            item.command,
            manager=NS(_get_client=lambda: object()),
            deployment=None,
            local=item.options["local"],
            lifecycle_store=NS(read=lambda **_: None),
            supplied=None,
            dry_run=mode == "dry-run",
            report=report,
            artifact=item.options["artifact"],
            tenant_id="selected-tenant",
            region_id="selected-region",
        )
    assert report.started is (mode == "approve")
    assert report.mapping()["pending"] == ("settle-provisioning-predecessor",)
    if mode == "approve":
        assert item.command.pending() is None
        assert set(item.resources) == {"disk-created"}
    else:
        assert before == (
            item.command.path.read_bytes(),
            item.child.path.read_bytes(),
            item.submissions,
        )
        assert not proposed.path.exists()
