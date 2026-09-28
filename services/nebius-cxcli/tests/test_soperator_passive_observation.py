"""Worker observation compares Kubernetes map lists by their declared keys."""

import copy
from types import SimpleNamespace

import pytest

from nebius_cxcli.soperator_passive_checks import PassiveDiagnostics


def pod():
    return {
        "metadata": {"uid": "pod-uid"},
        "spec": {"nodeName": "node"},
        "status": {
            "containerStatuses": [
                {
                    "name": "slurmd",
                    "ready": True,
                    "containerID": "containerd://id",
                    "restartCount": 0,
                    "allocatedResourcesStatus": [
                        {
                            "name": "nvidia.com/gpu",
                            "resources": [
                                {"resourceID": "gpu-a", "health": "Healthy"},
                                {"resourceID": "gpu-b", "health": "Healthy"},
                            ],
                        },
                        {
                            "name": "rdma/shared_device",
                            "resources": [{"resourceID": "rdma-a", "health": "Healthy"}],
                        },
                    ],
                    "volumeMounts": [
                        {"name": "a", "mountPath": "/a"},
                        {"name": "b", "mountPath": "/b"},
                    ],
                    "user": {"linux": {"supplementalGroups": [1, 2]}},
                }
            ]
        },
    }


@pytest.mark.parametrize(
    "change",
    [
        "order",
        "health",
        "resource-id",
        "resource-count",
        "container",
        "restart",
        "readiness",
        "uid",
        "node",
        "volume",
        "atomic-order",
        "duplicate-resource",
        "duplicate-name",
        "duplicate-mount",
    ],
)
def test_observation_ignores_only_declared_map_order(change):
    before = pod()
    after = copy.deepcopy(before)
    status = after["status"]["containerStatuses"][0]
    resources = status["allocatedResourcesStatus"][0]["resources"]
    if change == "order":
        resources.reverse()
        status["allocatedResourcesStatus"].reverse()
        status["volumeMounts"].reverse()
    elif change == "health":
        resources[0]["health"] = "Unhealthy"
    elif change == "resource-id":
        resources[0]["resourceID"] = "replacement"
    elif change == "resource-count":
        resources.pop()
    elif change == "container":
        status["containerID"] = "containerd://replacement"
    elif change == "restart":
        status["restartCount"] = 1
    elif change == "readiness":
        status["ready"] = False
    elif change == "uid":
        after["metadata"]["uid"] = "replacement"
    elif change == "node":
        after["spec"]["nodeName"] = "replacement"
    elif change == "volume":
        status["volumeMounts"][0]["readOnly"] = True
    elif change == "atomic-order":
        status["user"]["linux"]["supplementalGroups"].reverse()
    elif change == "duplicate-resource":
        resources.append(copy.deepcopy(resources[0]))
    elif change == "duplicate-name":
        status["allocatedResourcesStatus"].append(
            copy.deepcopy(status["allocatedResourcesStatus"][0])
        )
    else:
        status["volumeMounts"].append(copy.deepcopy(status["volumeMounts"][0]))
    snapshots = iter([before, after])
    checks = SimpleNamespace(
        _get=lambda *args: next(snapshots),
        authority=lambda: None,
        kube=lambda *args: {"worker": "worker-0"},
    )
    observer = PassiveDiagnostics(checks)
    if change == "order":
        result = observer._observe("worker-0", {}, "observe")
        assert result["identity"] == {
            "uid": "pod-uid",
            "container": "containerd://id",
            "restarts": 0,
            "node": "node",
        }
    else:
        with pytest.raises(RuntimeError, match="passive"):
            observer._observe("worker-0", {}, "observe")
    assert before == pod(), "comparison must not modify the API snapshot"
