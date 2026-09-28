import json
from dataclasses import asdict, replace

import pytest

from nebius_cxcli import soperator_install_observability_repair as repair_owner
from nebius_cxcli.deployment_state import digest
from nebius_cxcli.soperator_install_checks_repair import prepare_install_input_repair
from nebius_cxcli.soperator_receipt_io import write_owner_only_json
from nebius_cxcli.soperator_release_reconciler import (
    SoperatorReconcileRepairLineage,
    reconcile_soperator_release,
)
from test_soperator_observability_repair_contract import (  # noqa: F401
    bundles,
    interrupted,
    successor_intent,
)
from test_soperator_release_reconciler import _artifacts, _callbacks, _source


@pytest.fixture
def historical(interrupted, monkeypatch):  # noqa: F811
    paths, repair, successor = successor_intent(interrupted)
    paths.flux_dir.mkdir(parents=True, exist_ok=True)
    (paths.flux_dir / repair_owner.VALUES_FILE).write_text(
        'apiVersion: v1\nkind: ConfigMap\ndata:\n  values.yaml: "{}"\n'
    )
    repair["targetRef"] = successor.target_ref
    repair["replacementFiles"] = repair_owner._file_hashes(repair_owner._files(paths.flux_dir))
    successor = replace(
        successor,
        desired_values_sha256=repair["replacementFiles"][repair_owner.VALUES_FILE],
        admission_sha256=digest({"installObservabilityRepair": repair}),
    )
    repair_path = paths.reports_dir / "soperator-install-observability-repair-cluster-a.json"
    write_owner_only_json(repair_path, repair)
    repair_owner.seal_successor_intent(paths, successor, repair, lambda: None)
    frozen, strategy = interrupted[3], interrupted[-1]
    common = dict(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=frozen.snapshot,
        source=_source(frozen.snapshot),
        artifacts=_artifacts(frozen.snapshot),
    )
    completed_path = reconcile_soperator_release(
        **common,
        callbacks=_callbacks([]),
        operation_spec=successor,
        repair_lineage=SoperatorReconcileRepairLineage(
            predecessor_receipt=repair["predecessorReceipt"],
            previous_operation_spec_sha256=repair["previousOperationSpecSha256"],
            resume_phase="apply-declarative-release",
            reason=repair_owner.REASON,
        ),
    )
    current = replace(
        successor,
        intervention_generation=0,
        admission_sha256=digest({"mode": "not-required"}),
        infrastructure_plan_sha256=digest("later-plan"),
        scheduling_sha256=digest("later-scheduling"),
    )

    def fail():
        raise RuntimeError("interrupted later installation")

    with pytest.raises(RuntimeError, match="interrupted later installation"):
        reconcile_soperator_release(
            **common,
            callbacks=replace(_callbacks([]), accept_checks=fail),
            operation_spec=current,
        )
    sealed = []
    monkeypatch.setattr(repair_owner, "_bind_repair_admission", lambda *a, **kw: sealed.append(kw))
    kwargs = dict(
        paths=paths,
        target_ref="cluster-a",
        scheduling_journal={"operationSpecSha256": digest(asdict(current))},
        local_scheduling_journal=None,
        env={},
        kube_context="cluster",
        assert_authority=lambda: None,
    )
    return kwargs, repair_path, completed_path, current, sealed


def test_completed_historical_repair_does_not_rebind_a_later_install(historical):
    kwargs, repair_path, completed_path, current, sealed = historical
    before = {p: p.read_bytes() for p in kwargs["paths"].reports_dir.glob("*.json")}
    assert prepare_install_input_repair(**kwargs) is None
    assert {p: p.read_bytes() for p in before} == before
    assert sealed and all(call["create"] is False for call in sealed)


@pytest.mark.parametrize("bound", ["predecessor", "successor"])
def test_repair_still_applies_to_its_own_operation(historical, bound):
    kwargs, repair_path, completed_path, _, _ = historical
    repair = json.loads(repair_path.read_text())
    kwargs["scheduling_journal"]["operationSpecSha256"] = (
        repair["previousOperationSpecSha256"]
        if bound == "predecessor"
        else digest(json.loads(completed_path.read_text())["operation"]["spec"])
    )
    assert prepare_install_input_repair(**kwargs) == repair


@pytest.mark.parametrize(
    "field,value",
    [
        ("nebius_cluster_id", "foreign"),
        ("checks_policy_sha256", digest("changed")),
        ("intervention_generation", 1),
        ("admission_sha256", digest("other-admission")),
    ],
)
def test_later_operation_cannot_change_admitted_inputs(historical, field, value):
    kwargs, _, _, current, _ = historical
    current_sha = digest(asdict(current))
    for path in kwargs["paths"].reports_dir.glob("soperator-release-reconcile-*.json"):
        receipt = json.loads(path.read_text())
        if digest(receipt["operation"]["spec"]) == current_sha:
            receipt["operation"]["spec"][field] = value
            kwargs["scheduling_journal"]["operationSpecSha256"] = digest(
                receipt["operation"]["spec"]
            )
            write_owner_only_json(path, receipt)
            break
    else:
        pytest.fail("Current receipt missing")
    with pytest.raises(RuntimeError, match="matching inputs"):
        prepare_install_input_repair(**kwargs)


@pytest.mark.parametrize(
    "change", ["incomplete", "transition", "missing", "duplicate", "foreign", "inputs", "seal"]
)
def test_ambiguous_or_changed_history_never_skips_repair(historical, change):
    kwargs, repair_path, completed_path, current, _ = historical
    if change in {"incomplete", "transition", "duplicate"}:
        receipt = json.loads(completed_path.read_text())
        if change == "incomplete":
            receipt["status"] = "recovery-required"
        if change == "transition":
            receipt["transitions"][-1]["receiptSha256"] = digest("tampered")
        destination = (
            completed_path
            if change != "duplicate"
            else completed_path.with_name("soperator-release-reconcile-duplicate.json")
        )
        write_owner_only_json(destination, receipt)
    if change == "missing":
        completed_path.unlink()
    if change == "foreign":
        kwargs["target_ref"] = "foreign"
        repair_path.rename(
            repair_path.with_name("soperator-install-observability-repair-foreign.json")
        )
    if change == "inputs":
        (kwargs["paths"].flux_dir / repair_owner.VALUES_FILE).write_text("changed")
    if change == "seal":
        intent_path = (
            kwargs["paths"].reports_dir
            / "soperator-recovery-observability-successor-cluster-a.json"
        )
        intent = json.loads(intent_path.read_text())
        intent["repairSha256"] = digest("wrong")
        write_owner_only_json(intent_path, intent)
    with pytest.raises((RuntimeError, ValueError)):
        prepare_install_input_repair(**kwargs)
