"""The real CLI campaign callback establishes durability before loading checks."""

import ast
import copy
import inspect
from pathlib import Path
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
    monkeypatch.setattr(
        "nebius_cxcli.soperator_acceptance_hooks.verify_policy_hooks", lambda *a: None
    )

    from passive_scheduler_fakes import DESIRED_SCHEDULER

    monkeypatch.setattr(
        "nebius_cxcli.soperator_passive_policy._rendered_scheduler",
        lambda *_args: copy.deepcopy(DESIRED_SCHEDULER),
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
    tree = ast.parse(inspect.getsource(cli._run_soperator_upgrade_campaign).replace("cli.", ""))
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


def test_interrupted_campaign_reports_stop_and_resumes_completed_prefix(tmp_path):
    from collections import Counter

    from nebius_cxcli.soperator_full_stack_upgrade import campaign_receipt_path
    from nebius_cxcli.soperator_status import read_soperator_operation_status
    from nebius_cxcli.soperator_upgrade_supervisor import execute_committed_soperator_upgrade
    from test_soperator_status import _paths

    paths, intent = _paths(tmp_path), _intent()
    path = campaign_receipt_path(paths.project_dir, target_ref=intent.target_ref)
    interruption = KeyboardInterrupt()
    interrupted = False
    effects, restorations = [], []

    def segment(name):
        nonlocal interrupted
        if name == intent.segments[1] and not interrupted:
            interrupted = True
            raise interruption
        effects.append(name)
        return CampaignSegmentResult(evidence={"ready": True})

    def restore(record, existing):
        restorations.append(True)
        return {"restored": True}

    def run():
        return run_campaign(
            path=path,
            intent=intent,
            segment_executors={name: lambda name=name: segment(name) for name in intent.segments},
            enter_maintenance=lambda record, existing: {"active": True},
            restore_maintenance=restore,
            assert_fence=lambda: None,
        )

    # Exercise the actual CLI callback so status priority and preserved evidence
    # cannot drift behind a test-only stop-report implementation.
    tree = ast.parse(inspect.getsource(cli._run_soperator_upgrade_campaign))
    callback = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_record_parent_stop"
    )
    module = ast.fix_missing_locations(ast.Module(body=[callback], type_ignores=[]))
    environment = {"cli": cli, "receipt_path": path, "intent": intent}
    exec(compile(module, "<production-campaign-stop>", "exec"), environment)
    with pytest.raises(KeyboardInterrupt) as caught:
        execute_committed_soperator_upgrade(run, on_stop=environment["_record_parent_stop"])
    assert caught.value is interruption
    receipt = load_campaign_receipt(path)
    assert receipt.intent_sha256 == intent.digest
    assert receipt.maintenance == "active"
    assert not restorations
    completed_prefix = tuple(effects)
    assert completed_prefix
    status = read_soperator_operation_status(paths=paths, target_ref=intent.target_ref)
    assert status.status == "recovery-required"

    assert execute_committed_soperator_upgrade(run).status == "complete"
    assert restorations == [True]
    counts = Counter(effects)
    assert all(counts[name] == 1 for name in completed_prefix)
    assert read_soperator_operation_status(paths=paths, target_ref=intent.target_ref) is None


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
    native_transition = object()

    def stage(*args, **kwargs):
        assert kwargs["native_transition"] is native_transition
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
    tree = ast.parse(
        Path(cli.__file__).with_name("soperator_campaign_cli.py").read_text().replace("cli.", "")
    )
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
        "_native_checks_transition": lambda: native_transition,
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


def test_campaign_checks_reload_current_retirement_after_later_publication(tmp_path, monkeypatch):
    from dataclasses import replace

    from nebius_cxcli import flux_ops, soperator_release_order
    from nebius_cxcli.soperator_failures import SoperatorSafetyPauseError
    from nebius_cxcli.soperator_full_stack_upgrade import CampaignNativeTransitionStore
    from nebius_cxcli.soperator_operation import soperator_sha256
    from test_soperator_graph_transition import Cluster, desired, transition

    intent, path = _intent(), tmp_path / "campaign.json"
    _write_receipt(path, replace(_new_receipt(intent), maintenance="active"))
    store = CampaignNativeTransitionStore(path, intent)
    cluster = Cluster()
    graph = {"releases": [{"releaseName": "retained"}]}
    operation = transition(cluster, store.write)
    operation.state["admission"].update(
        target={"targetRef": intent.target_ref, "clusterId": "cluster", "kubernetesUid": "kube"},
        desiredGraphSha256=soperator_sha256(graph),
    )
    operation.state["admissionSha256"] = soperator_sha256(operation.admission)
    _write_receipt(
        path,
        replace(
            load_campaign_receipt(path),
            maintenance_evidence={
                "events": [{"action": "native-graph-admitted", "transition": operation.state}]
            },
        ),
    )
    operation.fence()
    operation.publish(desired(cluster))
    operation.wait_absent(timeout=0, interval=0)
    monkeypatch.setattr(flux_ops, "_rendered_soperator_graph_contract", lambda _path: graph)
    monkeypatch.setattr(soperator_release_order, "execution_release_graph", lambda value: value)
    tree = ast.parse(Path(cli.__file__).with_name("soperator_campaign_cli.py").read_text())
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_native_checks_transition"
    )
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    environment = {
        "__package__": "nebius_cxcli",
        "cli": SimpleNamespace(
            Any=Any,
            Path=Path,
            _run_soperator_upgrade_process=lambda args, **kwargs: cluster.run(
                args, input_text=kwargs.get("input_text")
            ),
        ),
        "_assert_campaign_authority": lambda: None,
        "target_paths": SimpleNamespace(flux_dir=tmp_path),
        "intent": intent,
        "cluster_id": "cluster",
        "kubernetes_uid": "kube",
        "kube_env": {},
        "campaign_native_store": store,
        "frozen": SimpleNamespace(snapshot=object(), source=SimpleNamespace(source_dir=tmp_path)),
    }
    exec(compile(module, "<production-native-checks-callback>", "exec"), environment)
    load = environment["_native_checks_transition"]
    checks = load()
    checks.suspend_parent(True)
    checks.publish_parent({**cluster.parent, "spec": {"suspend": True, "values": {"checks": True}}})
    replay = load()
    assert replay.state["parentPublication"] == checks.state["parentPublication"]
    replay.suspend_parent(False)
    cluster.parent["metadata"]["uid"] = "replacement"
    before = len(cluster.events)
    with pytest.raises(SoperatorSafetyPauseError, match="replaced"):
        load()
    assert len(cluster.events) == before


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
    ("change", "error_type", "message"),
    [
        ({"maintenance": "pending"}, cli.SoperatorSafetyPauseError, "authority is unavailable"),
        ({"status": "complete"}, RuntimeError, "inconsistent state"),
        ({"cluster_id": "other-cluster"}, RuntimeError, "identity does not match intent"),
    ],
)
def test_campaign_main_authority_requires_active_exact_campaign(
    tmp_path, change, error_type, message
):
    from dataclasses import asdict, replace

    from nebius_cxcli.soperator_receipt_io import write_owner_only_json
    from test_soperator_flux_sources import _main_identity

    intent, path = _intent(), tmp_path / "campaign.json"
    receipt = replace(_new_receipt(intent), maintenance="active")
    # Corruption bypasses the production writer's semantic validation.
    write_owner_only_json(path, asdict(replace(receipt, **change)))
    before = path.read_bytes()
    with pytest.raises(error_type, match=message):
        CampaignMainWorkloadAuthority(path, intent, lambda: None).freeze(_main_identity())
    assert path.read_bytes() == before


@pytest.mark.parametrize("converged", [True, False])
def test_final_desired_proof_runs_after_checks_restore_and_before_job_restore(
    tmp_path, monkeypatch, converged
):
    from contextlib import nullcontext

    intent = _intent()
    assert intent.requires_fresh_checks
    receipt_path = tmp_path / "campaign.json"
    events = []
    tree = ast.parse(inspect.getsource(cli._run_soperator_upgrade_campaign))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_final_readiness"
    )
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    monkeypatch.setattr(cli, "_paths_for_target_flux_dir", lambda *a: tmp_path)
    monkeypatch.setattr(
        cli, "run_final_runtime_validation_boundary", lambda **kw: ([], ([], (), (), (), {}), {})
    )
    monkeypatch.setattr(cli, "final_node_group_capacity_snapshot", lambda **kw: {})
    monkeypatch.setattr(
        cli,
        "wait_for_soperator_release_graph",
        lambda *a, **kw: events.append("graph-after-policy"),
    )

    def proof(**kw):
        assert events[-2:] == ["restore-checks-policy", "graph-after-policy"]
        events.append("desired-proof")
        if not converged:
            raise RuntimeError("desired settings drift")
        return {"ready": True}

    environment = {
        "cli": cli,
        "deployment_hooks": SimpleNamespace(final_intent=lambda x: x, verify_desired=proof),
        "intent": intent,
        "paths": tmp_path,
        "selected_target": {},
        "kube_env": {},
        "config_path": tmp_path / "config.yaml",
        "cluster_id": intent.cluster_id,
        "receipt_path": receipt_path,
        "_runtime_readiness": lambda *a: CampaignSegmentResult({"ready": True}),
        "_fast_campaign_smoke": lambda **kw: events.append(("smoke", kw)) or {"status": "passed"},
        "_campaign_checks": lambda: SimpleNamespace(
            documents_projection=lambda: None,
            readiness_exemptions=lambda: {},
            finalize=lambda **kw: events.append("restore-checks-policy") or {"restored": True},
        ),
        "upgrade_progress": SimpleNamespace(phase=lambda *a, **kw: nullcontext()),
        "executor": SimpleNamespace(list_node_groups=lambda _: []),
        "_assert_campaign_authority": lambda: None,
    }
    exec(compile(module, "production-final-readiness", "exec"), environment)
    executors = {name: lambda: CampaignSegmentResult({"ready": True}) for name in intent.segments}
    executors["final-readiness"] = environment["_final_readiness"]

    def run():
        return run_campaign(
            path=receipt_path,
            intent=intent,
            segment_executors=executors,
            enter_maintenance=lambda *a: {"active": True},
            restore_maintenance=lambda *a: events.append("restore-jobs") or {"restored": True},
            assert_fence=lambda: None,
        )

    if converged:
        assert run().status == "complete"
        assert events[-2:] == ["desired-proof", "restore-jobs"]
        assert not any(isinstance(event, tuple) for event in events)
        assert run().status == "complete"
        assert events[-1] == ("smoke", {"verify_only": True})
        assert events.count("restore-jobs") == 1
    else:
        with pytest.raises(RuntimeError, match="desired settings drift"):
            run()
        assert "restore-jobs" not in events
        assert load_campaign_receipt(receipt_path).status != "complete"
