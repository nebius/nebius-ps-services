"""The real CLI campaign callback establishes durability before loading checks."""

import ast
import inspect
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from nebius_cxcli import cli
from nebius_cxcli.soperator_checks_campaign import SoperatorCampaignChecks
from nebius_cxcli.soperator_checks_phase import ChecksPhase, ChecksPhaseContext
from nebius_cxcli.soperator_failures import SoperatorFailureDisposition
from nebius_cxcli.soperator_full_stack_upgrade import (
    CampaignMainWorkloadAuthority,
    CampaignSegmentResult,
    _new_receipt,
    _write_receipt,
    load_campaign_receipt,
    run_campaign,
)
from test_soperator_checks_login import native_source as native_source
from test_soperator_full_stack_upgrade import _intent


def test_source_checks_compile_native_login_scripts_with_cluster_identity(
    tmp_path, monkeypatch, native_source
):
    values_path = native_source / "helm/soperator-activechecks/values.yaml"
    values = yaml.safe_load(values_path.read_text())
    for row in values["checks"].values():
        row.update(enabled=True, checkType="k8sJob", runAfterCreation=True, suspend=True)
    values_path.write_text(yaml.safe_dump(values))
    monkeypatch.setattr(
        "nebius_cxcli.soperator_checks_policy._render_execution_specs",
        lambda _chart, _overrides: {name: {"schedule": "0 */6 * * *"} for name in values["checks"]},
    )

    def no_live_calls(*_args):
        pytest.fail("source policy compilation must not access the live target")

    checks = SoperatorCampaignChecks(
        operation_id="campaign",
        reports_dir=tmp_path / "reports",
        source_dir=native_source,
        cluster_name="lab",
        kubernetes=no_live_calls,
        slurm=no_live_calls,
        assert_authority=no_live_calls,
        load_target=no_live_calls,
        apply_target=no_live_calls,
        partition_preimages=no_live_calls,
        reservation="reserve",
        emit=no_live_calls,
        target_writers=no_live_calls,
        release_maintenance=no_live_calls,
        verify_maintenance_released=no_live_calls,
        recover_target=no_live_calls,
    )
    assert {rule.name for rule in checks.source.policy.rules} == {"create-user-nebius", "ssh-check"}
    assert not checks.source.path.exists()


def test_parent_check_factory_runs_after_receipt_creation(tmp_path):
    intent = _intent()
    path = tmp_path / "campaign.json"
    observations = []

    def checks():
        receipt = load_campaign_receipt(path)
        assert receipt is not None, "check initialization preceded durable campaign creation"
        assert receipt.intent_sha256 == intent.digest
        observations.append(receipt.maintenance)
        return SimpleNamespace(before_segment=lambda _: None)

    # Execute the production closure with bounded dependencies; do not replace
    # run_campaign or its receipt creation and maintenance ordering.
    tree = ast.parse(inspect.getsource(cli.soperator_upgrade_command))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_run_parent_campaign_once"
    )
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    environment = {
        "Any": Any,
        "receipt_path": path,
        "intent": intent,
        "run_campaign": run_campaign,
        "_campaign_checks": checks,
        "segment_executors": {
            name: lambda: CampaignSegmentResult(evidence={"ready": True})
            for name in intent.segments
        },
        "_enter_maintenance": lambda *_: {"ready": True},
        "_restore_maintenance": lambda *_: {"restored": True},
        "_assert_campaign_fence": lambda: None,
        "_soperator_upgrade_failure_disposition": lambda *_: SoperatorFailureDisposition.RETRY,
        "SoperatorFailureDisposition": SoperatorFailureDisposition,
    }
    exec(compile(module, "<production-campaign-callback>", "exec"), environment)
    result = environment["_run_parent_campaign_once"]()
    assert result.status == "complete"
    assert observations


@pytest.mark.parametrize("recovery", [False, True])
def test_campaign_policy_paths_freeze_main_before_staged_readiness(tmp_path, monkeypatch, recovery):
    from dataclasses import replace

    from nebius_cxcli import flux_ops, soperator_checks_catchup_flux
    from test_soperator_flux_sources import (
        _main_identity,
        _main_release_payload,
        _main_stage_contract,
        _main_wait_target,
    )

    intent = _intent()
    path = tmp_path / "campaign.json"
    _write_receipt(path, replace(_new_receipt(intent), maintenance="active"))
    lease_reads = []
    authority = CampaignMainWorkloadAuthority(path, intent, lambda: lease_reads.append("held"))
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *args, **kwargs: _main_release_payload(ready=True, stalled=False),
    )
    monkeypatch.setattr(
        flux_ops,
        "_observe_soperator_main_workload_identity",
        lambda *args, **kwargs: _main_identity(),
    )

    context = ChecksPhaseContext(ChecksPhase.MAINTENANCE, "reserve")

    def stage(*args, **kwargs):
        if not recovery:
            assert kwargs["checks_context"] is context
        flux_ops._wait_for_soperator_release_stage(
            _main_stage_contract(),
            0,
            cache_dir=tmp_path,
            env={},
            timeout_seconds=0,
            poll_interval_seconds=0.01,
            main_target=_main_wait_target(),
            freeze_main_workload_authority=kwargs.get("freeze_main_workload_authority"),
        )

    monkeypatch.setattr(soperator_checks_catchup_flux, "recover_staged_checks", stage)
    name = "_recover_target_checks" if recovery else "_apply_target"
    tree = ast.parse(inspect.getsource(cli.soperator_upgrade_command))
    function = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
    )
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    environment = {
        **vars(cli),
        "_assert_campaign_authority": lambda: lease_reads.append("held"),
        "main_workload_authority": authority,
        "target_paths": object(),
        "kube_env": {},
        "kube_context": "test",
        "frozen": SimpleNamespace(source=SimpleNamespace(source_dir=str(tmp_path))),
        "apply_staged_soperator_release": stage,
    }
    exec(compile(module, "<production-policy-callback>", "exec"), environment)
    if recovery:
        environment[name](object())
    else:
        environment[name](object(), context)
    receipt = load_campaign_receipt(path)
    assert (
        receipt.maintenance_evidence["checksMainWorkloadAuthority"]["identity"]["uid"] == "main-uid"
    )
    assert lease_reads


def test_campaign_main_authority_survives_resume_and_generation_refinement(tmp_path):
    from dataclasses import replace

    from test_soperator_flux_sources import _main_identity

    intent, path = _intent(), tmp_path / "campaign.json"
    _write_receipt(path, replace(_new_receipt(intent), maintenance="active"))
    identity = _main_identity()
    authority = CampaignMainWorkloadAuthority(path, intent, lambda: None)
    assert authority.freeze(identity) == identity
    before = path.read_bytes()
    assert CampaignMainWorkloadAuthority(path, intent, lambda: None).freeze(identity) == identity
    assert path.read_bytes() == before
    refined = replace(identity, generation=4, observed_generation=4)
    assert authority.freeze(refined) == refined
    with pytest.raises(cli.SoperatorSafetyPauseError, match="authority changed"):
        authority.freeze(identity)


def test_policy_authority_written_before_segment_is_retained_by_campaign(tmp_path):
    from test_soperator_flux_sources import _main_identity

    intent, path = _intent(), tmp_path / "campaign.json"
    identity = _main_identity()
    authority = CampaignMainWorkloadAuthority(path, intent, lambda: None)

    def execute():
        receipt = load_campaign_receipt(path)
        assert receipt is not None
        assert receipt.maintenance_evidence["checksMainWorkloadAuthority"]["identity"]["uid"] == (
            identity.uid
        )
        return CampaignSegmentResult(evidence={"status": "ready"})

    completed = run_campaign(
        path=path,
        intent=intent,
        segment_executors={name: execute for name in intent.segments},
        enter_maintenance=lambda _record, _evidence: {"state": "active"},
        restore_maintenance=lambda _record, _evidence: {"state": "restored"},
        assert_fence=lambda: None,
        verify_maintenance=lambda _segment: authority.freeze(identity),
    )
    assert completed.status == "complete"
    assert "checksMainWorkloadAuthority" in completed.maintenance_evidence["entry"]


@pytest.mark.parametrize(
    "change",
    [
        {"uid": "replacement"},
        {"source_revision": "sha256:" + "f" * 64},
        {"name": "different"},
        {"source_name": "different"},
    ],
)
def test_campaign_main_authority_rejects_identity_substitution(tmp_path, change):
    from dataclasses import replace

    from test_soperator_flux_sources import _main_identity

    intent, path = _intent(), tmp_path / "campaign.json"
    _write_receipt(path, replace(_new_receipt(intent), maintenance="active"))
    authority = CampaignMainWorkloadAuthority(path, intent, lambda: None)
    identity = _main_identity()
    authority.freeze(identity)
    before = path.read_bytes()
    with pytest.raises(cli.SoperatorSafetyPauseError, match="authority changed"):
        authority.freeze(replace(identity, generation=4, observed_generation=4, **change))
    assert path.read_bytes() == before


def test_campaign_main_authority_rejects_corruption_or_lost_lease(tmp_path):
    from dataclasses import replace

    from test_soperator_flux_sources import _main_identity

    intent, path = _intent(), tmp_path / "campaign.json"
    _write_receipt(path, replace(_new_receipt(intent), maintenance="active"))
    authority = CampaignMainWorkloadAuthority(path, intent, lambda: None)
    identity = _main_identity()
    authority.freeze(identity)
    receipt = load_campaign_receipt(path)
    evidence = dict(receipt.maintenance_evidence)
    evidence["checksMainWorkloadAuthority"]["sha256"] = "sha256:" + "0" * 64
    _write_receipt(path, replace(receipt, maintenance_evidence=evidence))
    before = path.read_bytes()
    with pytest.raises(cli.SoperatorSafetyPauseError, match="authority is invalid"):
        authority.freeze(identity)
    assert path.read_bytes() == before

    def lost():
        raise cli.SoperatorSafetyPauseError("lease lost")

    with pytest.raises(cli.SoperatorSafetyPauseError, match="lease lost"):
        CampaignMainWorkloadAuthority(path, intent, lost).freeze(identity)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [{"maintenance": "pending"}, {"status": "complete"}, {"cluster_id": "other-cluster"}],
)
def test_campaign_main_authority_requires_active_exact_campaign(tmp_path, change):
    from dataclasses import replace

    from test_soperator_flux_sources import _main_identity

    intent, path = _intent(), tmp_path / "campaign.json"
    receipt = replace(_new_receipt(intent), maintenance="active")
    _write_receipt(path, replace(receipt, **change))
    before = path.read_bytes()
    with pytest.raises(cli.SoperatorSafetyPauseError, match="authority is unavailable"):
        CampaignMainWorkloadAuthority(path, intent, lambda: None).freeze(_main_identity())
    assert path.read_bytes() == before
