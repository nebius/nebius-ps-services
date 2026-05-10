from __future__ import annotations

from nebius_cxcli.soperator_companions import materialize_soperator_companion_app_values


def test_activechecks_slurm_cluster_ref_follows_soperator_cluster_name() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "values": {
                        "clusterName": "cluster1",
                        "slurmNodes": {"login": {"size": 3}},
                    },
                },
                {
                    "id": "soperator-activechecks",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "values": {"slurmClusterRefName": "soperator"},
                },
            ]
        }
    }

    assert materialize_soperator_companion_app_values(payload) is True

    activechecks_values = payload["apps"]["charts"][1]["values"]
    assert activechecks_values["slurmClusterRefName"] == "cluster1"
    env = activechecks_values["checks"]["ssh-check"]["k8sJobSpec"]["jobContainer"]["env"]
    assert env == [{"name": "NUM_OF_LOGIN_NODES", "value": "3"}]


def test_activechecks_login_env_preserves_custom_env_items() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "enabled": True,
                    "values": {
                        "slurmNodes": {"login": {"size": 2}},
                    },
                },
                {
                    "id": "soperator-activechecks",
                    "enabled": True,
                    "values": {
                        "checks": {
                            "ssh-check": {
                                "k8sJobSpec": {
                                    "jobContainer": {
                                        "env": [
                                            {"name": "HTTP_PROXY", "value": "http://proxy"},
                                            {"name": "NUM_OF_LOGIN_NODES", "value": "1"},
                                        ]
                                    }
                                }
                            }
                        }
                    },
                },
            ]
        }
    }

    assert materialize_soperator_companion_app_values(payload) is True

    env = payload["apps"]["charts"][1]["values"]["checks"]["ssh-check"]["k8sJobSpec"][
        "jobContainer"
    ]["env"]
    assert env == [
        {"name": "HTTP_PROXY", "value": "http://proxy"},
        {"name": "NUM_OF_LOGIN_NODES", "value": "2"},
    ]
