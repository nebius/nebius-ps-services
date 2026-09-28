"""Status must use exact remote ownership without restoring executable state."""

import base64
import copy
import json

import pytest

from nebius_cxcli.deployment_applications import recorded_application_identity
from test_soperator_cli_surface import _paths


def record():
    generation = "sha256:" + "a" * 64
    identity = {"cluster_id": "mk8scluster-bound", "kubernetes_uid": "bound-uid"}
    journal = {
        "schema": "nebius-cxcli.deployment-applications.v1",
        "generation": generation,
        "selected": ["cluster-a"],
        "targets": {
            "cluster-a": {
                "identity": identity,
                "desiredBundle": generation,
                "status": "executing",
            }
        },
    }
    return journal, {
        "active": {
            "generation": generation,
            "plan": {"semanticPlan": {"selectedTargets": ["cluster-a"]}},
            "recovery": {},
        },
        "accepted": {"evidence": {"identities": {"cluster-a": dict(identity)}}},
    }


KEY = "tenant/project/generated/reports/deployment-applications.json"


@pytest.mark.parametrize(
    "fault", ["generation", "selection", "schema", "identity", "conflict", "encoding", "json"]
)
def test_remote_identity_rejects_ambiguous_or_corrupt_authority(tmp_path, fault):
    journal, value = record()
    if fault == "generation":
        journal["generation"] = "sha256:" + "b" * 64
    elif fault == "selection":
        value["active"]["plan"]["semanticPlan"]["selectedTargets"] = ["another"]
    elif fault == "schema":
        journal["schema"] = "unknown"
    elif fault == "identity":
        journal["targets"]["cluster-a"]["identity"]["cluster_id"] = ""
    elif fault == "conflict":
        value["accepted"]["evidence"]["identities"]["cluster-a"]["kubernetes_uid"] = "another"
    encoded = base64.b64encode(json.dumps(journal).encode()).decode()
    if fault == "encoding":
        encoded = "!invalid-base64!"
    elif fault == "json":
        encoded = base64.b64encode(b"{").decode()
    value["active"]["recovery"][KEY] = encoded
    with pytest.raises(RuntimeError):
        recorded_application_identity(value, paths=_paths(tmp_path), target_ref="cluster-a")
    assert not list(tmp_path.iterdir())


def test_remote_identity_exact_scope_and_detached_result(tmp_path):
    journal, value = record()
    encoded = base64.b64encode(json.dumps(journal).encode()).decode()
    value["active"]["recovery"][KEY] = encoded
    before = copy.deepcopy(value)
    result = recorded_application_identity(value, paths=_paths(tmp_path), target_ref="cluster-a")
    assert result == journal["targets"]["cluster-a"]["identity"]
    result["cluster_id"] = "changed"
    assert value == before
    assert (
        recorded_application_identity(value, paths=_paths(tmp_path), target_ref="another") is None
    )
    assert not list(tmp_path.iterdir())


def test_remote_identity_never_searches_another_projects_archive(tmp_path):
    journal, value = record()
    value["accepted"] = None
    value["active"]["recovery"][KEY.replace("tenant/", "other/")] = base64.b64encode(
        json.dumps(journal).encode()
    ).decode()
    assert (
        recorded_application_identity(value, paths=_paths(tmp_path), target_ref="cluster-a") is None
    )
    assert not list(tmp_path.iterdir())
