from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli.deployment_observation import verify_desired_target
from nebius_cxcli.deployment_state import DeploymentGeneration
from nebius_cxcli.flux_ops import stable_soperator_documents
from nebius_cxcli.soperator_checks_phase import restored_partition_configuration
from test_soperator_flux_sources import _outer_bundle, _staged_contract


@pytest.fixture
def restored_bundle():
    partitions = {
        "configType": "structured",
        "partitions": [
            {"name": "gpu", "config": "Default=YES State=UP MaxTime=INFINITE"},
            {"name": "hidden", "config": "Default=NO Hidden=YES State=UP"},
        ],
    }
    values = {"slurmCluster": {"overrideValues": {"partitionConfiguration": partitions}}}
    outer = yaml.safe_load(_outer_bundle())
    outer["spec"]["values"] = copy.deepcopy(values)
    graph = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "nebius-cxcli-soperator-release-graph", "namespace": "flux-system"},
        "data": {"graph.json": json.dumps(_staged_contract())},
    }
    value_map = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "terraform-fluxcd-values", "namespace": "flux-system"},
        "data": {"values.yaml": yaml.safe_dump(values)},
    }
    documents = [outer, graph, value_map]
    frozen = DeploymentGeneration(
        {"deploy": {"targets": [{"target_ref": "cluster", "flux_dir": "flux/cluster"}]}},
        {
            "flux/cluster/kustomization.yaml": base64.b64encode(
                b"resources: [bundle.yaml]"
            ).decode(),
            "flux/cluster/bundle.yaml": base64.b64encode(
                yaml.safe_dump_all(documents).encode()
            ).decode(),
        },
    )
    restored = copy.deepcopy(values)
    restored["slurmCluster"]["overrideValues"]["partitionConfiguration"] = (
        restored_partition_configuration(
            partitions,
            {name: {"AllowGroups": "ALL", "State": "UP"} for name in ("gpu", "hidden")},
        )
    )
    live = {
        doc["metadata"]["name"]: doc
        for doc in stable_soperator_documents(documents, _staged_contract()["releases"])
    }
    live[outer["metadata"]["name"]]["spec"]["values"] = copy.deepcopy(restored)
    live[value_map["metadata"]["name"]]["data"]["values.yaml"] = yaml.safe_dump(restored)
    for doc in live.values():
        doc["metadata"].update(uid="u", generation=2)
        doc["status"] = {
            "observedGeneration": 2,
            "conditions": [{"type": "Ready", "status": "True"}],
        }
    fake = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=lambda ns, argv, **kw: SimpleNamespace(
            stdout=json.dumps(live[argv[2]])
        ),
    )
    return fake, frozen, live


def test_final_proof_accepts_native_restoration_defaults_and_field_order(restored_bundle):
    fake, frozen, _ = restored_bundle
    assert verify_desired_target(fake, generation=frozen, target_ref="cluster", kube_env={})[
        "ready"
    ]


def test_unsuspended_outer_release_may_omit_false(restored_bundle):
    fake, frozen, live = restored_bundle
    live["soperator-controller"]["spec"].pop("suspend")
    assert verify_desired_target(fake, generation=frozen, target_ref="cluster", kube_env={})[
        "ready"
    ]
    live["soperator-controller"]["spec"]["suspend"] = True
    with pytest.raises(RuntimeError, match="not converged"):
        verify_desired_target(fake, generation=frozen, target_ref="cluster", kube_env={})


@pytest.mark.parametrize("surface", ["inline", "configmap"])
@pytest.mark.parametrize("drift", ["state", "groups", "other-field", "extra-value", "duplicate"])
def test_final_proof_rejects_restoration_or_unrelated_drift(restored_bundle, surface, drift):
    fake, frozen, live = restored_bundle
    values = copy.deepcopy(live["soperator-controller"]["spec"]["values"])
    row = values["slurmCluster"]["overrideValues"]["partitionConfiguration"]["partitions"][0]
    if drift == "state":
        row["config"] = row["config"].replace("State=UP", "State=DOWN")
    elif drift == "groups":
        row["config"] = row["config"].replace("AllowGroups=ALL", "AllowGroups=soperatorchecks")
    elif drift == "other-field":
        row["config"] = row["config"].replace("Default=YES", "Default=NO")
    elif drift == "duplicate":
        row["config"] += " State=UP"
    else:
        values["unadmitted"] = True
    if surface == "inline":
        live["soperator-controller"]["spec"]["values"] = values
    else:
        live["terraform-fluxcd-values"]["data"]["values.yaml"] = yaml.safe_dump(values)
    with pytest.raises((RuntimeError, ValueError), match="not converged|ambiguous"):
        verify_desired_target(fake, generation=frozen, target_ref="cluster", kube_env={})


def test_ordinary_values_do_not_receive_soperator_normalization(restored_bundle):
    from nebius_cxcli.deployment_observation import target_documents

    fake, frozen, _ = restored_bundle
    documents = [
        doc
        for doc in target_documents(frozen, "cluster").values()
        if doc["metadata"]["name"] != "nebius-cxcli-soperator-release-graph"
    ]
    ordinary = DeploymentGeneration(
        frozen.manifest,
        {
            **frozen.files,
            "flux/cluster/bundle.yaml": base64.b64encode(
                yaml.safe_dump_all(documents).encode()
            ).decode(),
        },
    )
    with pytest.raises(RuntimeError, match="not converged"):
        verify_desired_target(fake, generation=ordinary, target_ref="cluster", kube_env={})


@pytest.mark.parametrize(
    "value", [None, {}, {"slurmCluster": None}, {"slurmCluster": {"overrideValues": None}}]
)
def test_values_without_structured_partitions_remain_exact(value):
    from nebius_cxcli.deployment_observation import _canonical_soperator_values

    assert _canonical_soperator_values(value) == value


@pytest.mark.parametrize("location", ["structured", "custom"])
def test_explicit_slurm_default_inheritance_remains_exact(location):
    from nebius_cxcli.deployment_observation import _canonical_soperator_values

    override = {
        "partitionConfiguration": {
            "configType": "structured",
            "partitions": [{"name": "gpu", "config": "State=UP"}],
        }
    }
    if location == "structured":
        override["partitionConfiguration"]["partitions"].insert(
            0, {"name": "DEFAULT", "config": "AllowGroups=restricted"}
        )
    else:
        override["customSlurmConfig"] = "PartitionName=DEFAULT AllowGroups=restricted"
    value = {"slurmCluster": {"overrideValues": override}}
    assert _canonical_soperator_values(value) == value


@pytest.fixture(params=["In", "NotIn"])
def selector_bundle(restored_bundle, request):
    fake, frozen, live = restored_bundle
    selector = {
        "name": "no-gpu",
        "affinity": {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchExpressions": [
                                {
                                    "key": "nebius.com/node-group",
                                    "operator": request.param,
                                    "values": ["system", "controller", "login", "accounting"],
                                }
                            ]
                        }
                    ]
                }
            }
        },
    }

    def add_selector(document, *, reverse):
        if document["metadata"]["name"] == "soperator-controller":
            values = document["spec"]["values"]
        elif document["metadata"]["name"] == "terraform-fluxcd-values":
            values = yaml.safe_load(document["data"]["values.yaml"])
        else:
            return
        values["slurmCluster"]["overrideValues"]["k8sNodeFilters"] = [copy.deepcopy(selector)]
        if reverse:
            _selector_requirement(values)["values"].reverse()
        if document["kind"] == "ConfigMap":
            document["data"]["values.yaml"] = yaml.safe_dump(values)

    documents = list(yaml.safe_load_all(base64.b64decode(frozen.files["flux/cluster/bundle.yaml"])))
    for document in documents:
        add_selector(document, reverse=False)
    frozen = DeploymentGeneration(
        frozen.manifest,
        {
            **frozen.files,
            "flux/cluster/bundle.yaml": base64.b64encode(
                yaml.safe_dump_all(documents).encode()
            ).decode(),
        },
    )
    for document in live.values():
        add_selector(document, reverse=True)
    return fake, frozen, live


def _selector_requirement(values):
    return values["slurmCluster"]["overrideValues"]["k8sNodeFilters"][0]["affinity"][
        "nodeAffinity"
    ]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0]["matchExpressions"][
        0
    ]


def test_final_proof_accepts_reordered_node_selector_members(selector_bundle):
    fake, frozen, _ = selector_bundle
    assert verify_desired_target(fake, generation=frozen, target_ref="cluster", kube_env={})[
        "ready"
    ]


@pytest.mark.parametrize("surface", ["inline", "configmap"])
@pytest.mark.parametrize("drift", ["added", "removed", "duplicate", "key", "operator"])
def test_final_proof_rejects_changed_node_selector(selector_bundle, surface, drift):
    fake, frozen, live = selector_bundle
    document = live["soperator-controller" if surface == "inline" else "terraform-fluxcd-values"]
    values = (
        document["spec"]["values"]
        if surface == "inline"
        else yaml.safe_load(document["data"]["values.yaml"])
    )
    requirement = _selector_requirement(values)
    if drift == "added":
        requirement["values"].append("foreign-group")
    elif drift == "removed":
        requirement["values"].pop()
    elif drift == "duplicate":
        requirement["values"].append(requirement["values"][0])
    elif drift == "key":
        requirement["key"] = "another-label"
    else:
        requirement["operator"] = "NotIn" if requirement["operator"] == "In" else "In"
    if surface == "configmap":
        document["data"]["values.yaml"] = yaml.safe_dump(values)
    with pytest.raises(RuntimeError, match="not converged"):
        verify_desired_target(fake, generation=frozen, target_ref="cluster", kube_env={})
