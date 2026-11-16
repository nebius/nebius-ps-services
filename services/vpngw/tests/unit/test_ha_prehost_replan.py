import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_vm_ha_prehost import fixture

from nebius_vpngw.apply_approval import SCHEMA, digest
from nebius_vpngw.deploy import ha_command
from nebius_vpngw.deploy import ha_prehost_replan as replan
from nebius_vpngw.deploy.apply_command import CommandJournal, ReplanReady
from nebius_vpngw.deploy.apply_diagnostics import PreparationBlocked
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStore


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state, cloud, bindings, hosts = fixture()
    journal = CommandJournal("project", "gateway")
    command = ha_command.create(
        journal,
        scope="1" * 64,
        artifact="2" * 64,
        action_scope="3" * 64,
        targets=("vm-0", "vm-1"),
        approval="4" * 64,
        runtime_scope="5" * 64,
        report={"kind": "apply-convergence"},
        attachment_plan=None,
    )
    with journal:
        command.begin()
        command.start_runtime()
    prior_bindings = dict(state.transaction.resource_bindings)
    prior_bindings.update(
        {
            "runtime-command-execution": command.execution_id,
            "runtime-command-approval": json.dumps(
                {"schema": SCHEMA, "approval": command.approval, "scope": "5" * 64}
            ),
        }
    )
    state = replace(
        state,
        transaction=replace(
            state.transaction, resource_bindings=tuple(sorted(prior_bindings.items()))
        ),
    )
    credentials = SimpleNamespace(
        resource_bindings=lambda: {k: v for k, v in bindings.items() if k.startswith("credential-")}
    )
    credential_plan = SimpleNamespace(
        credentials=credentials,
        observation_record=lambda: {
            "action": "reuse",
            "iam_resources": {"service_account": {"action": "reuse"}},
        },
    )
    options = dict(
        lifecycle=state,
        cloud=cloud,
        hosts=hosts,
        bindings=bindings,
        desired="6" * 64,
        credential_plan=credential_plan,
        ssh_policy=SimpleNamespace(managed_action=None, assert_current=lambda: None),
    )
    return journal, command, options


def test_retirement_archives_parent_without_running_unsubmitted_steps(context):
    journal, command, options = context
    old = journal.path.read_bytes()
    proposal = replan.inspect(journal, **options)
    assert proposal is not None
    assert journal.path.read_bytes() == old  # Inspection/dry-run never archives.
    proposal.retire(replan.inspect(journal, **options))
    assert journal.snapshot()["transaction"] is None
    assert journal.retirement_completed(proposal.proof)
    histories = list(journal.root.rglob("*.json"))
    assert any(path.read_bytes() == old for path in histories)
    assert command.plan.artifact_sha256 == "2" * 64


@pytest.mark.parametrize("fault", ["parent", "host", "cloud", "credentials", "ssh", "package"])
def test_unsafe_retirement_refuses_without_mutation(context, fault, monkeypatch):
    journal, command, options = context
    before = journal.path.read_bytes()
    altered = deepcopy(options)
    if fault == "parent":
        altered["lifecycle"] = replace(
            options["lifecycle"],
            transaction=replace(
                options["lifecycle"].transaction,
                resource_bindings=tuple(
                    (k, "different" if k == "runtime-command-execution" else v)
                    for k, v in options["lifecycle"].transaction.resource_bindings
                ),
            ),
        )
    elif fault == "host":
        altered["hosts"]["gateway-0"]["writer_admission"] = False
    elif fault == "cloud":
        altered["cloud"]["members"][0]["compute_revision"] = "changed"
    elif fault == "credentials":
        altered["credential_plan"].observation_record = lambda: {"action": "create"}
    elif fault == "ssh":
        altered["ssh_policy"].managed_action = "repair"
    else:
        monkeypatch.setattr(replan.package_reservation, "read", lambda *_: {"pending": True})
    with pytest.raises((ValueError, PreparationBlocked)):
        replan.inspect(journal, **altered)
    assert journal.path.read_bytes() == before
    assert not (journal.root / "settlements").exists()


def test_approval_is_bound_to_fresh_host_evidence(context):
    journal, _, options = context
    proposal = replan.inspect(journal, **options)
    options["hosts"]["gateway-0"]["boot_id"] = "new-boot"
    changed = replan.inspect(journal, **options)
    with pytest.raises(PreparationBlocked):
        proposal.retire(changed)
    assert journal.pending() is not None


def test_poll_sequence_does_not_invalidate_settlement(context):
    journal, _, options = context
    proposal = replan.inspect(journal, **options)
    options["hosts"]["gateway-0"]["checkpoint"]["sequence"] += 1
    assert replan.inspect(journal, **options).approval == proposal.approval


@pytest.mark.parametrize("dry_run,accepted", [(True, True), (True, False), (False, False)])
def test_review_or_decline_does_not_retire_any_owner(context, dry_run, accepted):
    journal, _, options = context
    proposal = replan.inspect(journal, **options)
    before = {p: p.read_bytes() for p in journal.root.rglob("*") if p.is_file()}
    assert not proposal.review(
        approve=lambda *_a, **_k: accepted,
        refresh=lambda: pytest.fail("preview or decline refreshed effects"),
        supplied=None,
        dry_run=dry_run,
    )
    assert {p: p.read_bytes() for p in journal.root.rglob("*") if p.is_file()} == before
    assert all(not owner.root.exists() for owner in proposal.owners.values())


def test_fresh_approval_cannot_reuse_settlement_approval(context, monkeypatch):
    from nebius_vpngw import cli
    from nebius_vpngw.deploy.apply_result import ApplyReport
    from nebius_vpngw.deploy.recovery import ApprovalDeclined

    journal, _, options = context
    proposal = replan.inspect(journal, **options)
    observed = []

    def apply(**kwargs):
        observed.append(kwargs["approve_disruption"])
        if len(observed) == 1:
            assert proposal.review(
                approve=lambda *_a, **_k: True,
                refresh=lambda: replan.inspect(journal, **options),
                supplied=proposal.approval,
                dry_run=False,
            )
            raise ReplanReady()
        assert journal.snapshot()["transaction"] is None
        raise ApprovalDeclined()

    from nebius_vpngw.deploy import ha_convergence

    monkeypatch.setattr(ha_convergence, "run", lambda _api, report, **kw: apply(**kw))
    report = ApplyReport()
    with pytest.raises(ApprovalDeclined):
        cli._run_apply_reported(report, approve_disruption=proposal.approval)
    assert observed == [proposal.approval, None]
    assert report.cancelled
    assert journal.retirement_completed(proposal.proof)


def completed_credential_context(context, tmp_path, parent_ack, *, include_ssh=False):
    from nebius_vpngw.deploy.credential_enrollment import EnrollmentJournal
    from nebius_vpngw.deploy.provisioning_trust import TrustJournal, intent, selection
    from nebius_vpngw.deploy.replacement_cloud import effect_key
    from nebius_vpngw.vm_ha_managed_credentials import (
        vm_ha_authorized_key_name,
        vm_ha_service_account_name,
    )

    _, _, options = context
    credentials = options["credential_plan"].credentials
    account = credentials.resource_bindings()["credential-service-account:node-0"]
    key_id = credentials.resource_bindings()["credential-authorized-key:node-0"]
    current = {
        "action": "reuse",
        "algorithm": "RS256",
        "key_size_bits": 4096,
        "expires_at": None,
        "local_source_expected_absent": False,
        "project_id": "project",
        "purpose": "ha-controller",
        "node_ids": ["node-0", "node-1"],
        "service_account_name": vm_ha_service_account_name("gateway"),
        "authorized_key_name": vm_ha_authorized_key_name("gateway"),
        "destination": "protected-credential-source",
        "iam_resources": {
            "service_account": {"action": "reuse", "id": account},
            "group": {"action": "reuse", "id": "group"},
            "membership": {"action": "reuse", "member_id": account},
            "project_editor_permit": {
                "action": "reuse",
                "resource_id": "project",
                "role": "editor",
            },
            "authorized_key": {"action": "reuse", "id": key_id},
        },
    }
    original = deepcopy(current)
    original.update(action="create", local_source_expected_absent=True)
    original["iam_resources"]["authorized_key"] = {"action": "create", "id": None}
    journal = CommandJournal("project", "gateway", root=tmp_path / "credential-parent")
    child = EnrollmentJournal("project", "gateway")
    policy = SimpleNamespace(
        managed_action=None,
        assert_current=lambda: None,
        known_hosts_sha256="a" * 64,
        managed_receipt_sha256="b" * 64,
        fresh_identity_approval_sha256=None,
        transport_targets=(("gateway-0", "192.0.2.1"), ("gateway-1", "192.0.2.2")),
        pin_targets=(("gateway-0", "gateway-0"), ("gateway-1", "gateway-1")),
        identity_for=lambda name: SimpleNamespace(private_key=(name + "-fixture-key").encode()),
    )
    trust_intent = intent(policy, ("gateway-0", "gateway-1")) if include_ssh else None
    command = ha_command.create(
        journal,
        scope="1" * 64,
        artifact="2" * 64,
        action_scope="3" * 64,
        targets=("vm-0", "vm-1"),
        approval="4" * 64,
        runtime_scope="5" * 64,
        report={
            "kind": "apply-convergence",
            "managed_ssh_action": "repair" if include_ssh else None,
        },
        attachment_plan=None,
        credentials=original,
        node_ids=("node-0", "node-1"),
        ssh=trust_intent,
    )
    with journal:
        command.begin()
        command.start_prerequisite("credentials")
        with child:
            child.begin(original, approval=command.plan.approval, execution_id=command.execution_id)
            child.select("fixture-public-key")
            tx = child.state["transaction"]
            tx["operations"][
                effect_key(command.plan.approval, 0, "authorized-key", command.execution_id)
            ] = {
                "label": "authorized-key",
                "request_type": "CreateAuthPublicKeyRequest",
                "request_sha256": "a" * 64,
                "submission_count": 1,
                "single_attempt": True,
                "status": "settled",
                "cloud_operation_id": "settled-operation",
                "resource_id": key_id,
                "public_key_sha256": tx["selected"],
                "request_metadata": {},
            }
            receipt = child.complete(credentials.resource_bindings())
        if parent_ack:
            journal.verified("credentials", receipt)
            if trust_intent:
                command.start_prerequisite("ssh")
                with TrustJournal("project", "gateway") as trust:
                    trust.begin(
                        trust_intent,
                        approval=command.plan.approval,
                        execution_id=command.execution_id,
                    )
                    trust.select(selection(policy, tuple(trust_intent["members"])))
                    journal.verified("ssh", trust.complete())
            command.start_runtime()
    state = options["lifecycle"]
    bindings = dict(state.transaction.resource_bindings)
    bindings["runtime-command-execution"] = command.execution_id
    options["lifecycle"] = replace(
        state,
        transaction=replace(state.transaction, resource_bindings=tuple(sorted(bindings.items()))),
    )
    options["credential_plan"] = SimpleNamespace(
        credentials=credentials,
        observation_record=lambda: current,
        public_key_sha256=digest("unused"),
        command_intent=original,
        command_action="create",
    )
    options["credential_plan"].public_key_sha256 = child.snapshot()["transaction"]["selected"]
    if include_ssh:
        options["ssh_policy"] = policy
    return journal, command, options, child


@pytest.mark.parametrize("parent_ack", [False, True])
def test_completed_credential_create_is_retired_without_replaying_create(
    context, tmp_path, parent_ack
):
    journal, command, options, child = completed_credential_context(context, tmp_path, parent_ack)
    prior = child.path.read_bytes()
    proposal = replan.inspect(journal, **options)
    proposal.retire(replan.inspect(journal, **options))
    assert child.path.read_bytes() == prior
    assert journal.pending() is None
    fresh = ha_command.create(
        journal,
        scope="6" * 64,
        artifact="2" * 64,
        action_scope="7" * 64,
        targets=("vm-0", "vm-1"),
        approval="8" * 64,
        runtime_scope="9" * 64,
        report={"kind": "recovery"},
        attachment_plan=None,
        packages=ha_command.prehost_package_intent(options["lifecycle"], "a" * 64, {}),
    )
    with journal:
        fresh.begin()
    assert "credentials" not in fresh.bindings
    assert child.path.read_bytes() == prior
    # A lost reply after publishing this approved successor resumes it, rather
    # than trying to settle the old lifecycle a second time.
    assert replan.inspect(journal, **options) is None


def test_prehost_package_intent_is_new_and_lifecycle_archive_is_exact(context, tmp_path):
    from nebius_vpngw.deploy.vm_ha_prehost import operation_id, recover_prehost

    _, _, options = context
    state = options["lifecycle"]
    packages = ha_command.prehost_package_intent(state, "7" * 64, {})
    assert packages["parent"] != state.transaction.operation_id
    assert packages["parent"] == operation_id(state, "7" * 64)
    store = VMHALifecycleStore(tmp_path / "gateway.yaml")
    store.write_verified(state)
    successor = recover_prehost(
        state,
        operation_id=packages["parent"],
        approval_digest="7" * 64,
        desired_state_digest="8" * 64,
        bindings=options["bindings"],
        observation=options["cloud"],
        host_evidence=options["hosts"],
    )
    store.write_verified(successor, predecessor_sha256=state.record_sha256)
    history = store.path.parent / (store.path.name + ".history") / (state.record_sha256 + ".json")
    assert json.loads(history.read_text()) == state.to_dict()
    store.write_verified(successor, predecessor_sha256=state.record_sha256)
    assert (
        type(state).from_dict(json.loads(history.read_text())).record_sha256 == state.record_sha256
    )


@pytest.mark.parametrize(
    "name", ["credentials", "ssh", "attachments", "routes", "provisioning", "replacement"]
)
def test_prehost_retirement_excludes_every_owner(context, name):
    journal, _state, options = context
    proposal = replan.inspect(journal, **options)
    before = journal.path.read_bytes()
    with proposal.owners[name], pytest.raises(RuntimeError, match="still running"):
        proposal.retire(replan.inspect(journal, **options))
    assert journal.path.read_bytes() == before
    assert not (journal.root / "history").exists()
