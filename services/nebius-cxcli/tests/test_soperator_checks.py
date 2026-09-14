from __future__ import annotations

import copy
from dataclasses import replace

import pytest
import yaml

from nebius_cxcli.soperator_checks import native_acceptance_job, slurm_acceptance_result
from nebius_cxcli.soperator_checks_policy import (
    CheckRule,
    compile_checks_policy,
    operation_checks_documents,
    paused_partition_configuration,
    propose_checks_target,
)


@pytest.fixture
def source(tmp_path, monkeypatch):
    chart = tmp_path / "helm/soperator-activechecks"
    (chart / "scripts").mkdir(parents=True)
    (chart / "scripts/user.sh").write_text("useradd soperatorchecks\n")
    (chart / "scripts/gpu.sh").write_text("#SBATCH --exclusive\nsrun nvidia-smi\n")
    (chart / "scripts/ready.sh").write_text("srun hostname\n")
    (chart / "scripts/retrigger.py").write_text("print('retrigger')\n")
    checks = {
        "create-user-soperatorchecks": {
            "enabled": True,
            "checkType": "k8sJob",
            "runAfterCreation": True,
            "suspend": True,
            "k8sJobSpec": {"scriptFile": "scripts/user.sh"},
        },
        "wait-for-soperatorchecks-srun-ready": {
            "enabled": True,
            "checkType": "k8sJob",
            "runAfterCreation": True,
            "suspend": True,
            "dependsOn": ["create-user-soperatorchecks"],
            "k8sJobSpec": {"scriptFile": "scripts/ready.sh"},
        },
        "gpu-fryer": {
            "enabled": True,
            "checkType": "slurmJob",
            "runAfterCreation": True,
            "suspend": False,
            "dependsOn": ["wait-for-soperatorchecks-srun-ready"],
            "slurmJobSpec": {
                "sbatchScriptFile": "scripts/gpu.sh",
                "eachWorkerJobs": True,
                "maxNumberOfJobs": 2,
            },
        },
        "retrigger-checks": {
            "enabled": True,
            "checkType": "k8sJob",
            "runAfterCreation": False,
            "suspend": True,
            "k8sJobSpec": {"pythonScriptFile": "scripts/retrigger.py"},
        },
    }
    (chart / "values.yaml").write_text(yaml.safe_dump({"checks": checks}))

    def render(_chart, overrides):
        return {
            name: {"schedule": "0 */6 * * *"}
            for name, row in checks.items()
            if overrides.get("checks", {}).get(name, {}).get("enabled", row["enabled"])
        }

    monkeypatch.setattr("nebius_cxcli.soperator_checks_policy._render_execution_specs", render)
    return tmp_path


@pytest.fixture
def values():
    return {
        "soperatorActiveChecks": {"enabled": True, "overrideValues": {}},
        "slurmCluster": {
            "overrideValues": {
                "partitionConfiguration": {
                    "configType": "structured",
                    "partitions": [
                        {"name": "main", "config": "Default=YES State=UP"},
                        {"name": "hidden", "config": "Hidden=YES State=UP"},
                    ],
                },
            }
        },
    }


def test_temporary_policy_preserves_desired_values_and_bootstrap(source, values):
    original = copy.deepcopy(values)
    policy = compile_checks_policy(source, values)
    temporary = policy.effective_values(values, installing=True)
    rows = temporary["soperatorActiveChecks"]["overrideValues"]["checks"]
    assert rows["create-user-soperatorchecks"]["runAfterCreation"] is True
    assert rows["wait-for-soperatorchecks-srun-ready"]["runAfterCreation"] is False
    assert rows["gpu-fryer"]["runAfterCreation"] is False
    assert all(row["suspend"] for row in rows.values())
    assert all(
        "State=DOWN" in row["config"]
        for row in temporary["slurmCluster"]["overrideValues"]["partitionConfiguration"][
            "partitions"
        ]
    )
    assert values == original
    assert [rule.name for rule in policy.required] == [
        "create-user-soperatorchecks",
        "wait-for-soperatorchecks-srun-ready",
        "gpu-fryer",
    ]
    assert policy.required[-1].concurrency == 2


def test_policy_binds_full_chart_and_values(source, values):
    policy = compile_checks_policy(source, values)
    (source / "helm/soperator-activechecks/scripts/gpu.sh").write_text("changed\n")
    assert compile_checks_policy(source, values).sha256 != policy.sha256
    changed = copy.deepcopy(values)
    changed["another"] = True
    with pytest.raises(ValueError, match="does not match"):
        policy.effective_values(changed, installing=False)


@pytest.mark.parametrize(
    "override",
    [
        {"waitForChecks": {"enabled": False}},
        {"checks": {"foreign": {"enabled": True, "checkType": "slurmJob"}}},
        {"checks": {"gpu-fryer": {"slurmJobSpec": {"sbatchScriptFile": "another.sh"}}}},
    ],
)
def test_unknown_or_ineffective_policy_fails_before_execution(source, values, override):
    values["soperatorActiveChecks"]["overrideValues"] = override
    with pytest.raises(ValueError):
        compile_checks_policy(source, values)


def test_disabled_platform_checks_stay_disabled(source, values):
    values["soperatorActiveChecks"]["overrideValues"] = {
        "checks": {"gpu-fryer": {"enabled": False}}
    }
    policy = compile_checks_policy(source, values)
    assert "gpu-fryer" not in {rule.name for rule in policy.rules}


def test_common_document_transform_cannot_restore_early(source, values):
    from nebius_cxcli.soperator_checks_binding import (
        CHECKS_RELEASE,
        auxiliary_post_renderers,
        bind_checks_jail,
        checks_post_renderers,
    )

    values["slurmCluster"]["overrideValues"]["clusterName"] = "example"
    values["soperatorActiveChecks"]["overrideValues"] = bind_checks_jail({}, "active-jail", [])
    policy = compile_checks_policy(source, values)
    documents = [
        {
            "kind": "ConfigMap",
            "metadata": {"name": "terraform-fluxcd-values"},
            "data": {"values.yaml": yaml.safe_dump(values)},
        },
        {
            "kind": "HelmRelease",
            "metadata": {"name": "custom-umbrella", "namespace": "flux-system"},
            "spec": {
                "chartRef": {"kind": "OCIRepository", "name": "helm-soperator"},
                "values": copy.deepcopy(values),
                "postRenderers": [
                    {
                        "kustomize": {
                            "patches": [
                                {
                                    "target": {"name": CHECKS_RELEASE},
                                    "patch": yaml.safe_dump(
                                        [
                                            {
                                                "op": "add",
                                                "path": "/spec/postRenderers",
                                                "value": checks_post_renderers("active-jail", []),
                                            }
                                        ]
                                    ),
                                }
                            ]
                        }
                    }
                ],
            },
        },
        {
            "kind": "HelmRelease",
            "metadata": {"name": "ordinary"},
            "spec": {"values": {"replicas": 1}},
        },
    ]
    effective = operation_checks_documents(
        documents,
        policy,
        installing=True,
        outer_namespace="flux-system",
        outer_name="custom-umbrella",
    )
    for bundle in (copy.deepcopy(effective), copy.deepcopy(effective)):
        parsed = yaml.safe_load(bundle[0]["data"]["values.yaml"])
        assert (
            parsed["soperatorActiveChecks"]["overrideValues"]["checks"]["gpu-fryer"][
                "runAfterCreation"
            ]
            is False
        )
        assert bundle[1]["spec"]["values"] == parsed
    assert effective[2] == documents[2]
    child = yaml.safe_load(
        effective[1]["spec"]["postRenderers"][0]["kustomize"]["patches"][0]["patch"]
    )
    assert child[0]["value"] == auxiliary_post_renderers(values, suspended=True)
    auxiliary = yaml.safe_load(child[0]["value"][0]["kustomize"]["patches"][0]["patch"])
    cluster = values["slurmCluster"]["overrideValues"]["clusterName"]
    assert any(op.get("value") == cluster + "-munge" for op in auxiliary)
    assert documents[1]["spec"]["values"] == values
    assert yaml.safe_load(documents[0]["data"]["values.yaml"]) == values


@pytest.mark.parametrize(
    "corruption", ["missing", "duplicate", "changed-values", "wrong-namespace"]
)
def test_operation_policy_rejects_ambiguous_or_drifted_umbrella(source, values, corruption):
    policy = compile_checks_policy(source, values)
    cm = {
        "kind": "ConfigMap",
        "metadata": {"name": "terraform-fluxcd-values"},
        "data": {"values.yaml": yaml.safe_dump(values)},
    }
    hr = {
        "kind": "HelmRelease",
        "metadata": {"name": "umbrella", "namespace": "flux-system"},
        "spec": {"values": copy.deepcopy(values)},
    }
    documents = [cm, hr]
    if corruption == "missing":
        documents.pop()
    elif corruption == "duplicate":
        documents.append(copy.deepcopy(hr))
    elif corruption == "changed-values":
        hr["spec"]["values"]["unrelated"] = "changed"
    else:
        hr["metadata"]["namespace"] = "foreign"
    with pytest.raises(ValueError, match="exact matching umbrella"):
        operation_checks_documents(
            documents, policy, installing=True, outer_namespace="flux-system", outer_name="umbrella"
        )


def test_initial_barrier_rejects_implicit_partition_inventory():
    with pytest.raises(ValueError, match="structured"):
        paused_partition_configuration({"configType": "default"})


def test_enabling_proposal_is_explicit_and_preserves_platform_exclusions():
    original = {
        "soperator-activechecks": {
            "enabled": False,
            "waitForChecks": {"enabled": False},
            "checks": {
                "gpu-fryer": {"runAfterCreation": False},
                "cuda-samples": {"enabled": False, "runAfterCreation": False},
            },
        }
    }
    target, changes = propose_checks_target(original)
    assert original["soperator-activechecks"]["enabled"] is False
    assert target["soperator-activechecks"]["enabled"] is True
    assert target["soperator-checks"]["enabled"] is True
    assert "runAfterCreation" not in target["soperator-activechecks"]["checks"]["gpu-fryer"]
    assert target["soperator-activechecks"]["checks"]["cuda-samples"]["enabled"] is False
    assert any("gpu-fryer" in item for item in changes)
    assert any("waitForChecks" in item for item in changes)


@pytest.fixture
def cronjob():
    # Upstream jobTemplate.metadata is empty: labels live on CronJob and Pod.
    return {
        "metadata": {"labels": {"component": "soperatorchecks"}},
        "spec": {
            "jobTemplate": {
                "metadata": {},
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {"slurm.nebius.ai/active-check-name": "gpu-fryer"}
                        },
                        "spec": {
                            "containers": [
                                {
                                    "name": "check",
                                    "image": "upstream:fixed",
                                    "env": [{"name": "EACH_WORKER_JOBS", "value": "true"}],
                                }
                            ],
                            "volumes": [
                                {
                                    "name": "sbatch-volume",
                                    "configMap": {"name": "sbatch-script-gpu-fryer"},
                                }
                            ],
                        },
                    }
                },
            }
        },
    }


def test_native_job_retains_upstream_controller_linkage_and_avoids_bulk_branch(cronjob):
    rule = CheckRule("gpu-fryer", "slurmJob", False, True, False, (), True, 200, "sha256:script")
    job = native_acceptance_job(
        cronjob,
        rule=rule,
        operation_id="operation",
        reservation="cxcli_0123",
        worker="worker-0",
        sbatch_script="#!/bin/bash\nsrun gpu-fryer\n",
    )
    assert job["metadata"]["labels"]["component"] == "soperatorchecks"
    assert (
        job["spec"]["template"]["metadata"]["annotations"]["slurm.nebius.ai/active-check-name"]
        == "gpu-fryer"
    )
    env = {
        item["name"]: item["value"]
        for item in job["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["RESERVATION_NAME"] == "cxcli_0123"
    assert "SBATCH_NODELIST" not in env
    assert "SBATCH_NODES" not in env
    assert env["ACTIVE_CHECK_NAME"] == job["metadata"]["name"]
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
    other = native_acceptance_job(
        cronjob,
        rule=replace(rule),
        operation_id="operation",
        reservation="cxcli_0123",
        worker="worker-1",
        sbatch_script="#!/bin/bash\nsrun gpu-fryer\n",
    )
    assert other["metadata"]["name"] != job["metadata"]["name"]


@pytest.mark.parametrize(
    "record", ["", "42|RUNNING|0:0|worker-0|soperatorchecks|check|reserve|cpu=8"]
)
def test_missing_or_nonterminal_slurm_evidence_is_not_success(record):
    assert (
        slurm_acceptance_result(
            record,
            job_id="42",
            name="check",
            reservation="reserve",
            expected_nodes=("worker-0",),
            expand_nodes=lambda s: (s,),
        )
        is None
    )


@pytest.mark.parametrize(
    "record",
    [
        "42|CANCELLED|0:0|worker-0|soperatorchecks|check|reserve|cpu=8",
        "42|COMPLETED|1:0|worker-0|soperatorchecks|check|reserve|cpu=8",
        "42|COMPLETED|0:0|worker-1|soperatorchecks|check|reserve|cpu=8",
        "42|COMPLETED|0:0|worker-0|customer|check|reserve|cpu=8",
        "42|COMPLETED|0:0|worker-0|soperatorchecks|check|foreign|cpu=8",
    ],
)
def test_wrong_or_failed_slurm_evidence_fails_closed(record):
    with pytest.raises(RuntimeError):
        slurm_acceptance_result(
            record,
            job_id="42",
            name="check",
            reservation="reserve",
            expected_nodes=("worker-0",),
            expand_nodes=lambda s: (s,),
        )


def test_success_requires_exact_job_identity_and_coverage():
    result = slurm_acceptance_result(
        "42|COMPLETED|0:0|worker-0|soperatorchecks|check|reserve|cpu=8",
        job_id="42",
        name="check",
        reservation="reserve",
        expected_nodes=("worker-0",),
        expand_nodes=lambda s: (s,),
    )
    assert result == {
        "jobId": "42",
        "state": "COMPLETED",
        "exitCode": "0:0",
        "nodes": ["worker-0"],
        "allocatedGpus": 0,
    }


def test_auxiliary_schedule_is_derived_from_the_frozen_render_without_new_identity(
    source, values, monkeypatch
):
    from dataclasses import replace

    from nebius_cxcli import soperator_checks_policy as module

    chart = source / "helm/soperator-activechecks"
    (chart / "templates").mkdir()
    (chart / "templates/run-extensive-check-on-reservations.yaml").write_text("frozen template")
    values["soperatorActiveChecks"]["overrideValues"]["jobContainer"] = {
        "volumes": [{"name": "jail", "persistentVolumeClaim": {"claimName": "jail-active"}}]
    }
    expected = {"schedule": "*/5 * * * *", "timeZone": "Etc/UTC", "suspend": False}
    values["slurmCluster"]["overrideValues"]["clusterName"] = "cluster"
    expected["jobTemplate"] = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "check",
                            "volumeMounts": [{"name": "jail", "mountPath": "/mnt/jail"}],
                        }
                    ],
                    "volumes": [
                        {"name": "jail", "persistentVolumeClaim": {"claimName": "jail-pvc"}},
                        {"name": "slurm-configs", "configMap": {"name": "soperator-slurm-configs"}},
                        {"name": "munge-key", "secret": {"secretName": "soperator-munge"}},
                    ],
                }
            }
        }
    }
    monkeypatch.setattr(
        module,
        "_cached_execution_specs",
        lambda *_: (
            {
                "kind": "CronJob",
                "metadata": {"name": "run-extensive-check-on-reservations"},
                "spec": expected,
            },
        ),
    )
    policy = compile_checks_policy(source, values)
    from nebius_cxcli.soperator_checks_binding import bind_auxiliary_spec

    assert policy.auxiliary_spec == bind_auxiliary_spec(
        expected, "jail-active", [], cluster="cluster"
    )
    assert policy.auxiliary_pvc == "jail-active"
    assert replace(policy, auxiliary_spec={}).sha256 == policy.sha256


@pytest.mark.parametrize("defaults", [["0 0 1 1 *"], ["0 0 1 1 *", "1 0 1 1 *"], [None]])
def test_omitted_schedule_uses_unambiguous_served_upstream_crd_default(
    source, values, monkeypatch, defaults
):
    path = source / "helm/soperator-crds/templates/slurmcluster-crd.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "metadata": {"name": "activechecks.slurm.nebius.ai"},
                "spec": {
                    "versions": [
                        {
                            "served": True,
                            "schema": {
                                "openAPIV3Schema": {
                                    "properties": {
                                        "spec": {"properties": {"schedule": {"default": value}}}
                                    }
                                }
                            },
                        }
                        for value in defaults
                    ]
                },
            }
        )
    )
    names = yaml.safe_load((source / "helm/soperator-activechecks/values.yaml").read_text())[
        "checks"
    ]
    monkeypatch.setattr(
        "nebius_cxcli.soperator_checks_policy._render_execution_specs",
        lambda *_: {
            name: {} if name == "gpu-fryer" else {"schedule": "0 */6 * * *"} for name in names
        },
    )
    if defaults != ["0 0 1 1 *"]:
        with pytest.raises(ValueError, match="schedule defaults"):
            compile_checks_policy(source, values)
    else:
        policy = compile_checks_policy(source, values)
        assert policy.execution_specs["gpu-fryer"]["schedule"] == "0 0 1 1 *"
        assert policy.execution_specs["create-user-soperatorchecks"]["schedule"] == "0 */6 * * *"
