import copy
import json
from dataclasses import asdict, replace

import pytest
import yaml

from nebius_cxcli.soperator_checks_policy import checks_digest
from nebius_cxcli.soperator_install_render_repair import (
    OUTER_FILE,
    RUNTIME_REPAIR_REASON,
    STORAGE_REPAIR_REASON,
    VALUES_FILE,
    _file_hashes,
    _files,
)
from nebius_cxcli.soperator_install_runtime_repair import (
    runtime_repair_candidate,
    runtime_reservation_handoff,
)
from nebius_cxcli.soperator_operation import soperator_sha256
from nebius_cxcli.soperator_release_reconciler import (
    SoperatorReconcileRepairLineage,
    reconcile_soperator_release,
    resolve_soperator_reconcile_strategy,
    validate_install_runtime_frontier,
)
from soperator_fixtures import sample_snapshot
from test_soperator_checks_execution import policy as policy
from test_soperator_release_reconciler import _artifacts, _callbacks, _paths, _source, _spec


def compiled():
    values = {
        "nodesets": {
            "overrideValues": {
                "nodesets": [
                    {
                        "name": "worker",
                        "replicas": 2,
                        "slurmd": {
                            "resources": {"gpu": 8, "cpu": "32"},
                            "volumes": {
                                "customVolumeMounts": [
                                    {
                                        "name": "driver",
                                        "mountPath": "/driver",
                                        "volumeSource": {"hostPath": {"path": "/driver"}},
                                    }
                                ]
                            },
                        },
                    }
                ]
            }
        },
        "untouched": {"value": True},
    }
    return {
        VALUES_FILE: yaml.safe_dump(
            {"data": {"values.yaml": yaml.safe_dump(values, sort_keys=False)}}, sort_keys=False
        ).encode(),
        OUTER_FILE: yaml.safe_dump(
            {"spec": {"values": values, "postRenderers": ["unchanged"]}}, sort_keys=False
        ).encode(),
        "soperator-nebius-adapter.yaml": b"kind: ConfigMap\n",
    }, values


def test_runtime_candidate_is_exact_reversible_two_file_delta():
    before, values = compiled()
    after = runtime_repair_candidate(before)
    assert {key for key in after if after[key] != before[key]} == {VALUES_FILE, OUTER_FILE}
    assert runtime_repair_candidate(after, inverse=True) == before
    assert runtime_repair_candidate(after) == after
    new = yaml.safe_load(yaml.safe_load(after[VALUES_FILE])["data"]["values.yaml"])
    worker = new["nodesets"]["overrideValues"]["nodesets"][0]
    assert (
        worker["slurmd"]["resources"]
        == values["nodesets"]["overrideValues"]["nodesets"][0]["slurmd"]["resources"]
    )
    assert worker["slurmd"]["volumes"]["customVolumeMounts"][-1]["name"] == "driver"
    assert new["untouched"] == values["untouched"]


@pytest.mark.parametrize("mutation", [None, "previous", "replacement", "policy", "mount"])
def test_runtime_handoff_binds_both_policies_through_the_sealed_delta(tmp_path, policy, mutation):
    before, values = compiled()
    after = runtime_repair_candidate(before)
    paths = _paths(tmp_path)
    for name, data in after.items():
        (paths.flux_dir / name).write_bytes(data)
    after = _files(paths.flux_dir)
    before = runtime_repair_candidate(after, inverse=True)
    new_values = yaml.safe_load(yaml.safe_load(after[VALUES_FILE])["data"]["values.yaml"])
    old_policy = replace(policy, values_sha256=checks_digest(values))
    new_policy = replace(policy, values_sha256=checks_digest(new_values))
    repair = {
        "previousFiles": _file_hashes(before),
        "replacementFiles": _file_hashes(after),
        "reservationHandoff": {
            "policy": old_policy.sha256,
            "operation": "old",
            "receiptSha256": "receipt",
            "reservation": "barrier",
            "fingerprint": "unchanged",
        },
    }
    if mutation == "previous":
        repair["previousFiles"][VALUES_FILE] = "changed"
    elif mutation == "replacement":
        repair["replacementFiles"][OUTER_FILE] = "changed"
    elif mutation == "policy":
        repair["reservationHandoff"]["policy"] = "changed"
    elif mutation == "mount":
        (paths.flux_dir / VALUES_FILE).write_bytes(
            after[VALUES_FILE].replace(b"readOnly: true", b"readOnly: false")
        )
    if mutation:
        with pytest.raises(RuntimeError):
            runtime_reservation_handoff(repair, paths=paths, policy=new_policy)
    else:
        handoff = runtime_reservation_handoff(repair, paths=paths, policy=new_policy)
        assert handoff["predecessorPolicy"] == old_policy.sha256
        assert handoff["policy"] == new_policy.sha256
        assert handoff["fingerprint"] == "unchanged"


@pytest.fixture
def interrupted(tmp_path):
    paths, snapshot = _paths(tmp_path), sample_snapshot()
    strategy = resolve_soperator_reconcile_strategy(
        current_release="",
        target_release=snapshot.release,
        source_contract="",
        target_contract=snapshot.capability_contract,
    )
    kwargs = dict(
        paths=paths,
        target_ref="cluster-a",
        ownership="managed",
        strategy=strategy,
        snapshot=snapshot,
        source=_source(snapshot),
        artifacts=_artifacts(snapshot),
        operation_spec=_spec(snapshot, strategy, paths),
    )

    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        reconcile_soperator_release(
            **kwargs,
            callbacks=replace(
                _callbacks([]),
                restore_infrastructure=lambda: {
                    "checks": {
                        "partitions": {"gpu": "UP"},
                        "reservation": {"fingerprint": "barrier"},
                    },
                    "infrastructure": {"namespaceCount": 0, "status": "restored"},
                },
                accept_checks=interrupt,
            ),
        )
    path = next(paths.reports_dir.glob("soperator-release-reconcile-*.json"))
    return kwargs, json.loads(path.read_text()), path


@pytest.mark.parametrize(
    "mutation", [None, "extra", "completed", "restoration", "source", "phase", "digest"]
)
def test_runtime_frontier_requires_original_interrupted_prefix(interrupted, mutation):
    _, predecessor, _ = interrupted
    if mutation == "extra":
        predecessor["transitions"].append({})
    elif mutation == "completed":
        predecessor["transitions"][-1]["status"] = "complete"
    elif mutation == "restoration":
        predecessor["transitions"][6]["evidence"].pop("checks")
    elif mutation == "source":
        predecessor["operation"]["spec"]["current_release"] = "4.1.5"
    elif mutation == "phase":
        predecessor["transitions"][-1]["phase"] = "other"
    elif mutation == "digest":
        predecessor["transitions"][2]["receiptSha256"] = "tampered"
    if mutation:
        with pytest.raises(ValueError):
            validate_install_runtime_frontier(predecessor)
    else:
        validate_install_runtime_frontier(predecessor)


@pytest.mark.parametrize("reason", [RUNTIME_REPAIR_REASON, STORAGE_REPAIR_REASON])
def test_runtime_successor_replays_apply_and_acceptance_preserving_old_history(interrupted, reason):
    kwargs, predecessor, path = interrupted
    before = path.read_bytes()
    old_spec = kwargs["operation_spec"]
    kwargs["operation_spec"] = replace(
        old_spec, intervention_generation=1, admission_sha256="sha256:" + "9" * 64
    )
    kwargs["artifacts"] = replace(kwargs["artifacts"], umbrella_render_sha256="sha256:" + "7" * 64)
    kwargs["repair_lineage"] = SoperatorReconcileRepairLineage(
        predecessor_receipt=copy.deepcopy(predecessor),
        previous_operation_spec_sha256=soperator_sha256(asdict(old_spec)),
        resume_phase="apply-declarative-release",
        reason=reason,
    )
    calls = []
    receipt_path = reconcile_soperator_release(**kwargs, callbacks=_callbacks(calls))
    receipt = json.loads(receipt_path.read_text())
    assert calls.count("apply") == 1
    assert all("repairPredecessor" in row for row in receipt["transitions"][:2])
    assert all("repairPredecessor" not in row for row in receipt["transitions"][2:])
    assert receipt["transitions"][8]["status"] == "complete"
    assert path.read_bytes() == before
