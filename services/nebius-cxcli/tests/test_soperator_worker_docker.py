import copy

import pytest

from nebius_cxcli import soperator_config_materialization as materialization
from nebius_cxcli.soperator_worker_docker import materialize_worker_docker


def test_render_gpu_worker_binds_upstream_docker_runtime():
    worker = {
        "name": "worker",
        "slurmd": {
            "resources": {"gpu": 8, "cpu": "32", "memory": "16Gi", "ephemeralStorage": "55Gi"}
        },
    }
    original = copy.deepcopy(worker["slurmd"]["resources"])
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "enabled": True,
                    "target": "mk8s",
                    "values": {"nodesets": [worker]},
                }
            ]
        }
    }
    materialization._materialize_soperator_render_only_values(payload)
    assert worker["configMapRefSupervisord"] == "custom-supervisord-config"
    mounts = worker["slurmd"]["volumes"]["customVolumeMounts"]
    daemon = next(m for m in mounts if m["mountPath"] == "/etc/docker/daemon.json")
    assert daemon["volumeSource"]["configMap"]["name"] == "image-storage"
    assert daemon["subPath"] == "daemon.json" and daemon["readOnly"] is True
    assert worker["slurmd"]["volumes"]["jailSubMounts"] == [
        {
            "name": "docker-image-storage",
            "mountPath": "/mnt/image-storage",
            "volumeSource": {"emptyDir": {}},
        }
    ]
    assert worker["slurmd"]["resources"] == original
    saved = copy.deepcopy(payload)
    materialization._materialize_soperator_render_only_values(payload)
    assert payload == saved


def test_docker_runtime_leaves_cpu_workers_unchanged():
    values = {"nodesets": [{"name": "cpu", "slurmd": {"resources": {"cpu": "8"}}}]}
    before = copy.deepcopy(values)
    assert materialize_worker_docker(values) is False
    assert values == before


@pytest.mark.parametrize("collision", ["supervisor", "path", "name", "scope", "type"])
def test_docker_runtime_rejects_conflicting_explicit_configuration(collision):
    node = {"name": "worker", "slurmd": {"resources": {"gpu": 8}, "volumes": {}}}
    if collision == "supervisor":
        node["configMapRefSupervisord"] = "other"
    elif collision == "type":
        node["slurmd"]["volumes"]["customVolumeMounts"] = [None]
    else:
        node["slurmd"]["volumes"]["customVolumeMounts"] = [
            {
                "name": "docker-daemon-config"
                if collision == "name"
                else "docker-image-storage"
                if collision == "scope"
                else "other",
                "mountPath": "/etc/docker/daemon.json/" if collision == "path" else "/other",
                "volumeSource": {"emptyDir": {}},
            }
        ]
    before = copy.deepcopy(node)
    with pytest.raises(ValueError):
        materialize_worker_docker({"nodesets": [node]})
    assert node == before
