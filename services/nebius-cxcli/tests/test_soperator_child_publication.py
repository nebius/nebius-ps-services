import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli.soperator_child_publication import (
    empty_values_postimage,
    pending_empty_values,
    recover_pending_materializations,
)
from nebius_cxcli.soperator_failures import SoperatorSafetyPauseError
from nebius_cxcli.soperator_graph_transition import HR, NativeGraphTransition, spec_digest
from nebius_cxcli.soperator_operation import soperator_sha256
from test_soperator_graph_transition import Cluster, desired, resource, transition


@pytest.fixture
def publication():
    cluster = Cluster()
    checkpoint = {}

    def persist(value):
        checkpoint.clear()
        checkpoint.update(copy.deepcopy(value))

    operation = transition(cluster, persist)
    child = resource("metrics")
    child["spec"] = {
        "suspend": True,
        "values": {"vmagent": {"spec": {"remoteWriteSettings": None}}},
    }
    cluster.resources[(HR, "flux-system", "metrics")] = child
    document = copy.deepcopy(child)
    document["spec"]["values"]["vmagent"]["spec"]["remoteWriteSettings"] = {}
    manifest = [document]

    def run(args, **kwargs):
        if args[:3] == ["helm", "get", "manifest"]:
            return SimpleNamespace(stdout=yaml.safe_dump_all(manifest))
        if args[:3] == ["helm", "get", "metadata"]:
            return SimpleNamespace(
                stdout=json.dumps({"applyMethod": "ssa", "status": "deployed", "revision": 2})
            )
        return cluster.run(args, **kwargs)

    operation.run = run
    operation.fence()
    operation.publish(desired(cluster))
    operation.wait_absent(timeout=0, interval=0)
    operation.suspend_parent(True)
    return cluster, operation, child, manifest, checkpoint


def test_exact_materialization_precedes_open_and_preserves_admission(publication):
    cluster, operation, child, manifest, _ = publication
    admission = copy.deepcopy(operation.admission)
    assert pending_empty_values(operation, cluster.parent, child, manifest, manifest[0]["spec"])
    operation.resume_child("flux-system", "metrics")
    assert child["spec"]["values"]["vmagent"]["spec"]["remoteWriteSettings"] == {}
    assert cluster.events[-2:] == [("metrics", True), ("metrics", False)]
    assert operation.admission == admission
    assert next(iter(operation.state["childMaterializations"].values()))["status"] == "verified"


@pytest.mark.parametrize("boundary", ["intent", "cas", "verified", "open-intent"])
def test_replay_across_materialization_boundaries(publication, boundary):
    cluster, operation, child, _, checkpoint = publication
    persist, run = operation.persist, operation.run
    failed = False

    def interrupted_save(value):
        nonlocal failed
        persist(value)
        records = list(value.get("childMaterializations", {}).values())
        selected = (
            (boundary == "intent" and records and records[0]["status"] == "pending")
            or (boundary == "verified" and records and records[0]["status"] == "verified")
            or (boundary == "open-intent" and value.get("openedChildren"))
        )
        if selected and not failed:
            failed = True
            raise RuntimeError("interrupted")

    def interrupted_run(args, **kwargs):
        result = run(args, **kwargs)
        if (
            boundary == "cas"
            and "patch" in args
            and "metrics" in args
            and "--dry-run=server" not in args
        ):
            raise RuntimeError("lost materialization response")
        return result

    operation.persist, operation.run = interrupted_save, interrupted_run
    with pytest.raises(RuntimeError):
        operation.resume_child("flux-system", "metrics")
    replay = NativeGraphTransition(checkpoint, run=run, persist=persist, authority=lambda: None)
    # The deploy invokes this before it can publish another parent revision.
    recover_pending_materializations(replay)
    replay.resume_child("flux-system", "metrics")
    assert child["spec"]["suspend"] is False
    assert next(iter(replay.state["childMaterializations"].values()))["status"] == "verified"
    assert [event for event in cluster.events if event == ("metrics", True)] == [("metrics", True)]


@pytest.mark.parametrize(
    "drift",
    [
        "nonempty",
        "extra",
        "missing",
        "scalar",
        "array",
        "outside",
        "unsuspended",
        "parent-active",
        "parent-reconciling",
        "revision",
    ],
)
def test_unqualified_drift_never_mutates(publication, drift):
    cluster, operation, child, _, _ = publication
    settings = child["spec"]["values"]["vmagent"]["spec"]
    if drift == "nonempty":
        settings["remoteWriteSettings"] = {"label": {"foreign": "value"}}
    elif drift == "extra":
        settings["foreign"] = True
    elif drift == "missing":
        del settings["remoteWriteSettings"]
    elif drift == "scalar":
        settings["remoteWriteSettings"] = 0
    elif drift == "array":
        settings["remoteWriteSettings"] = []
    elif drift == "outside":
        child["spec"]["timeout"] = "2m"
    elif drift == "unsuspended":
        child["spec"]["suspend"] = False
    elif drift == "parent-active":
        cluster.parent["spec"]["suspend"] = False
    elif drift == "parent-reconciling":
        cluster.parent["status"]["conditions"].append({"type": "Reconciling", "status": "True"})
    elif drift == "revision":
        cluster.parent["status"]["history"][0]["version"] = 3
    count = len(cluster.events)
    with pytest.raises(SoperatorSafetyPauseError):
        operation.resume_child("flux-system", "metrics")
    assert len(cluster.events) == count


@pytest.mark.parametrize("drift", ["preimage", "uid", "ownership"])
def test_verified_materialization_cannot_reauthorize_later_drift(publication, drift):
    cluster, operation, child, _, _ = publication
    operation.resume_child("flux-system", "metrics")
    child["spec"]["suspend"] = True
    child["spec"]["values"]["vmagent"]["spec"]["remoteWriteSettings"] = None
    if drift == "uid":
        child["metadata"]["uid"] = "replacement"
    elif drift == "ownership":
        child["metadata"]["annotations"]["meta.helm.sh/release-name"] = "other"
    count = len(cluster.events)
    with pytest.raises(SoperatorSafetyPauseError):
        operation.resume_child("flux-system", "metrics")
    assert len(cluster.events) == count


def test_no_generic_null_empty_equivalence():
    assert not empty_values_postimage({"chartRef": None}, {"chartRef": {}})
    assert not empty_values_postimage({"values": {"x": [None]}}, {"values": {"x": [{}]}})
    assert not empty_values_postimage({"values": {"x": {}}}, {"values": {"x": None}})
    assert not empty_values_postimage({"values": {"x": False}}, {"values": {"x": 0}})


def test_matching_child_uses_existing_path_without_materialization(publication):
    _, operation, child, manifest, _ = publication
    child["spec"] = copy.deepcopy(manifest[0]["spec"])
    operation.resume_child("flux-system", "metrics")
    assert "childMaterializations" not in operation.state
    assert spec_digest(child) == spec_digest(manifest[0])
    assert operation.state["admissionSha256"] == soperator_sha256(operation.admission)


@pytest.mark.parametrize(
    "metadata",
    [
        {"applyMethod": "csa", "revision": 2, "status": "deployed"},
        {"applyMethod": "ssa", "revision": 3, "status": "deployed"},
        {"applyMethod": "ssa", "revision": 2, "status": "pending-upgrade"},
    ],
)
def test_materialization_requires_the_exact_deployed_ssa_revision(publication, metadata):
    cluster, operation, _, _, _ = publication
    original = operation.run

    def run(args, **kwargs):
        if args[:3] == ["helm", "get", "metadata"]:
            return SimpleNamespace(stdout=json.dumps(metadata))
        return original(args, **kwargs)

    operation.run = run
    count = len(cluster.events)
    with pytest.raises(SoperatorSafetyPauseError):
        operation.resume_child("flux-system", "metrics")
    assert len(cluster.events) == count
