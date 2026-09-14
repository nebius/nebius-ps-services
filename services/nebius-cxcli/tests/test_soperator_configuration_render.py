"""Final chart-consumer assertions against pinned, unmodified upstream fixtures."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from nebius_cxcli.soperator_adapter import compile_upstream_soperator_values
from nebius_cxcli.soperator_config_materialization import _materialize_soperator_guided_sssd_values
from nebius_cxcli.soperator_release import SoperatorReleaseGraphNode
from nebius_cxcli.soperator_release_artifacts import (
    render_soperator_consumers,
    soperator_consumer_namespaces,
)
from nebius_cxcli.soperator_release_source import SoperatorSourceReceipt
from nebius_cxcli.soperator_values import validate_frozen_backup_values
from soperator_fixtures import sample_snapshot
from test_soperator_upstream_adapter import _values


@pytest.fixture
def frozen_charts(tmp_path):
    if not shutil.which("helm"):
        pytest.skip("helm is required for frozen chart integration validation")
    fixtures = Path(__file__).parent / "fixtures"
    archive = fixtures / "soperator-configuration-4.1.8.tar.gz"
    hashes = json.loads((fixtures / "soperator-configuration-4.1.8.json").read_text())
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == hashes["fixture_sha256"]
    with tarfile.open(archive) as source:
        source.extractall(tmp_path, filter="data")
    for name, expected in hashes["files"].items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == expected
    snapshot = sample_snapshot(release="4.1.8")
    roles = {
        "umbrella": "soperator-fluxcd",
        "slurmCluster": "slurm-cluster",
        "nodesets": "nodesets",
        "backupConfig": "soperator-backup-config",
    }
    charts = {
        role: replace(snapshot.umbrella, source_path=f"helm/{name}", name=f"helm-{name}")
        for role, name in roles.items()
    }
    graph = tuple(
        SoperatorReleaseGraphNode(
            release_name=f"soperator-fluxcd-{suffix}",
            namespace="flux-system",
            owner="upstream",
            stage=index,
            chart_key=role,
            dependencies=(),
            is_main=role == "slurmCluster",
        )
        for index, (role, suffix) in enumerate(
            (
                ("slurmCluster", "slurm-cluster"),
                ("nodesets", "nodesets"),
                ("backupConfig", "backup-config"),
            )
        )
    )
    snapshot = replace(snapshot, charts=charts, release_graph=graph)
    receipt = SoperatorSourceReceipt(
        schema="fixture",
        release=snapshot.release,
        commit=snapshot.commit,
        tree=snapshot.tree,
        archive_sha256=snapshot.archive_sha256,
        manifest_sha256=snapshot.source_manifest_sha256,
        source_dir=str(tmp_path),
    )
    return snapshot, receipt, tmp_path


def _render_child(doc, chart):
    values = chart.parent / "test-values.yaml"
    values.write_text(yaml.safe_dump(doc["spec"].get("values") or {}))
    result = subprocess.run(
        [
            "helm",
            "template",
            doc["spec"]["releaseName"],
            str(chart),
            "--namespace",
            doc["spec"]["targetNamespace"],
            "--values",
            str(values),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def test_backup_and_sssd_reach_real_frozen_chart_consumers(frozen_charts):
    snapshot, receipt, source = frozen_charts
    values = _values()
    values["sssd"] = {
        "enabled": True,
        "sssdConfSecretRefName": "directory",
        "sssdLdapCAConfigMapRefName": "ldap-ca",
    }
    values["soperator-backup-config"] = {
        "enabled": True,
        "bucket": {"name": "existing-backup", "endpoint": "https://storage.example.invalid"},
        "secret": {
            "name": "backup-credentials",
            "keys": {
                "accessKeyID": "access",
                "secretAccessKey": "secret",
                "backupPassword": "repository",
            },
        },
        "backup": {"schedule": "0 1 * * *"},
        "prune": {"schedule": "0 2 * * *", "retention": {"keepDaily": 13}},
    }
    _materialize_soperator_guided_sssd_values(values)
    umbrella, _ = compile_upstream_soperator_values(values, release=snapshot)
    assert "sssd" not in umbrella["slurmCluster"]["overrideValues"]
    assert "overrideValues" not in umbrella["backup"]["config"]
    consumers = render_soperator_consumers(snapshot, receipt, umbrella)
    namespaces = soperator_consumer_namespaces(snapshot, consumers)
    assert namespaces == {
        "backupConfig": "soperator",
        "slurmCluster": "soperator",
        "nodesets": "soperator",
    }
    by_name = {doc["metadata"]["name"]: doc for doc in consumers}
    backup = _render_child(
        by_name["soperator-fluxcd-backup-config"], source / "helm/soperator-backup-config"
    )
    schedule = next(doc["spec"] for doc in backup if doc["kind"] == "Schedule")
    assert schedule["backend"]["s3"]["bucket"] == "existing-backup"
    assert schedule["backend"]["s3"]["endpoint"] == "https://storage.example.invalid"
    assert schedule["backup"]["schedule"] == "0 1 * * *"
    assert schedule["prune"]["schedule"] == "0 2 * * *"
    assert schedule["prune"]["retention"]["keepDaily"] == 13
    assert schedule["backend"]["s3"]["accessKeyIDSecretRef"] == {
        "name": "backup-credentials",
        "key": "access",
    }
    slurm = _render_child(by_name["soperator-fluxcd-slurm-cluster"], source / "helm/slurm-cluster")
    cluster = next(doc for doc in slurm if doc["kind"] == "SlurmCluster")
    assert cluster["spec"]["slurmNodes"]["controller"]["sssdConfSecretRefName"] == "directory"
    assert cluster["spec"]["slurmNodes"]["login"]["sssdLdapCAConfigMapRefName"] == "ldap-ca"
    nodes = _render_child(by_name["soperator-fluxcd-nodesets"], source / "helm/nodesets")
    node = next(doc for doc in nodes if doc["kind"] == "NodeSet")
    assert node["spec"]["sssdConfSecretRefName"] == "directory"
    assert node["spec"]["sssdLdapCAConfigMapRefName"] == "ldap-ca"


def test_backup_consumer_namespace_comes_from_actual_umbrella_template(frozen_charts):
    snapshot, receipt, _ = frozen_charts
    consumers = render_soperator_consumers(
        snapshot,
        receipt,
        {
            "slurmCluster": {"namespace": "backups"},
            "backup": {"enabled": True, "config": {"enabled": True}},
        },
    )
    assert soperator_consumer_namespaces(snapshot, consumers)["backupConfig"] == "backups"


def test_nested_backup_typo_is_rejected_against_frozen_contract(frozen_charts):
    _, _, source = frozen_charts
    defaults = yaml.safe_load((source / "helm/soperator-backup-config/values.yaml").read_text())
    with pytest.raises(ValueError, match="backup.failedJobsHistoryLimits"):
        validate_frozen_backup_values({"backup": {"failedJobsHistoryLimits": 5}}, defaults)
    validate_frozen_backup_values(
        {"backup": {"failedJobsHistoryLimit": 5}, "prune": {"retention": {"keepWeekly": 3}}},
        defaults,
    )


def _render_values(chart, values):
    return _render_child(
        {"spec": {"releaseName": "fixture", "targetNamespace": "soperator", "values": values}},
        chart,
    )


@pytest.mark.parametrize("keys", [[], ["ssh-ed25519 first", "ssh-rsa second"]])
def test_root_keys_reach_upstream_cluster_unchanged(frozen_charts, keys):
    snapshot, _, source = frozen_charts
    values = _values()
    values["slurmNodes"].setdefault("login", {})["sshRootPublicKeys"] = keys
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    rendered = _render_values(
        source / "helm/slurm-cluster", compiled["slurmCluster"]["overrideValues"]
    )
    cluster = next(row for row in rendered if row["kind"] == "SlurmCluster")
    assert cluster["spec"]["slurmNodes"]["login"]["sshRootPublicKeys"] == keys


def test_retained_homes_reach_frozen_bootstrap_checks_and_auxiliary_job(frozen_charts, tmp_path):
    from nebius_cxcli.soperator_checks_binding import (
        auxiliary_post_renderers,
        retained_check_mounts,
    )
    from nebius_cxcli.soperator_checks_policy import compile_checks_policy
    from nebius_cxcli.soperator_checks_scheduling import auxiliary_storage_matches

    if not shutil.which("kubectl"):
        pytest.skip("kubectl kustomize is required for native postrenderer validation")
    snapshot, _, source = frozen_charts
    compiled, _ = compile_upstream_soperator_values(_values(), release=snapshot)
    policy = compile_checks_policy(source, compiled)
    bindings = retained_check_mounts(compiled)
    expected = {"/mnt/jail" + row["mount_path"]: row["name"] for row in bindings}
    assert "/mnt/jail/opt/soperator-home" in expected
    assert {"create-user-nebius", "create-user-soperatorchecks"} <= policy.execution_specs.keys()
    for spec in policy.execution_specs.values():
        job = spec[spec["checkType"] + "Spec"]
        actual = {row["mountPath"]: row["name"] for row in job["jobContainer"]["volumeMounts"]}
        assert actual.items() >= expected.items()
    rendered = _render_values(
        source / "helm/soperator-activechecks", compiled["soperatorActiveChecks"]["overrideValues"]
    )
    native = next(row for row in rendered if row["kind"] == "CronJob")
    directory = tmp_path / "auxiliary"
    directory.mkdir()
    (directory / "cron.yaml").write_text(yaml.safe_dump(native))
    (directory / "kustomization.yaml").write_text(
        yaml.safe_dump(
            {
                "resources": ["cron.yaml"],
                "patches": auxiliary_post_renderers(compiled)[0]["kustomize"]["patches"],
            }
        )
    )
    result = subprocess.run(
        ["kubectl", "kustomize", str(directory)], capture_output=True, text=True, check=True
    )
    bound = yaml.safe_load(result.stdout)
    assert auxiliary_storage_matches(bound["spec"], policy.auxiliary_spec)
    pod = bound["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    pod["containers"][0]["volumeMounts"].pop()
    assert not auxiliary_storage_matches(bound["spec"], policy.auxiliary_spec)


@pytest.mark.parametrize(
    "override",
    [
        {
            "create-user-nebius": {
                "k8sJobSpec": {
                    "jobContainer": {
                        "volumeMounts": [
                            {"name": "jail", "mountPath": "/mnt/jail"},
                        ]
                    }
                }
            }
        },
        {
            "gpu-fryer": {
                "slurmJobSpec": {
                    "jobContainer": {
                        "extraVolumeMounts": [
                            {
                                "name": "jail",
                                "mountPath": "/mnt/jail/opt/soperator-home",
                                "readOnly": True,
                            },
                        ]
                    }
                }
            }
        },
    ],
)
def test_check_specific_overrides_cannot_bypass_retained_storage(frozen_charts, override):
    from nebius_cxcli.soperator_checks_policy import compile_checks_policy

    snapshot, _, source = frozen_charts
    values = _values()
    values["soperator-activechecks"] = {"checks": override}
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    with pytest.raises(ValueError, match="retained|writable jail mount"):
        compile_checks_policy(source, compiled)


@pytest.mark.parametrize(
    "check,kind", [("create-user-nebius", "k8sJobSpec"), ("gpu-fryer", "slurmJobSpec")]
)
@pytest.mark.parametrize(
    "path",
    [
        "/mnt/jail/opt//soperator-home",
        "/mnt/jail/opt/./soperator-home",
        "/unrelated/../mnt/jail/opt/soperator-home",
        "//mnt/jail/opt/soperator-home",
        "/mnt/jail/opt/soperator-home/",
        "/",
    ],
)
def test_check_mount_aliases_cannot_shadow_retained_home(frozen_charts, check, kind, path):
    from nebius_cxcli.soperator_checks_policy import compile_checks_policy

    snapshot, _, source = frozen_charts
    values = _values()
    container = {"extraVolumeMounts": [{"name": "shadow", "mountPath": path}]}
    job = {"jobContainer": container}
    (job if kind == "k8sJobSpec" else container)["extraVolumes"] = [
        {"name": "shadow", "emptyDir": {}}
    ]
    values["soperator-activechecks"] = {"checks": {check: {kind: job}}}
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    with pytest.raises(ValueError, match="canonical|retained"):
        compile_checks_policy(source, compiled)
