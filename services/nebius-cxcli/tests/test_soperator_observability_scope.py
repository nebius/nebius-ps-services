import base64
import copy
import json

import pytest
import yaml

from nebius_cxcli.config_model import to_runtime_payload
from nebius_cxcli.deployment_state import DeploymentGeneration
from nebius_cxcli.soperator_flux_graph import SOPERATOR_GRAPH_CONFIGMAP
from nebius_cxcli.soperator_observability_scope import (
    JAIL_LOGS,
    OWNED_WRITER,
    WRITER,
    normalize_native_patches,
    retired_writer_sources,
)
from test_observability_routing import payload


def generation(data, files=None):
    return DeploymentGeneration({"runtime_config": to_runtime_payload(data)}, files or {})


def encoded(document):
    return base64.b64encode(yaml.safe_dump(document).encode()).decode()


def graph_generation(contract):
    return generation(
        payload(),
        {
            "flux/targets/cluster/graph.yaml": encoded(
                {
                    "kind": "ConfigMap",
                    "metadata": {"name": SOPERATOR_GRAPH_CONFIGMAP},
                    "data": {"graph.json": json.dumps(contract)},
                }
            )
        },
    )


def test_only_token_writer_graph_retirement_is_allowed():
    release = {
        "upstreamReleaseName": "workload",
        "sourceKind": "HelmRepository",
        "sourceName": "workload",
        "dependencies": [OWNED_WRITER],
        "stage": 2,
        "version": "1.0",
    }
    writer = {
        **release,
        "upstreamReleaseName": WRITER,
        "sourceName": "writer",
        "dependencies": [],
        "stage": 1,
    }
    before = {
        "releases": [release, writer],
        "readiness": {"telemetryReleases": [OWNED_WRITER, "workload"]},
    }
    after = copy.deepcopy(before)
    after["releases"] = [{**release, "dependencies": [], "stage": 1}]
    after["readiness"]["telemetryReleases"] = ["workload"]
    assert retired_writer_sources(graph_generation(before), graph_generation(after), "cluster") == {
        ("HelmRepository", "writer")
    }
    after["releases"][0]["version"] = "2.0"
    with pytest.raises(RuntimeError, match="workload graph"):
        retired_writer_sources(graph_generation(before), graph_generation(after), "cluster")


def test_native_patches_preserve_nonrouting_values_and_jail_identity():
    def patch(index, path="/jail"):
        return {
            "target": {"name": JAIL_LOGS},
            "patch": yaml.safe_dump(
                [
                    {
                        "op": "replace",
                        "path": f"/spec/values/extraVolumes/{index}/hostPath/path",
                        "value": path,
                    }
                ]
            ),
        }

    assert normalize_native_patches([patch(0)]) == normalize_native_patches([patch(1)])
    assert normalize_native_patches([patch(0)]) != normalize_native_patches([patch(1, "/another")])
    strategic = {"patch": "spec:\n  values:\n    replicas: 3\n"}
    assert normalize_native_patches([strategic]) == [strategic]
    values = {
        "target": {"name": "soperator-fluxcd-vm-stack"},
        "patch": "- op: replace\n  path: /spec/values\n  value: {}\n",
    }
    assert normalize_native_patches([values]) == []
