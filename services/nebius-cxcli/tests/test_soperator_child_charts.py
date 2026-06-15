from __future__ import annotations

from nebius_cxcli.soperator_child_charts import (
    materialize_soperator_child_chart_values,
    soperator_child_chart_warnings,
)


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
                        "soperator-activechecks": {
                            "enabled": True,
                            "slurmClusterRefName": "soperator",
                        },
                    },
                },
            ]
        }
    }

    assert materialize_soperator_child_chart_values(payload) is True

    soperator_values = payload["apps"]["charts"][0]["values"]
    assert soperator_values["soperator-checks"]["enabled"] is True
    activechecks_values = soperator_values["soperator-activechecks"]
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
                        "soperator-activechecks": {
                            "enabled": True,
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
                            },
                        },
                    },
                },
            ]
        }
    }

    assert materialize_soperator_child_chart_values(payload) is True

    env = payload["apps"]["charts"][0]["values"]["soperator-activechecks"]["checks"]["ssh-check"][
        "k8sJobSpec"
    ]["jobContainer"]["env"]
    assert env == [
        {"name": "HTTP_PROXY", "value": "http://proxy"},
        {"name": "NUM_OF_LOGIN_NODES", "value": "2"},
    ]


def test_disabled_activechecks_leave_soperator_values_unchanged() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "enabled": True,
                    "values": {
                        "slurmNodes": {"login": {"size": 2}},
                        "soperator-activechecks": {
                            "enabled": False,
                            "ssh-check": {
                                "k8sJobSpec": {
                                    "jobContainer": {
                                        "env": [{"name": "NUM_OF_LOGIN_NODES", "value": "1"}]
                                    }
                                }
                            },
                        },
                    },
                },
            ]
        }
    }

    assert materialize_soperator_child_chart_values(payload) is False
    assert "soperator-checks" not in payload["apps"]["charts"][0]["values"]


def test_soperator_child_chart_warnings_are_empty_for_default_disabled_gates() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "values": {},
                },
            ]
        }
    }

    assert soperator_child_chart_warnings(payload) == ()


def test_soperator_child_chart_warnings_flag_activechecks_training_impact() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "values": {
                        "soperator-activechecks": {"enabled": True},
                    },
                },
            ]
        }
    }

    warnings = soperator_child_chart_warnings(payload)

    assert len(warnings) == 1
    assert "ActiveChecks are enabled for target cluster1" in warnings[0]
    assert "not production training clusters" in warnings[0]


def test_soperator_child_chart_warnings_flag_checks_controller_without_activechecks() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "values": {
                        "soperator-checks": {"enabled": True},
                        "soperator-activechecks": {"enabled": False},
                    },
                },
            ]
        }
    }

    warnings = soperator_child_chart_warnings(payload)

    assert len(warnings) == 1
    assert "checks controller is enabled for target cluster1" in warnings[0]
    assert "does not run GPU benchmarks by itself" in warnings[0]
    assert "SlurmNodeDrain" in warnings[0]
    assert "NebiusMaintenanceScheduled" in warnings[0]
    assert "graceful maintenance drain/node handoff" in warnings[0]
    assert "actual host reboot signal" in warnings[0]
    assert "Soperator-managed node maintenance automation" in warnings[0]


def test_soperator_child_chart_warnings_flag_rebooter_host_maintenance() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "values": {
                        "rebooter": {"enabled": True},
                    },
                },
            ]
        }
    }

    warnings = soperator_child_chart_warnings(payload)

    assert len(warnings) == 1
    assert "NodeConfigurator rebooter is enabled for target cluster1" in warnings[0]
    assert "privileged host-level helper" in warnings[0]
    assert "SlurmNodeReboot" in warnings[0]
    assert "SlurmNodeDrain only performs graceful maintenance drain/node handoff" in warnings[0]
    assert "actual host reboot happens only after SlurmNodeReboot" in warnings[0]


def test_soperator_child_chart_warnings_flag_soperator_dcgm_exporter() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "values": {
                        "soperator-dcgm-exporter": {"enabled": True},
                    },
                },
            ]
        }
    }

    warnings = soperator_child_chart_warnings(payload)

    assert len(warnings) == 1
    assert "Soperator DCGM job-mapping exporter is enabled for target cluster1" in warnings[0]
    assert "NVIDIA GPU Operator DCGM exporter plus the Nebius Observability Agent" in warnings[0]


def test_soperator_null_chart_values_are_pruned_before_render() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "enabled": True,
                    "values": {
                        "certManager": {"enabled": None},
                        "customContainer": {"enabled": False},
                        "mariadb-operator": {
                            "webhook": {
                                "enabled": None,
                                "cert": {"certManager": {"enabled": None}},
                            },
                            "metrics": {"enabled": False},
                        },
                        "slurmScripts": {
                            "builtIn": {
                                "cleanup_enroot.sh": {
                                    "customContent": None,
                                    "customContentFile": None,
                                }
                            }
                        },
                    },
                },
            ]
        }
    }

    assert materialize_soperator_child_chart_values(payload) is True

    values = payload["apps"]["charts"][0]["values"]
    assert values["certManager"] == {}
    assert values["customContainer"]["enabled"] is False
    assert values["mariadb-operator"]["webhook"]["cert"]["certManager"] == {}
    assert "enabled" not in values["mariadb-operator"]["webhook"]
    assert values["mariadb-operator"]["metrics"]["enabled"] is False
    cleanup_enroot = values["slurmScripts"]["builtIn"]["cleanup_enroot.sh"]
    assert cleanup_enroot["customContent"] is None
    assert cleanup_enroot["customContentFile"] is None


def test_notifier_mysterybox_source_preserves_explicitly_disabled_target_sync() -> None:
    payload = {
        "deploy": {
            "targets": [
                {
                    "instance_id": "cluster1",
                    "secrets": {
                        "mysterybox": {
                            "enabled": False,
                            "sync_namespaces": ["default"],
                        }
                    },
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "namespace": "soperator",
                    "values": {
                        "soperator-notifier": {
                            "enabled": True,
                            "slack": {
                                "mode": "existing-webhook",
                                "webhookSource": "mysterybox",
                                "existingSecret": "soperator-notifier-slack-webhook",
                                "existingSecretKey": "url",
                                "mysterybox": {
                                    "secretId": "mbsec-e00slack",
                                    "property": "url",
                                },
                            },
                        },
                    },
                },
            ]
        },
    }

    assert materialize_soperator_child_chart_values(payload) is False

    mysterybox = payload["deploy"]["targets"][0]["secrets"]["mysterybox"]
    assert mysterybox["enabled"] is False
    assert mysterybox["sync_namespaces"] == ["default"]


def test_notifier_mysterybox_source_preserves_scalar_disabled_target_sync() -> None:
    payload = {
        "deploy": {
            "targets": [
                {
                    "instance_id": "cluster1",
                    "secrets": {"mysterybox": False},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "target_ref": "cluster1",
                    "enabled": True,
                    "namespace": "soperator",
                    "values": {
                        "soperator-notifier": {
                            "enabled": True,
                            "slack": {
                                "mode": "existing-webhook",
                                "webhookSource": "mysterybox",
                                "existingSecret": "soperator-notifier-slack-webhook",
                                "existingSecretKey": "url",
                                "mysterybox": {
                                    "secretId": "mbsec-e00slack",
                                    "property": "url",
                                },
                            },
                        },
                    },
                },
            ]
        },
    }

    assert materialize_soperator_child_chart_values(payload) is False

    assert payload["deploy"]["targets"][0]["secrets"]["mysterybox"] is False


def test_activechecks_writes_preserve_existing_underscore_value_keys() -> None:
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "values": {
                        "clusterName": "cluster-a",
                        "soperator_activechecks": {"enabled": True},
                        "soperator_checks": {"enabled": False},
                        "slurmNodes": {"login": {"size": 2}},
                    },
                }
            ]
        }
    }

    assert materialize_soperator_child_chart_values(payload) is True

    values = payload["apps"]["charts"][0]["values"]
    assert "soperator-activechecks" not in values
    assert "soperator-checks" not in values
    assert values["soperator_activechecks"]["slurmClusterRefName"] == "cluster-a"
    assert values["soperator_checks"]["enabled"] is True
