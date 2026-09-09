import copy

import pytest

from nebius_cxcli import soperator_config_materialization as materialization


def test_managed_render_binds_native_runtime_mounts_without_changing_worker_resources():
    worker = {
        "name": "worker",
        "gpu": {"enabled": True},
        "slurmd": {
            "resources": {"cpu": "32", "memory": "16Gi", "gpu": 8, "ephemeralStorage": "80Gi"},
            "volumes": {
                "customVolumeMounts": [
                    {"name": "customer", "mountPath": "/customer", "volumeSource": {"emptyDir": {}}}
                ]
            },
        },
    }
    original = copy.deepcopy(worker)
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
    mounts = worker["slurmd"]["volumes"]["customVolumeMounts"]
    assert {mount["name"] for mount in mounts} == {
        "slurm-scripts",
        "slurm-scripts-jail",
        "hpc-jobs-dir",
        "docker-daemon-config",
        "customer",
    }
    by_name = {mount["name"]: mount for mount in mounts}
    assert by_name["customer"] == original["slurmd"]["volumes"]["customVolumeMounts"][0]
    assert by_name["docker-daemon-config"]["readOnly"] is True
    assert by_name["docker-daemon-config"]["mountPath"] == "/etc/docker/daemon.json"
    assert worker["slurmd"]["resources"] == original["slurmd"]["resources"]
    before = copy.deepcopy(payload)
    materialization._materialize_soperator_render_only_values(payload)
    assert payload == before


def test_runtime_mount_collision_cannot_hide_behind_a_trailing_slash():
    values = {
        "nodesets": [
            {
                "name": "worker",
                "slurmd": {
                    "volumes": {
                        "customVolumeMounts": [
                            {
                                "name": "foreign",
                                "mountPath": "/opt/slurm_scripts",
                                "volumeSource": {"emptyDir": {}},
                            }
                        ]
                    }
                },
            }
        ]
    }
    with pytest.raises(ValueError, match="runtime mount slurm-scripts"):
        materialization._materialize_soperator_nodeset_runtime_mounts(values)
