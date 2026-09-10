import copy
import json
from dataclasses import asdict, replace

import pytest
import yaml

from nebius_cxcli.soperator_install_gpu_repair import gpu_maintenance_repair_candidate
from nebius_cxcli.soperator_install_render_repair import (
    GPU_MAINTENANCE_REPAIR_REASON,
    OUTER_FILE,
    VALUES_FILE,
)
from nebius_cxcli.soperator_operation import soperator_sha256
from nebius_cxcli.soperator_release_reconciler import (
    SoperatorReconcileRepairLineage,
    reconcile_soperator_release,
    resolve_soperator_reconcile_strategy,
    validate_install_gpu_acceptance_frontier,
)
from soperator_fixtures import sample_snapshot
from test_soperator_checks_execution import policy as policy
from test_soperator_release_reconciler import _artifacts, _callbacks, _paths, _source, _spec


def test_candidate_changes_only_absent_gpu_counts_and_matching_inline_values():
    node = {
        "name": "worker",
        "replicas": 2,
        "gpu": {"enabled": True},
        "nodeConfig": {"static": "Boards=1 SocketsPerBoard=1 CoresPerSocket=32 ThreadsPerCore=1"},
        "slurmd": {"resources": {"gpu": 8, "cpu": "32", "memory": "16Gi"}},
    }
    explicit = copy.deepcopy(node)
    explicit["name"] = "custom"
    explicit["nodeConfig"]["static"] += " Gres=gpu:h200:8"
    values = {
        "nodesets": {"overrideValues": {"nodesets": [node, explicit]}},
        "other": {"keep": True},
    }
    previous = {
        VALUES_FILE: yaml.safe_dump({"data": {"values.yaml": yaml.safe_dump(values)}}).encode(),
        OUTER_FILE: yaml.safe_dump(
            {"spec": {"values": values, "postRenderers": ["keep"]}}
        ).encode(),
        "soperator-nebius-adapter.yaml": b"untouched",
    }
    candidate = gpu_maintenance_repair_candidate(previous)
    actual = yaml.safe_load(yaml.safe_load(candidate[VALUES_FILE])["data"]["values.yaml"])
    expected = copy.deepcopy(values)
    expected["nodesets"]["overrideValues"]["nodesets"][0]["nodeConfig"]["static"] += " Gres=gpu:8"
    assert actual == expected
    assert yaml.safe_load(candidate[OUTER_FILE]) == {
        "spec": {"values": expected, "postRenderers": ["keep"]}
    }
    assert {k for k in previous if candidate[k] != previous[k]} == {VALUES_FILE, OUTER_FILE}
    assert gpu_maintenance_repair_candidate(candidate) == candidate
    previous[OUTER_FILE] = yaml.safe_dump({"spec": {"values": {}}}).encode()
    with pytest.raises(RuntimeError, match="matching umbrella"):
        gpu_maintenance_repair_candidate(previous)


@pytest.fixture
def failed_install(tmp_path):
    snapshot = sample_snapshot()
    paths = _paths(tmp_path)
    strategy = resolve_soperator_reconcile_strategy(
        current_release="",
        target_release=snapshot.release,
        source_contract="",
        target_contract=snapshot.capability_contract,
    )
    spec = _spec(snapshot, strategy, paths)

    def fail_accept():
        raise RuntimeError("missing GPU inventory")

    kwargs = dict(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=_artifacts(snapshot),
        operation_spec=spec,
    )
    with pytest.raises(RuntimeError, match="missing GPU"):
        reconcile_soperator_release(
            **kwargs,
            callbacks=replace(
                _callbacks([]),
                restore_infrastructure=lambda: {"namespaceCount": 0, "status": "restored"},
                accept_checks=fail_accept,
            ),
        )
    path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    return kwargs, json.loads(path.read_text()), path


@pytest.mark.parametrize(
    "mutation",
    [None, "status", "completion", "failure", "receipt", "frontier", "restore", "phase", "extra"],
)
def test_closed_initial_acceptance_frontier(failed_install, mutation):
    _, predecessor, _ = failed_install
    if mutation == "status":
        predecessor["status"] = "running"
    elif mutation == "completion":
        predecessor["transitions"][-1]["status"] = "complete"
    elif mutation == "failure":
        predecessor["transitions"][-1]["failureAttempts"] = 0
    elif mutation == "receipt":
        predecessor["transitions"][1]["receiptSha256"] = "tampered"
    elif mutation == "frontier":
        predecessor["irreversibleFrontier"]["transitionId"] = "tampered"
    elif mutation == "restore":
        predecessor["transitions"][6]["evidence"] = {"status": "different"}
    elif mutation == "phase":
        predecessor["transitions"][-1]["phase"] = "other"
    elif mutation == "extra":
        predecessor["transitions"].append({})
    if mutation:
        with pytest.raises(ValueError):
            validate_install_gpu_acceptance_frontier(predecessor)
    else:
        validate_install_gpu_acceptance_frontier(predecessor)


def test_successor_preserves_failed_history_and_runs_interrupted_recovery(
    failed_install, tmp_path, policy
):
    from test_soperator_checks_maintenance import maintenance

    kwargs, predecessor, old_path = failed_install
    old_bytes = old_path.read_bytes()
    old_spec = kwargs["operation_spec"]
    kwargs["operation_spec"] = replace(
        old_spec, intervention_generation=1, admission_sha256="sha256:" + "9" * 64
    )
    kwargs["artifacts"] = replace(kwargs["artifacts"], umbrella_render_sha256="sha256:" + "7" * 64)
    kwargs["repair_lineage"] = SoperatorReconcileRepairLineage(
        predecessor_receipt=predecessor,
        previous_operation_spec_sha256=soperator_sha256(asdict(old_spec)),
        resume_phase="apply-declarative-release",
        reason=GPU_MAINTENANCE_REPAIR_REASON,
    )
    calls = []
    runner, cluster, states, infra_calls, composed = maintenance(tmp_path, policy)
    original_slurm = runner.slurm

    def interrupt_partition(command):
        result = original_slurm(command)
        if command == "scontrol update PartitionName=gpu State=UP":
            raise KeyboardInterrupt
        return result

    def interrupted():
        calls.append("restore-interrupted")
        return composed.restore()

    def recovered(_transition):
        calls.append("complete-maintenance-recovery")
        return composed.recover()

    runner.slurm = interrupt_partition
    callbacks = replace(
        _callbacks(calls),
        restore_infrastructure=interrupted,
        interrupted_recovery={"restore-infrastructure-and-scheduling-preimages": recovered},
    )
    with pytest.raises(KeyboardInterrupt):
        reconcile_soperator_release(**kwargs, callbacks=callbacks)
    assert states == {"gpu": "UP", "hidden": "DOWN"}
    assert infra_calls == []
    runner.slurm = original_slurm
    path = reconcile_soperator_release(**kwargs, callbacks=callbacks)
    result = json.loads(path.read_text())
    assert result["status"] == "complete"
    assert all("repairPredecessor" in row for row in result["transitions"][:2])
    assert all("repairPredecessor" not in row for row in result["transitions"][2:])
    assert calls.count("apply") == 1
    assert calls.count("restore-interrupted") == 1
    assert calls.count("complete-maintenance-recovery") == 1
    assert infra_calls == ["recover"]
    assert states == {"gpu": "UP", "hidden": "UP"}
    assert (
        result["transitions"][6]["evidence"]["checks"]["reservation"]["fingerprint"]
        == runner.state["reservationFingerprint"]
    )
    assert old_path.read_bytes() == old_bytes


@pytest.mark.parametrize("mutation", [None, "journal", "jobs", "node", "source", "reservation"])
def test_gpu_admission_seals_only_exact_native_maintenance_successor(
    failed_install, tmp_path, monkeypatch, policy, mutation
):
    from types import SimpleNamespace

    from nebius_cxcli import soperator_install_checks_repair as shared
    from nebius_cxcli import soperator_install_gpu_repair as repair
    from nebius_cxcli.soperator_checks import SoperatorChecksExecution
    from test_soperator_checks_execution import Cluster

    kwargs, _, _ = failed_install
    paths = kwargs["paths"]
    node = {
        "name": "worker",
        "replicas": 2,
        "gpu": {"enabled": True},
        "nodeConfig": {"static": "CoresPerSocket=32 Sockets=1"},
        "slurmd": {"resources": {"gpu": 8}},
    }
    volumes = [{"name": "jail", "persistentVolumeClaim": {"claimName": "jail-active"}}]
    values = {
        "nodesets": {
            "releaseName": "native-nodesets",
            "namespace": "soperator",
            "overrideValues": {"nodesets": [node]},
        },
        "slurmCluster": {"overrideValues": {"clusterName": "lab", "volumeSources": volumes}},
    }
    for name, value in {
        VALUES_FILE: {"data": {"values.yaml": yaml.safe_dump(values)}},
        OUTER_FILE: {"spec": {"values": values}},
        "soperator-nebius-adapter.yaml": {"kind": "ConfigMap"},
    }.items():
        (paths.flux_dir / name).write_text(yaml.safe_dump(value))
    hashes = repair._file_hashes(repair._files(paths.flux_dir))
    kwargs["operation_spec"] = replace(
        kwargs["operation_spec"],
        desired_values_sha256=hashes[VALUES_FILE],
        adapter_sha256=hashes["soperator-nebius-adapter.yaml"],
    )

    def fail_accept():
        raise RuntimeError("GPU missing")

    with pytest.raises(RuntimeError, match="GPU missing"):
        reconcile_soperator_release(
            **kwargs,
            callbacks=replace(
                _callbacks([]),
                restore_infrastructure=lambda: {"namespaceCount": 0, "status": "restored"},
                accept_checks=fail_accept,
            ),
        )
    operation_sha = soperator_sha256(asdict(kwargs["operation_spec"]))
    cluster = Cluster(policy)
    cluster.nodes = {"worker-0": (32, 0), "worker-1": (32, 0)}
    path = paths.reports_dir / f"soperator-checks-{operation_sha.removeprefix('sha256:')[:24]}.json"
    checks = SoperatorChecksExecution(
        policy=policy,
        operation_id=operation_sha,
        receipt_path=path,
        kubernetes=cluster.kube,
        slurm=cluster.slurm,
        assert_authority=lambda: None,
    )
    name = "cxcli_" + checks.operation_id[:16]
    cluster.reservation_fields["ReservationName"] = name
    checks.state.update(installReservationIntent=True, reservation=name, targetApplyIntent=True)
    if mutation == "jobs":
        checks.state["jobs"] = {"already-started": {}}
    checks._save()
    journal = {
        "operationSpecSha256": operation_sha,
        "status": "recovery-required",
        "lastCompletedStage": "infrastructure-restored",
        "actions": [],
        "infrastructureRestoreReceipt": {"namespaceCount": 0, "status": "restored"},
    }
    local = copy.deepcopy(journal)
    if mutation == "journal":
        local["actions"] = [{}]
    snapshot = SimpleNamespace(
        release=kwargs["snapshot"].release,
        charts={
            "nodesets": SimpleNamespace(
                digest="sha256:" + "a" * 64, package_sha256="sha256:" + "b" * 64
            )
        },
    )
    monkeypatch.setattr(repair, "load_soperator_release_snapshot", lambda *_: snapshot)
    monkeypatch.setattr(
        repair,
        "frozen_soperator_release_from_snapshot",
        lambda *_: SimpleNamespace(source=SimpleNamespace(source_dir=tmp_path)),
    )
    default_path = tmp_path / "helm/soperator-fluxcd/values.yaml"
    default_path.parent.mkdir(parents=True)
    default_path.write_text(
        yaml.safe_dump(
            {"nodesets": {"releaseName": "upstream-default", "namespace": "upstream-default"}}
        )
    )
    monkeypatch.setattr(repair, "compile_checks_policy", lambda *_: policy)
    source = {
        "metadata": {"uid": "source"},
        "spec": {"ref": {"digest": snapshot.charts["nodesets"].digest}},
        "status": {"artifact": {"digest": snapshot.charts["nodesets"].package_sha256}},
    }
    child = {
        "metadata": {"uid": "child", "labels": {"app.kubernetes.io/version": snapshot.release}},
        "spec": {
            "releaseName": "native-nodesets",
            "targetNamespace": "soperator",
            "chartRef": {
                "kind": "OCIRepository",
                "name": "soperator-upstream-nodesets",
                "namespace": "flux-system",
            },
        },
        "status": {"lastAttemptedRevisionDigest": snapshot.charts["nodesets"].digest},
    }
    live = {
        "metadata": {
            "uid": "node",
            "annotations": {
                "meta.helm.sh/release-name": "native-nodesets",
                "meta.helm.sh/release-namespace": "soperator",
            },
        },
        "spec": {
            "replicas": 2,
            "gpu": {"enabled": True},
            "nodeConfig": node["nodeConfig"],
            "slurmd": {"resources": {"nvidia.com/gpu": "8"}},
        },
    }
    if mutation == "node":
        live["spec"]["replicas"] = 3
    elif mutation == "source":
        source["spec"]["ref"]["digest"] = "different"
    elif mutation == "reservation":
        cluster.users = "root,soperatorchecks"
    resources = {
        "nodeset": live,
        "helmrelease": child,
        "ocirepository": source,
        "slurmcluster": {"spec": {"volumeSources": volumes}},
    }
    monkeypatch.setattr(repair, "_kube_get", lambda args, **_: resources[args[0]])
    seals = []
    monkeypatch.setattr(
        shared, "_bind_repair_admission", lambda evidence, **_: seals.append(evidence)
    )
    ancestor = {
        "interventionGeneration": 0,
        "replacementFiles": hashes,
        "previousOperationSpecSha256": "ancestor",
    }
    call = dict(
        paths=paths,
        target_ref="cluster-a",
        scheduling_journal=journal,
        local_scheduling_journal=local,
        env={},
        kube_context="lab",
        assert_authority=lambda: None,
        slurm=cluster.slurm,
        ancestor=ancestor,
    )
    if mutation:
        with pytest.raises(RuntimeError):
            repair.prepare_install_gpu_maintenance_repair(**call)
        assert not seals
        assert repair._file_hashes(repair._files(paths.flux_dir)) == hashes
        return
    result = repair.prepare_install_gpu_maintenance_repair(**call)
    assert result["interventionGeneration"] == 1
    assert result["reservationHandoff"]["reservation"] == name
    assert result["reservationHandoff"]["receiptSha256"] == repair._digest(checks.state)
    assert result["ancestorRepair"] == ancestor
    assert result["schema"] == GPU_MAINTENANCE_REPAIR_REASON
    assert seals == [result]
    assert {key for key in hashes if result["replacementFiles"][key] != hashes[key]} == {
        VALUES_FILE,
        OUTER_FILE,
    }
    assert not cluster.writes


@pytest.mark.parametrize(
    "mutation", [None, "source", "previous-values", "next-values", "nodeset", "reservation-policy"]
)
def test_reservation_handoff_binds_both_full_values_policy_epochs(tmp_path, policy, mutation):
    from types import SimpleNamespace

    from nebius_cxcli import soperator_install_gpu_repair as repair
    from nebius_cxcli.soperator_checks_policy import checks_digest

    values = {
        "nodesets": {
            "overrideValues": {
                "nodesets": [
                    {
                        "name": "worker",
                        "gpu": {"enabled": True},
                        "nodeConfig": {"static": "CoresPerSocket=32 Sockets=1"},
                        "slurmd": {"resources": {"gpu": 8}},
                    }
                ]
            }
        },
        "preserved": True,
    }
    old = {
        VALUES_FILE: yaml.safe_dump(
            {"data": {"values.yaml": yaml.safe_dump(values, sort_keys=False)}}, sort_keys=False
        ).encode(),
        OUTER_FILE: yaml.safe_dump({"spec": {"values": values}}, sort_keys=False).encode(),
    }
    new = gpu_maintenance_repair_candidate(old)
    next_values = yaml.safe_load(yaml.safe_load(new[VALUES_FILE])["data"]["values.yaml"])
    before = replace(policy, values_sha256=checks_digest(values))
    after = replace(policy, values_sha256=checks_digest(next_values))
    assert before.sha256 != after.sha256
    receipt = {
        "previousFiles": repair._file_hashes(old),
        "replacementFiles": repair._file_hashes(new),
        "nodesetsRelease": {"nodes": [{"name": "worker"}]},
        "reservationHandoff": {"policy": before.sha256, "reservation": "original"},
    }
    for name, content in new.items():
        (tmp_path / name).write_bytes(content)
    if mutation == "source":
        after = replace(after, source_sha256="changed")
    elif mutation == "previous-values":
        receipt["previousFiles"][VALUES_FILE] = "tampered"
    elif mutation == "next-values":
        after = replace(after, values_sha256="changed")
    elif mutation == "nodeset":
        receipt["nodesetsRelease"]["nodes"] = [{"name": "other"}]
    elif mutation == "reservation-policy":
        receipt["reservationHandoff"]["policy"] = "changed"
    call = dict(paths=SimpleNamespace(flux_dir=tmp_path), policy=after)
    if mutation:
        with pytest.raises(RuntimeError):
            repair.gpu_maintenance_reservation_handoff(receipt, **call)
    else:
        handoff = repair.gpu_maintenance_reservation_handoff(receipt, **call)
        assert handoff == {
            "policy": after.sha256,
            "predecessorPolicy": before.sha256,
            "reservation": "original",
        }
