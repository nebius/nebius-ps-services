import copy
import json
from dataclasses import asdict, replace

import pytest

from nebius_cxcli.soperator_checks import SoperatorChecksExecution
from nebius_cxcli.soperator_checks_policy import compile_checks_policy
from nebius_cxcli.soperator_graph_repair import (
    prepare_repair,
    seal_successor,
    sealed_binding,
    transfer_checks,
    validate_successor,
)
from nebius_cxcli.soperator_graph_transition import REPAIR_REASON
from nebius_cxcli.soperator_operation import soperator_sha256
from nebius_cxcli.soperator_release_reconciler import (
    SoperatorReconcileRepairLineage,
    reconcile_soperator_release,
    resolve_soperator_reconcile_strategy,
)
from soperator_fixtures import sample_snapshot
from test_soperator_checks import source as source
from test_soperator_checks import values as values
from test_soperator_graph_transition import Cluster, transition
from test_soperator_release_reconciler import _artifacts, _callbacks, _paths, _source, _spec


@pytest.fixture
def paused(tmp_path, source, values):
    paths = _paths(tmp_path)
    snapshot = sample_snapshot()
    strategy = resolve_soperator_reconcile_strategy(
        current_release=snapshot.release,
        target_release=snapshot.release,
        source_contract=snapshot.capability_contract,
        target_contract=snapshot.capability_contract,
        desired_state_changed=True,
    )
    policy = compile_checks_policy(source, values)
    spec = replace(_spec(snapshot, strategy, paths), checks_policy_sha256=policy.sha256)
    calls = []

    def failed():
        raise RuntimeError("unexpected predecessor child")

    with pytest.raises(RuntimeError, match="unexpected predecessor"):
        reconcile_soperator_release(
            paths=paths,
            target_ref=spec.target_ref,
            ownership="managed",
            strategy=strategy,
            snapshot=snapshot,
            source=_source(snapshot),
            artifacts=_artifacts(snapshot),
            callbacks=replace(_callbacks(calls), apply_desired_state=failed),
            operation_spec=spec,
        )
    receipt = json.loads(
        next(paths.reports_dir.glob("soperator-release-reconcile-*.json")).read_text()
    )
    cluster = Cluster()
    cluster.parent["spec"]["suspend"] = True
    retirement = transition(cluster)
    retirement.state["admission"]["target"] = {
        "targetRef": spec.target_ref,
        "clusterId": spec.nebius_cluster_id,
        "kubernetesUid": spec.kubernetes_uid,
    }
    retirement.state["admissionSha256"] = soperator_sha256(retirement.admission)
    sha = soperator_sha256(asdict(spec))

    def checks(op, source=False):
        return SoperatorChecksExecution(
            policy=policy,
            operation_id=op + (":source" if source else ""),
            receipt_path=paths.reports_dir
            / (
                "soperator-checks-"
                + op.split(":")[-1][:24]
                + ("-source" if source else "")
                + ".json"
            ),
            kubernetes=lambda *_: {},
            slurm=lambda _: "",
            assert_authority=lambda: None,
        )

    for is_source in (False, True):
        execution = checks(sha, is_source)
        execution.state["targetApplyIntent"] = True
        if is_source:
            parent = retirement.admission["parent"]
            execution.state["sourceWriters"] = [
                {"kind": "helmrelease", **parent, "contract": parent["specSha256"]}
            ]
            execution.state["reservation"] = "owned-reservation"
            execution.state["reservationFingerprint"] = "frozen-reservation"
        execution._save()
    # Kubernetes accepts singular and plural resource names; the model uses HR.
    original_get = retirement.get
    retirement.get = lambda kind, namespace, name: original_get(
        "helmreleases.helm.toolkit.fluxcd.io" if kind == "helmrelease" else kind,
        namespace,
        name,
    )
    return paths, snapshot, strategy, spec, receipt, retirement, checks


def test_pending_real_checks_receipts_transfer_without_recapturing_preimages(paused):
    paths, _, _, spec, _, retirement, checks = paused
    sha = soperator_sha256(asdict(spec))
    repair = prepare_repair(paths, retirement, bound_sha256=sha, scheduling={"actions": []})
    assert repair is not None
    assert repair["checks"]["target"]["validation"]["readiness"] == "pending"
    before = {
        file.name: file.read_bytes() for file in paths.reports_dir.glob("soperator-checks-*.json")
    }
    replacement = replace(
        spec, admission_sha256=soperator_sha256(repair), intervention_generation=1
    )
    seal_successor(paths, replacement, repair, lambda: None)
    new_sha = soperator_sha256(asdict(replacement))
    for source_lane in (False, True):
        successor = checks(new_sha, source_lane)
        transfer_checks(successor, repair, source=source_lane)
        transfer_checks(successor, repair, source=source_lane)
        assert successor.state["targetApplyIntent"] is True
        assert successor.state["jobs"] == {}
        assert successor.state["validation"]["readiness"] == "pending"
    assert all((paths.reports_dir / name).read_bytes() == data for name, data in before.items())
    assert sealed_binding(paths, spec.target_ref, new_sha) == (sha, 1)
    assert (
        prepare_repair(paths, retirement, bound_sha256=new_sha, scheduling={"actions": []})
        == repair
    )


def test_successor_reconciles_unchanged_artifacts_from_failed_apply(paused):
    paths, snapshot, strategy, spec, predecessor, retirement, _ = paused
    repair = prepare_repair(
        paths, retirement, bound_sha256=soperator_sha256(asdict(spec)), scheduling={}
    )
    replacement = replace(
        spec, admission_sha256=soperator_sha256(repair), intervention_generation=1
    )
    calls = []
    result = reconcile_soperator_release(
        paths=paths,
        target_ref=spec.target_ref,
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=_artifacts(snapshot),
        callbacks=_callbacks(calls),
        operation_spec=replacement,
        repair_lineage=SoperatorReconcileRepairLineage(
            predecessor_receipt=predecessor,
            previous_operation_spec_sha256=soperator_sha256(asdict(spec)),
            reason=REPAIR_REASON,
            resume_phase="apply-declarative-release",
        ),
    )
    assert json.loads(result.read_text())["status"] == "complete"
    assert calls.count("apply") == 1


@pytest.mark.parametrize(
    "field",
    [
        "desired_values_sha256",
        "scheduling_sha256",
        "infrastructure_plan_sha256",
        "checks_policy_sha256",
    ],
)
def test_successor_rejects_any_other_immutable_change(paused, field):
    _, _, _, spec, _, _, _ = paused
    after = {
        **asdict(spec),
        "admission_sha256": "changed",
        "intervention_generation": 1,
        field: "changed",
    }
    with pytest.raises(RuntimeError, match="immutable"):
        validate_successor(asdict(spec), after)


def test_seal_rejects_conflicting_successor_and_scheduling_state(paused):
    paths, _, _, spec, _, retirement, _ = paused
    sha = soperator_sha256(asdict(spec))
    repair = prepare_repair(paths, retirement, bound_sha256=sha, scheduling={"startedAt": "frozen"})
    replacement = replace(
        spec, admission_sha256=soperator_sha256(repair), intervention_generation=1
    )
    seal_successor(paths, replacement, repair, lambda: None)
    with pytest.raises(RuntimeError, match="seal changed"):
        seal_successor(
            paths, replace(replacement, admission_sha256="sha256:" + "f" * 64), repair, lambda: None
        )
    with pytest.raises(RuntimeError, match="conflicts"):
        prepare_repair(paths, retirement, bound_sha256=sha, scheduling={"startedAt": "other"})


def test_changed_source_writer_cannot_gain_retirement_authority(paused):
    paths, _, _, spec, _, retirement, _ = paused
    saved = retirement.get

    def changed(*args):
        value = copy.deepcopy(saved(*args))
        value["spec"]["suspend"] = False
        return value

    retirement.get = changed
    with pytest.raises(RuntimeError, match="maintenance fence"):
        prepare_repair(
            paths, retirement, bound_sha256=soperator_sha256(asdict(spec)), scheduling={}
        )


def test_sealed_successor_replay_rechecks_source_fences(paused):
    paths, _, _, spec, _, retirement, _ = paused
    sha = soperator_sha256(asdict(spec))
    repair = prepare_repair(paths, retirement, bound_sha256=sha, scheduling={})
    successor = replace(spec, admission_sha256=soperator_sha256(repair), intervention_generation=1)
    seal_successor(paths, successor, repair, lambda: None)
    saved = retirement.get

    def unfenced(*args):
        value = copy.deepcopy(saved(*args))
        value["spec"]["suspend"] = False
        return value

    retirement.get = unfenced
    with pytest.raises(RuntimeError, match="maintenance fence"):
        prepare_repair(
            paths, retirement, bound_sha256=soperator_sha256(asdict(successor)), scheduling={}
        )


def test_completed_cleanup_does_not_admit_changed_unrelated_source_writer(paused):
    from nebius_cxcli.soperator_graph_repair import verify_source_fences
    from test_soperator_graph_transition import resource, witness

    paths, _, _, spec, _, retirement, _ = paused
    repair = prepare_repair(
        paths, retirement, bound_sha256=soperator_sha256(asdict(spec)), scheduling={}
    )
    other = resource("unrelated-parent")
    frozen = witness(other)
    repair["checks"]["-source"]["sourceWriters"].append(
        {"kind": "kustomization", **frozen, "contract": frozen["specSha256"]}
    )
    retirement.state["phase"] = "verified-absent"
    retirement.assert_cleanup = lambda: None
    saved = retirement.get
    retirement.get = lambda kind, ns, name: (
        other if name == "unrelated-parent" else saved(kind, ns, name)
    )
    with pytest.raises(RuntimeError, match="maintenance fence"):
        verify_source_fences(repair, retirement)


@pytest.mark.parametrize("field", ["user", "name", "reason"])
def test_maintenance_rejects_changed_held_job_postimage(field):
    from types import SimpleNamespace

    from nebius_cxcli.soperator_graph_repair import verify_scheduling_maintenance

    postimage = dict(
        job_id="42",
        user="user",
        state="PENDING",
        partition="compute",
        allocated_nodes="",
        reason="JobHeldAdmin",
        name="work",
    )
    live = SimpleNamespace(**{**postimage, field: "changed"})
    cli = SimpleNamespace(
        _soperator_flux_apply_owned_scheduling_state=lambda _: (
            {},
            {},
            {},
            {"soperator": {"42"}},
            {},
        ),
        _soperator_upgrade_jobs_by_id=lambda **_: [live],
        slurm_job_is_held=lambda _: True,
    )
    with pytest.raises(RuntimeError, match="postimage changed"):
        verify_scheduling_maintenance(
            cli,
            {
                "actions": [
                    {
                        "namespace": "soperator",
                        "action": "pending-hold-applied",
                        "jobs": [postimage],
                    }
                ]
            },
            {},
        )


@pytest.mark.parametrize("empty_values", [False, True])
def test_source_fences_follow_durable_parent_revision_and_child_opening(paused, empty_values):
    import yaml

    from nebius_cxcli.soperator_graph_repair import verify_source_fences
    from nebius_cxcli.soperator_graph_transition import HR
    from test_soperator_graph_transition import desired, resource, witness

    paths, _, _, spec, _, retirement, _ = paused
    cluster = retirement.run.__self__
    retained = resource("retained")
    retained["spec"]["suspend"] = True
    cluster.resources[(HR, "flux-system", "retained")] = retained
    original = witness(retained)
    repair = prepare_repair(
        paths, retirement, bound_sha256=soperator_sha256(asdict(spec)), scheduling={}
    )
    repair["checks"]["-source"]["sourceWriters"].append(
        {"kind": "helmrelease", **original, "contract": original["specSha256"]}
    )
    target_child = copy.deepcopy(retained)
    target_child["spec"]["values"] = {"settings": {}} if empty_values else {"target": True}
    original_run = retirement.run

    def run(args, **kwargs):
        if args[:3] == ["helm", "get", "metadata"]:
            from types import SimpleNamespace

            return SimpleNamespace(
                stdout=json.dumps({"applyMethod": "ssa", "status": "deployed", "revision": 2})
            )
        if args[:3] == ["helm", "get", "manifest"]:
            from types import SimpleNamespace

            return SimpleNamespace(stdout=yaml.safe_dump(target_child))
        return original_run(args, **kwargs)

    retirement.run = run
    retirement.fence()
    retirement.publish(desired(cluster))
    retained["spec"] = copy.deepcopy(target_child["spec"])
    if empty_values:
        retained["spec"]["values"]["settings"] = None
    retirement.wait_absent(timeout=0, interval=0)
    retirement.suspend_parent(True)
    verify_source_fences(repair, retirement)
    retirement.publish_parent(
        {**cluster.parent, "spec": {"suspend": True, "values": {"stable": True}}}
    )
    verify_source_fences(repair, retirement)
    retirement.resume_child("flux-system", "retained")
    verify_source_fences(repair, retirement)
    retirement.state["openedChildren"].clear()
    with pytest.raises(RuntimeError, match="maintenance fence"):
        verify_source_fences(repair, retirement)
