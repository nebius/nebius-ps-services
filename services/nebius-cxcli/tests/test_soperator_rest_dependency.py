import copy
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import soperator_install_checks_repair as repair
from nebius_cxcli.soperator_adapter import _MOUNT_GATE_SCRIPT, _REST_JWT_CONFIG_GATE_SCRIPT
from nebius_cxcli.soperator_rest_contract import materialize_soperator_rest


@pytest.fixture
def files():
    values = {
        "slurmCluster": {
            "overrideValues": {
                "slurmNodes": {
                    "rest": {"size": 2, "k8sNodeFilterName": "controller"},
                    "controller": {
                        "customInitContainers": [
                            {
                                "name": "mount-gate-controller-jail",
                                "command": ["/bin/sh", "-ec", _MOUNT_GATE_SCRIPT],
                                "volumeMounts": [{"name": "jail", "mountPath": "/proof"}],
                            }
                        ],
                    },
                },
                "volumeSources": [
                    {"name": "jail", "persistentVolumeClaim": {"claimName": "active"}}
                ],
            },
        },
        "nodesets": {"overrideValues": {"nodesets": [{"name": "worker", "replicas": 2}]}},
    }
    cm = {"kind": "ConfigMap", "data": {"values.yaml": yaml.safe_dump(values)}}
    outer = {"spec": {"values": values, "postRenderers": [{"keep": True}]}}
    return {
        repair.VALUES_FILE: yaml.safe_dump(cm).encode(),
        repair.OUTER_FILE: yaml.safe_dump(outer).encode(),
        "storage": b"retain-storage-bytes",
        "checks": b"retain-checks-bytes",
    }


def test_rest_delta_preserves_all_unrelated_approved_inputs(files):
    before = copy.deepcopy(files)
    after = repair.rest_repair_candidate(files)
    assert files == before
    assert after.keys() == before.keys()
    assert {k for k in after if before[k] != after[k]} == {repair.VALUES_FILE, repair.OUTER_FILE}
    values = yaml.safe_load(yaml.safe_load(after[repair.VALUES_FILE])["data"]["values.yaml"])
    outer = yaml.safe_load(after[repair.OUTER_FILE])
    assert outer["spec"]["values"] == values
    nodes = values["slurmCluster"]["overrideValues"]["slurmNodes"]
    assert nodes["rest"].pop("enabled") is True
    assert nodes["controller"].pop("openMetrics") == {"enabled": False}
    gate = nodes["controller"]["customInitContainers"][0]
    assert gate["command"][-1] == _MOUNT_GATE_SCRIPT + _REST_JWT_CONFIG_GATE_SCRIPT
    gate["command"][-1] = _MOUNT_GATE_SCRIPT
    assert values == yaml.safe_load(
        yaml.safe_load(before[repair.VALUES_FILE])["data"]["values.yaml"]
    )
    assert outer["spec"]["postRenderers"] == [{"keep": True}]


@pytest.mark.parametrize(
    "mutation", ["drift", "explicit-disabled", "already-enabled", "foreign-gate", "duplicate-gate"]
)
def test_rest_delta_rejects_unproven_changes(files, mutation):
    outer = yaml.safe_load(files[repair.OUTER_FILE])
    nodes = outer["spec"]["values"]["slurmCluster"]["overrideValues"]["slurmNodes"]
    if mutation == "drift":
        nodes["rest"]["size"] = 3
    elif mutation in {"explicit-disabled", "already-enabled"}:
        nodes["rest"]["enabled"] = mutation == "already-enabled"
    elif mutation == "foreign-gate":
        nodes["controller"]["customInitContainers"][0]["command"][-1] += "unexpected command"
    else:
        nodes["controller"]["customInitContainers"] *= 2
    files[repair.OUTER_FILE] = yaml.safe_dump(outer).encode()
    if mutation != "drift":
        cm = yaml.safe_load(files[repair.VALUES_FILE])
        cm["data"]["values.yaml"] = yaml.safe_dump(outer["spec"]["values"])
        files[repair.VALUES_FILE] = yaml.safe_dump(cm).encode()
    with pytest.raises(RuntimeError):
        repair.rest_repair_candidate(files)


@pytest.mark.parametrize("size", [0, -1, False, "2", None])
def test_required_rest_rejects_nonfunctional_replica_count(size):
    with pytest.raises(ValueError, match="replica"):
        materialize_soperator_rest({"slurmNodes": {"rest": {"size": size}}})


def test_router_does_not_admit_two_successors_in_one_call(monkeypatch, tmp_path):
    calls = []

    def prepare(**kwargs):
        calls.append(kwargs.get("reason", repair.CHECKS_REPAIR_REASON))
        return {"previousOperationSpecSha256": "old"}

    monkeypatch.setattr(repair, "_prepare_install_binding_repair", prepare)
    repair.prepare_install_input_repair(
        paths=SimpleNamespace(reports_dir=tmp_path),
        target_ref="test",
        scheduling_journal={"operationSpecSha256": "old"},
    )
    assert calls == [repair.CHECKS_REPAIR_REASON]


def test_all_ancestor_seals_and_hash_links_are_required(monkeypatch):
    calls = []
    first = {"replacementFiles": {"one": "a"}}
    second = {
        "ancestorRepair": first,
        "previousFiles": {"one": "a"},
        "replacementFiles": {"one": "b"},
    }
    third = {"ancestorRepair": second, "previousFiles": {"one": "b"}}
    monkeypatch.setattr(
        repair, "_bind_repair_admission", lambda record, **kwargs: calls.append(record)
    )
    repair._verify_ancestor_seals(
        third, env={}, kube_context="exact", assert_authority=lambda: None
    )
    assert calls == [first, second]
    first["replacementFiles"] = {"one": "foreign"}
    with pytest.raises(RuntimeError, match="ancestry"):
        repair._verify_ancestor_seals(
            third, env={}, kube_context="exact", assert_authority=lambda: None
        )
