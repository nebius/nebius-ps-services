from __future__ import annotations

import json
import os
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from typer.testing import CliRunner

import nebius_cxcli.cli as cli

runner = CliRunner()


def _command_result(
    args: list[str],
    stdout: str = "",
    returncode: int = 0,
    stderr: str | None = None,
) -> Any:
    return cli._SoperatorUpgradeCommandResult(
        args=tuple(args),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr if stderr is not None else ("" if returncode == 0 else "failed"),
    )


def _kubernetes_list(items: list[dict[str, Any]]) -> str:
    return json.dumps({"apiVersion": "v1", "kind": "List", "items": items})


def _assert_archive_has_unique_members(archive: tarfile.TarFile) -> None:
    names = archive.getnames()
    assert len(names) == len(set(names))


def _source_payload() -> dict[str, Any]:
    return {
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-123",
                "project_id": "project-456",
                "region_id": "eu-north1",
            },
        },
        "infra": {
            "components": [{"id": "mk8s", "instance_id": "mk8s", "enabled": True, "inputs": {}}]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "mk8s",
                    "enabled": True,
                    "version": "0.26.0",
                    "namespace": "soperator",
                    "release-name": "soperator",
                    "values": {"slurmNodes": {"accounting": {"externalDB": {"enabled": False}}}},
                }
            ]
        },
    }


def _required_recreation_items() -> dict[str, list[dict[str, Any]]]:
    return {
        "secrets": [
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": name, "resourceVersion": "1"},
                "data": {"password": "c2VjcmV0"},
            }
            for name in (
                *cli._SOPERATOR_BACKUP_REQUIRED_RECREATION_SECRETS,
                *cli._SOPERATOR_BACKUP_OPTIONAL_RECREATION_SECRETS,
            )
        ],
        "configmaps": [
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": name, "uid": "uid"},
                "data": {"slurm.conf": "ClusterName=test\n"},
            }
            for name in cli._SOPERATOR_BACKUP_REQUIRED_RECREATION_CONFIGMAPS
        ],
        "persistentvolumeclaims": [
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {
                    "name": "controller-spool-controller-0",
                    "uid": "pvc-uid",
                    "annotations": {
                        "pv.kubernetes.io/bind-completed": "yes",
                        "example.nebius.ai/keep": "true",
                    },
                },
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "10Gi"}},
                    "storageClassName": "retain",
                    "volumeName": "pv-controller-spool",
                },
                "status": {"phase": "Bound"},
            },
            {
                "apiVersion": "v1",
                "kind": "PersistentVolumeClaim",
                "metadata": {"name": "storage-soperator-acct-db-0", "uid": "pvc-uid"},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "20Gi"}},
                    "storageClassName": "retain",
                    "volumeName": "pv-accounting",
                },
                "status": {"phase": "Bound"},
            },
        ],
        "slurmclusters": [
            {
                "apiVersion": "slurm.nebius.ai/v1",
                "kind": "SlurmCluster",
                "metadata": {"name": "soperator"},
                "spec": {"clusterName": "soperator"},
            }
        ],
    }


def _required_recreation_pvs() -> list[dict[str, Any]]:
    return [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {
                "name": "pv-controller-spool",
                "uid": "pv-uid",
                "annotations": {
                    "pv.kubernetes.io/provisioned-by": "example.csi",
                    "example.nebius.ai/keep": "true",
                },
            },
            "spec": {
                "capacity": {"storage": "10Gi"},
                "accessModes": ["ReadWriteOnce"],
                "persistentVolumeReclaimPolicy": "Retain",
                "storageClassName": "retain",
                "claimRef": {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "name": "controller-spool-controller-0",
                    "namespace": "soperator",
                    "uid": "claim-uid",
                    "resourceVersion": "2",
                },
                "csi": {"driver": "example.csi", "volumeHandle": "controller-spool"},
            },
            "status": {"phase": "Bound"},
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {"name": "pv-accounting", "uid": "pv-uid"},
            "spec": {
                "capacity": {"storage": "20Gi"},
                "accessModes": ["ReadWriteOnce"],
                "persistentVolumeReclaimPolicy": "Retain",
                "storageClassName": "retain",
                "claimRef": {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "name": "storage-soperator-acct-db-0",
                    "namespace": "soperator",
                    "uid": "claim-uid",
                    "resourceVersion": "3",
                },
                "csi": {"driver": "example.csi", "volumeHandle": "accounting"},
            },
            "status": {"phase": "Bound"},
        },
    ]


def _chart_named_required_recreation_items() -> dict[str, list[dict[str, Any]]]:
    required_items = _required_recreation_items()
    slurmcluster_name = "mk8s"
    required_items["slurmclusters"][0]["metadata"]["name"] = slurmcluster_name
    required_items["slurmclusters"][0]["spec"] = {
        "clusterName": slurmcluster_name,
        "volumeSources": [
            {
                "name": "controller-spool",
                "persistentVolumeClaim": {"claimName": "controller-spool-pvc"},
            },
            {
                "name": "slurm-scripts",
                "configMap": {"name": "mk8s-slurm-scripts"},
            },
        ],
    }
    required_items["persistentvolumeclaims"] = [
        {
            **required_items["persistentvolumeclaims"][0],
            "metadata": {"name": "controller-spool-pvc", "uid": "pvc-uid"},
            "spec": {
                **required_items["persistentvolumeclaims"][0]["spec"],
                "volumeName": "pv-controller-spool",
            },
        },
        {
            **required_items["persistentvolumeclaims"][1],
            "metadata": {
                "name": "storage-mk8s-acct-db-0",
                "uid": "pvc-uid",
                "labels": {"pvc.k8s.mariadb.com/role": "storage"},
            },
            "spec": {
                **required_items["persistentvolumeclaims"][1]["spec"],
                "volumeName": "pv-accounting",
            },
        },
    ]
    required_items["secrets"] = [
        {
            **item,
            "metadata": {
                **item["metadata"],
                "name": {
                    "soperator-sshd-keys": f"{slurmcluster_name}-sshd-keys",
                    "soperator-slurmdbd-configs": f"{slurmcluster_name}-slurmdbd-configs",
                }.get(item["metadata"]["name"], item["metadata"]["name"]),
            },
        }
        for item in required_items["secrets"]
    ]
    required_items["configmaps"] = [
        {
            **item,
            "metadata": {
                **item["metadata"],
                "name": {
                    "soperator-slurm-configs": f"{slurmcluster_name}-slurm-configs",
                    "slurm-scripts": f"{slurmcluster_name}-slurm-scripts",
                }.get(item["metadata"]["name"], item["metadata"]["name"]),
            },
        }
        for item in required_items["configmaps"]
    ]
    return required_items


def _chart_named_required_recreation_pvs() -> list[dict[str, Any]]:
    pvs = _required_recreation_pvs()
    pvs[0]["spec"]["claimRef"]["name"] = "controller-spool-pvc"
    pvs[1]["spec"]["claimRef"]["name"] = "storage-mk8s-acct-db-0"
    return pvs


def test_soperator_backup_filename_uses_external_prefix_without_upgrade_transitions() -> None:
    filename = cli._soperator_upgrade_backup_filename(
        chart_from="1.22.3",
        chart_to="1.22.3",
        k8s_from=None,
        k8s_to=None,
        generated_at=datetime(2026, 6, 29, 20, 34, 59, tzinfo=UTC),
        prefix="external-soperator-backup",
        include_transitions=False,
    )

    assert filename == "external-soperator-backup-20260629T203459Z-chart-1.22.3.tar.gz"


def test_ext_soperator_upgrade_backup_filename_includes_source_transitions() -> None:
    filename = cli._soperator_upgrade_backup_filename(
        chart_from="1.22.3",
        chart_to="4.0.2-ps.3",
        k8s_from="1.31",
        k8s_to="1.32",
        generated_at=datetime(2026, 7, 2, 22, 53, 35, tzinfo=UTC),
        prefix="ext-soperator-upgrade",
    )

    assert (
        filename == "ext-soperator-upgrade-20260702T225335Z-chart-1.22.3-to-4.0.2-ps.3-"
        "k8s-1.31-to-1.32.tar.gz"
    )
    assert "unknown" not in filename


def test_create_external_soperator_upgrade_backup_uses_accepted_transition_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    payload = _source_payload()
    payload["apps"]["charts"][0]["version"] = "4.0.2-ps.3"
    payload["deploy"] = {
        "targets": [
            {
                "instance_id": "mk8s",
                "soperator_onboarding": {
                    "source_version": "1.22.3",
                    "target_version": "4.0.2-ps.3",
                    "node_template_upgrade": {"target_k8s_version": "1.32"},
                },
            }
        ]
    }
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_backup(**kwargs: Any) -> cli._SoperatorUpgradeBackupResult:
        captured.update(kwargs)
        return cli._SoperatorUpgradeBackupResult(
            path=tmp_path / "backups" / "ext-soperator-upgrade.tar.gz",
            size_bytes=1,
            sha256="archive-sha",
            included_categories=("accounting", "kubernetes"),
            secret_material_included=True,
            accounting_db_included=True,
            manifest_sha256="manifest-sha",
        )

    monkeypatch.setattr(cli, "_create_restore_capable_soperator_upgrade_backup", fake_backup)

    metadata = cli._create_external_soperator_upgrade_backup(
        config_path=config_path,
        source_payload=payload,
        target_ref="mk8s",
        backup_dir=tmp_path / "backups",
        kube_context="ctx",
        command=["nebius-cxcli", "ext-soperator", "upgrade", str(config_path)],
        source_chart_version="1.22.3",
        target_chart_version="4.0.2-ps.3",
        source_k8s_version="1.31",
        target_k8s_version="1.32",
    )

    assert metadata["path"].endswith("ext-soperator-upgrade.tar.gz")
    assert captured["plan"].current_version == "1.22.3"
    assert captured["plan"].target_version == "4.0.2-ps.3"
    assert captured["source_k8s_version"] == "1.31"
    assert captured["target_k8s_version"] == "1.32"


def test_soperator_login_command_falls_back_to_controller_for_config_source_failure(
    monkeypatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        assert namespace == "soperator"
        command = tuple(args)
        calls.append(command)
        if command[:3] == ("exec", "login-0", "--"):
            return _command_result(
                list(command),
                returncode=1,
                stderr=(
                    "squeue: error: resolve_ctls_from_dns_srv: res_nsearch error: Unknown host\n"
                    "squeue: fatal: Could not establish a configuration source"
                ),
            )
        if command[:6] == ("exec", "controller-0", "-c", "slurmctld", "--", "bash"):
            return _command_result(list(command), "123|user|RUNNING\n")
        raise AssertionError(f"unexpected kubectl args: {args}")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    result = cli._run_soperator_upgrade_login_command(
        "soperator",
        "squeue -h",
        kube_context="ctx",
    )

    assert result.returncode == 0
    assert result.stdout == "123|user|RUNNING\n"
    assert calls == [
        ("exec", "login-0", "--", "bash", "-lc", "squeue -h"),
        ("exec", "controller-0", "-c", "slurmctld", "--", "bash", "-lc", "squeue -h"),
    ]


def test_soperator_backup_archive_contains_restore_material(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(_source_payload()), encoding="utf-8")
    target = cli._parse_soperator_upgrade_target("mk8s")
    plan = cli._plan_helm_chart_upgrade(
        payload=_source_payload(),
        target=target,
        target_version="0.26.0",
    )

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if namespace == "flux-system":
            return _command_result(
                args,
                returncode=1,
                stderr='Error from server (NotFound): configmaps "soperator-fluxcd" not found',
            )
        assert namespace == "soperator"
        if args[:2] == ["get", "deployment/accounting"]:
            return _command_result(
                args,
                json.dumps({"spec": {"replicas": 1}, "metadata": {"name": "accounting"}}),
            )
        if args[:1] == ["scale"]:
            return _command_result(args)
        if args[:2] == ["exec", "soperator-acct-db-0"]:
            command = args[-1]
            if "mariadb-dump" in command or "mysqldump" in command:
                return _command_result(args, "CREATE DATABASE slurm_acct_db;\n")
            return _command_result(args, "slurm_acct_db|12\n")
        if args[:1] == ["get"]:
            resource = args[1]
            items_by_resource: dict[str, list[dict[str, Any]]] = {
                **_required_recreation_items(),
                "services": [
                    {
                        "apiVersion": "v1",
                        "kind": "Service",
                        "metadata": {"name": "login"},
                        "spec": {
                            "clusterIP": "10.0.0.1",
                            "ports": [{"port": 6817, "nodePort": 30001}],
                        },
                    }
                ],
                "deployments.apps": [
                    {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "metadata": {"name": "slurmctld", "generation": 2},
                        "spec": {"selector": {"matchLabels": {"app": "slurmctld"}}},
                        "status": {"readyReplicas": 1},
                    }
                ],
                "pods": [
                    {
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {"name": "soperator-acct-db-0"},
                        "status": {"phase": "Running"},
                    }
                ],
            }
            return _command_result(args, _kubernetes_list(items_by_resource.get(resource, [])))
        raise AssertionError(f"unexpected kubectl args: {args}")

    def fake_kubectl_cluster(
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if args[:2] == ["get", "persistentvolumes"]:
            return _command_result(args, _kubernetes_list(_required_recreation_pvs()))
        raise AssertionError(f"unexpected cluster kubectl args: {args}")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)
    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", fake_kubectl_cluster)
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_process",
        lambda args, **_kwargs: _command_result(list(args), '{"values": true}\n'),
    )
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_login_command",
        lambda namespace, command, **_kwargs: _command_result(
            ["login", command],
            "snapshot\n",
        ),
    )

    backup = cli._create_restore_capable_soperator_upgrade_backup(
        config_path=config_path,
        backup_dir=tmp_path / "backups",
        source_payload=_source_payload(),
        generated_config=SimpleNamespace(client_info={"client_name": "client-a"}),
        manifest={"schema": "manifest"},
        target=target,
        plan=plan,
        checkpoint_id="checkpoint-1",
        target_k8s_version=None,
        command=["nebius-cxcli", "soperator", "backup", str(config_path)],
        archive_prefix="soperator-backup",
        source_kind="managed-backup",
    )

    assert os.stat(backup.path.parent).st_mode & 0o777 == 0o700
    assert os.stat(backup.path).st_mode & 0o777 == 0o600
    assert backup.path.parent == tmp_path / "backups" / "soperator-clusters" / "mk8s"
    assert "-chart-0.26.0.tar.gz" in backup.path.name
    assert "-to-" not in backup.path.name
    assert "unknown" not in backup.path.name
    with tarfile.open(backup.path, "r:gz") as archive:
        _assert_archive_has_unique_members(archive)
        names = set(archive.getnames())
        assert "backup-manifest.json" in names
        assert "restore-plan.json" in names
        assert "checksums.json" in names
        assert "kubernetes/secrets.json" in names
        assert "kubernetes/restore/secrets.yaml" in names
        assert "kubernetes/restore/deployments.yaml" in names
        assert "kubernetes/restore/services.yaml" in names
        assert "recreation/recreation-coverage.json" in names
        assert "recreation/flux-system-configmaps.json" in names
        assert "slurm/sacctmgr-dump.cfg" in names
        assert "slurm/running-job-ids.txt" in names
        assert "slurm/pending-held-job-ids.txt" in names
        assert "accounting/slurm-accounting-db.sql" in names
        manifest = json.loads(archive.extractfile("backup-manifest.json").read())
        restore_plan = json.loads(archive.extractfile("restore-plan.json").read())
        coverage = json.loads(archive.extractfile("recreation/recreation-coverage.json").read())
        service_restore = archive.extractfile("kubernetes/restore/services.yaml").read().decode()
        deployment_restore = (
            archive.extractfile("kubernetes/restore/deployments.yaml").read().decode()
        )
        generic_pvc_restore = (
            archive.extractfile("kubernetes/restore/persistentvolumeclaims.yaml").read().decode()
        )
        required_pv_restore = yaml.safe_load_all(
            archive.extractfile("recreation/restore/persistentvolumes.yaml").read().decode()
        )
        required_pvc_restore = yaml.safe_load_all(
            archive.extractfile("recreation/restore/persistentvolumeclaims.yaml").read().decode()
        )
        required_pvs = list(required_pv_restore)
        required_pvcs = list(required_pvc_restore)

    assert manifest["schema"] == "nebius-cxcli-soperator-backup/v1"
    assert manifest["source_kind"] == "managed-backup"
    assert manifest["cluster_key"] == "mk8s"
    assert manifest["cluster_id"] == ""
    assert manifest["cluster_name"] == ""
    assert manifest["target_ref"] == "mk8s"
    assert restore_plan["cluster_key"] == "mk8s"
    assert restore_plan["target_ref"] == "mk8s"
    assert manifest["kubernetes_restore_material"]["resource_counts"]["deployments"] == 1
    assert manifest["recreation_runbook_material"]["coverage_path"] == (
        "recreation/recreation-coverage.json"
    )
    assert (
        manifest["recreation_runbook_material"]["retained_pv_bindings_are_command_restored"] is True
    )
    assert (
        "recreation/restore/persistentvolumes.yaml"
        in manifest["recreation_runbook_material"]["restore_paths"]
    )
    assert "recreation/restore/persistentvolumes.yaml" in names
    assert "recreation/restore/persistentvolumeclaims.yaml" in names
    assert restore_plan["cluster_apply_order"] == ["recreation/restore/persistentvolumes.yaml"]
    assert restore_plan["apply_order"][:4] == [
        "recreation/restore/persistentvolumes.yaml",
        "kubernetes/restore/secrets.yaml",
        "kubernetes/restore/configmaps.yaml",
        "recreation/restore/persistentvolumeclaims.yaml",
    ]
    assert "kubernetes/restore/deployments.yaml" in restore_plan["apply_order"]
    assert restore_plan["recreation_runbook"]["retained_pv_bindings_are_command_restored"] is True
    assert (
        "recreation/recreation-coverage.json" in restore_plan["restore_material"]["recreation_raw"]
    )
    assert coverage["items"]["bound_persistentvolumes"]["status"] == "collected"
    assert coverage["items"]["required_recreation_material"]["status"] == "collected"
    assert "clusterIP" not in service_restore
    assert "nodePort" not in service_restore
    assert "status" not in deployment_restore
    assert "controller-spool-controller-0" not in generic_pvc_restore
    assert required_pvs[0]["kind"] == "PersistentVolume"
    assert "namespace" not in required_pvs[0]["metadata"]
    assert required_pvs[0]["metadata"]["annotations"] == {"example.nebius.ai/keep": "true"}
    assert required_pvs[0]["spec"]["claimRef"]["name"] == "controller-spool-controller-0"
    assert "uid" not in required_pvs[0]["spec"]["claimRef"]
    assert "resourceVersion" not in required_pvs[0]["spec"]["claimRef"]
    assert required_pvcs[0]["kind"] == "PersistentVolumeClaim"
    assert required_pvcs[0]["metadata"]["namespace"] == "soperator"
    assert required_pvcs[0]["metadata"]["annotations"] == {"example.nebius.ai/keep": "true"}
    assert required_pvcs[0]["spec"]["volumeName"] == "pv-controller-spool"


def test_soperator_backup_archive_deduplicates_chart_named_recreation_pvcs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(_source_payload()), encoding="utf-8")
    target = cli._parse_soperator_upgrade_target("mk8s")
    plan = cli._plan_helm_chart_upgrade(
        payload=_source_payload(),
        target=target,
        target_version="0.26.0",
    )
    required_items = _chart_named_required_recreation_items()

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if namespace == "flux-system":
            return _command_result(
                args,
                returncode=1,
                stderr='Error from server (NotFound): configmaps "soperator-fluxcd" not found',
            )
        assert namespace == "soperator"
        if args[:2] == ["get", "deployment/accounting"]:
            return _command_result(
                args,
                json.dumps({"spec": {"replicas": 1}, "metadata": {"name": "accounting"}}),
            )
        if args[:1] == ["scale"]:
            return _command_result(args)
        if args[:2] == ["exec", "soperator-acct-db-0"]:
            command = args[-1]
            if "mariadb-dump" in command or "mysqldump" in command:
                return _command_result(args, "CREATE DATABASE slurm_acct_db;\n")
            return _command_result(args, "slurm_acct_db|12\n")
        if args[:1] == ["get"]:
            resource = args[1]
            items_by_resource: dict[str, list[dict[str, Any]]] = {
                **required_items,
                "services": [],
                "deployments.apps": [],
                "pods": [
                    {
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {"name": "soperator-acct-db-0"},
                        "status": {"phase": "Running"},
                    }
                ],
            }
            return _command_result(args, _kubernetes_list(items_by_resource.get(resource, [])))
        raise AssertionError(f"unexpected kubectl args: {args}")

    def fake_kubectl_cluster(
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if args[:2] == ["get", "persistentvolumes"]:
            return _command_result(args, _kubernetes_list(_chart_named_required_recreation_pvs()))
        raise AssertionError(f"unexpected cluster kubectl args: {args}")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)
    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", fake_kubectl_cluster)
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_process",
        lambda args, **_kwargs: _command_result(list(args), '{"values": true}\n'),
    )
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_login_command",
        lambda namespace, command, **_kwargs: _command_result(
            ["login", command],
            "snapshot\n",
        ),
    )

    backup = cli._create_restore_capable_soperator_upgrade_backup(
        config_path=config_path,
        backup_dir=tmp_path / "backups",
        source_payload=_source_payload(),
        generated_config=SimpleNamespace(client_info={"client_name": "client-a"}),
        manifest={"schema": "manifest"},
        target=target,
        plan=plan,
        checkpoint_id="checkpoint-1",
        target_k8s_version=None,
        command=["nebius-cxcli", "soperator", "backup", str(config_path)],
        archive_prefix="soperator-backup",
        source_kind="managed-backup",
    )

    with tarfile.open(backup.path, "r:gz") as archive:
        generic_pvc_restore = (
            archive.extractfile("kubernetes/restore/persistentvolumeclaims.yaml").read().decode()
        )
        required_pvcs = list(
            yaml.safe_load_all(
                archive.extractfile("recreation/restore/persistentvolumeclaims.yaml")
                .read()
                .decode()
            )
        )
        coverage = json.loads(archive.extractfile("recreation/recreation-coverage.json").read())

    assert "controller-spool-pvc" not in generic_pvc_restore
    assert "storage-mk8s-acct-db-0" not in generic_pvc_restore
    assert [item["metadata"]["name"] for item in required_pvcs] == [
        "controller-spool-pvc",
        "storage-mk8s-acct-db-0",
    ]
    assert coverage["items"]["required_recreation_material"]["retained_pvc_names"] == [
        "controller-spool-pvc",
        "storage-mk8s-acct-db-0",
    ]


def test_soperator_backup_accepts_slurmcluster_prefixed_recreation_material(
    tmp_path: Path,
    monkeypatch,
) -> None:
    kubernetes_dir = tmp_path / "kubernetes"
    kubernetes_dir.mkdir()
    required_items = _required_recreation_items()
    slurmcluster_name = "soperator-sop-cluster"
    required_items["slurmclusters"][0]["metadata"]["name"] = slurmcluster_name
    required_items["secrets"] = [
        {
            **item,
            "metadata": {
                **item["metadata"],
                "name": {
                    "soperator-sshd-keys": f"{slurmcluster_name}-sshd-keys",
                    "soperator-slurmdbd-configs": f"{slurmcluster_name}-slurmdbd-configs",
                }.get(item["metadata"]["name"], item["metadata"]["name"]),
            },
        }
        for item in required_items["secrets"]
    ]
    required_items["configmaps"] = [
        {
            **item,
            "metadata": {
                **item["metadata"],
                "name": {
                    "soperator-slurm-configs": f"{slurmcluster_name}-slurm-configs",
                }.get(item["metadata"]["name"], item["metadata"]["name"]),
            },
        }
        for item in required_items["configmaps"]
    ]
    for label, items in required_items.items():
        (kubernetes_dir / f"{label}.json").write_text(
            json.dumps({"items": items}),
            encoding="utf-8",
        )
    (kubernetes_dir / "services.json").write_text(json.dumps({"items": []}), encoding="utf-8")

    def fake_kubectl_cluster(
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if args[:2] == ["get", "persistentvolumes"]:
            return _command_result(args, _kubernetes_list(_required_recreation_pvs()))
        raise AssertionError(f"unexpected cluster kubectl args: {args}")

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if namespace == "flux-system":
            return _command_result(args, returncode=1, stderr="NotFound")
        assert namespace == "soperator"
        return _command_result(args, returncode=1, stderr="NotFound")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", fake_kubectl_cluster)
    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    _paths, coverage = cli._soperator_upgrade_collect_recreation_material(
        namespace="soperator",
        output_dir=tmp_path / "recreation",
        kubernetes_dir=kubernetes_dir,
    )

    assert coverage["items"]["required_recreation_material"]["status"] == "collected"


def test_soperator_backup_collects_chart_named_recreation_material(
    tmp_path: Path,
    monkeypatch,
) -> None:
    kubernetes_dir = tmp_path / "kubernetes"
    kubernetes_dir.mkdir()
    required_items = _chart_named_required_recreation_items()
    for label, items in required_items.items():
        (kubernetes_dir / f"{label}.json").write_text(
            json.dumps({"items": items}),
            encoding="utf-8",
        )
    (kubernetes_dir / "services.json").write_text(json.dumps({"items": []}), encoding="utf-8")

    def fake_kubectl_cluster(
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if args[:2] == ["get", "persistentvolumes"]:
            return _command_result(args, _kubernetes_list(_chart_named_required_recreation_pvs()))
        raise AssertionError(f"unexpected cluster kubectl args: {args}")

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if namespace == "flux-system":
            return _command_result(args, returncode=1, stderr="NotFound")
        assert namespace == "soperator"
        return _command_result(args, returncode=1, stderr="NotFound")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", fake_kubectl_cluster)
    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    _paths, coverage = cli._soperator_upgrade_collect_recreation_material(
        namespace="soperator",
        output_dir=tmp_path / "recreation",
        kubernetes_dir=kubernetes_dir,
    )

    assert coverage["items"]["required_recreation_material"]["status"] == "collected"
    assert coverage["items"]["required_recreation_material"]["retained_pvc_names"] == [
        "controller-spool-pvc",
        "storage-mk8s-acct-db-0",
    ]


def test_soperator_backup_fails_when_required_recreation_material_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    kubernetes_dir = tmp_path / "kubernetes"
    kubernetes_dir.mkdir()
    required_items = _required_recreation_items()
    required_items["secrets"] = [
        item for item in required_items["secrets"] if item["metadata"]["name"] != "mariadb-root"
    ]
    for label, items in required_items.items():
        (kubernetes_dir / f"{label}.json").write_text(
            json.dumps({"items": items}),
            encoding="utf-8",
        )
    (kubernetes_dir / "services.json").write_text(json.dumps({"items": []}), encoding="utf-8")

    def fake_kubectl_cluster(
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if args[:2] == ["get", "persistentvolumes"]:
            return _command_result(args, _kubernetes_list(_required_recreation_pvs()[:1]))
        raise AssertionError(f"unexpected cluster kubectl args: {args}")

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if namespace == "flux-system":
            return _command_result(args, returncode=1, stderr="NotFound")
        assert namespace == "soperator"
        return _command_result(args, returncode=1, stderr="NotFound")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", fake_kubectl_cluster)
    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    try:
        cli._soperator_upgrade_collect_recreation_material(
            namespace="soperator",
            output_dir=tmp_path / "recreation",
            kubernetes_dir=kubernetes_dir,
        )
    except RuntimeError as exc:
        assert "PersistentVolume/pv-accounting" in str(exc)
        assert "Secret/mariadb-root" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("backup accepted missing required recreation secret")


def test_soperator_backup_allows_missing_mariadb_metrics_generated_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    assert not set(cli._SOPERATOR_BACKUP_OPTIONAL_RECREATION_SECRETS).intersection(
        cli._SOPERATOR_BACKUP_REQUIRED_RECREATION_SECRETS
    )
    kubernetes_dir = tmp_path / "kubernetes"
    kubernetes_dir.mkdir()
    required_items = _required_recreation_items()
    optional_secret_names = set(cli._SOPERATOR_BACKUP_OPTIONAL_RECREATION_SECRETS)
    required_items["secrets"] = [
        item
        for item in required_items["secrets"]
        if item["metadata"]["name"] not in optional_secret_names
    ]
    for label, items in required_items.items():
        (kubernetes_dir / f"{label}.json").write_text(
            json.dumps({"items": items}),
            encoding="utf-8",
        )
    (kubernetes_dir / "services.json").write_text(json.dumps({"items": []}), encoding="utf-8")

    def fake_kubectl_cluster(
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if args[:2] == ["get", "persistentvolumes"]:
            return _command_result(args, _kubernetes_list(_required_recreation_pvs()))
        raise AssertionError(f"unexpected cluster kubectl args: {args}")

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if namespace == "flux-system":
            return _command_result(args, returncode=1, stderr="NotFound")
        assert namespace == "soperator"
        return _command_result(args, returncode=1, stderr="NotFound")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", fake_kubectl_cluster)
    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    included, coverage = cli._soperator_upgrade_collect_recreation_material(
        namespace="soperator",
        output_dir=tmp_path / "recreation",
        kubernetes_dir=kubernetes_dir,
    )

    assert "recreation/restore/persistentvolumes.yaml" in included
    assert coverage["items"]["required_recreation_material"]["status"] == "collected"


def test_soperator_backup_allows_missing_accounting_db(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(_source_payload()), encoding="utf-8")
    target = cli._parse_soperator_upgrade_target("mk8s")
    plan = cli._plan_helm_chart_upgrade(
        payload=_source_payload(),
        target=target,
        target_version="0.26.0",
    )

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if namespace == "flux-system":
            return _command_result(
                args,
                returncode=1,
                stderr='Error from server (NotFound): configmaps "soperator-fluxcd" not found',
            )
        assert namespace == "soperator"
        if args[:2] == ["get", "deployment/accounting"]:
            return _command_result(
                args,
                returncode=1,
                stderr='Error from server (NotFound): deployments.apps "accounting" not found',
            )
        if args[:1] == ["scale"]:
            raise AssertionError("accounting deployment should not be scaled when absent")
        if args[:1] == ["get"]:
            resource = args[1]
            items_by_resource: dict[str, list[dict[str, Any]]] = {
                **_required_recreation_items(),
                "services": [],
                "deployments.apps": [],
                "pods": [],
            }
            return _command_result(args, _kubernetes_list(items_by_resource.get(resource, [])))
        raise AssertionError(f"unexpected kubectl args: {args}")

    def fake_kubectl_cluster(
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if args[:2] == ["get", "persistentvolumes"]:
            return _command_result(args, _kubernetes_list(_required_recreation_pvs()))
        raise AssertionError(f"unexpected cluster kubectl args: {args}")

    def fake_login_command(
        namespace: str,
        command: str,
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        assert namespace == "soperator"
        if command.startswith("sacctmgr "):
            return _command_result(
                ["login", command],
                returncode=1,
                stderr=(
                    "You are not running a supported accounting_storage plugin\n"
                    "Only 'accounting_storage/slurmdbd' is supported."
                ),
            )
        return _command_result(["login", command], "snapshot\n")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)
    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", fake_kubectl_cluster)
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_process",
        lambda args, **_kwargs: _command_result(list(args), '{"values": true}\n'),
    )
    monkeypatch.setattr(cli, "_run_soperator_upgrade_login_command", fake_login_command)

    backup = cli._create_restore_capable_soperator_upgrade_backup(
        config_path=config_path,
        backup_dir=tmp_path / "backups",
        source_payload=_source_payload(),
        generated_config=SimpleNamespace(client_info={"client_name": "client-a"}),
        manifest={"schema": "manifest"},
        target=target,
        plan=plan,
        checkpoint_id="checkpoint-1",
        target_k8s_version=None,
        command=["nebius-cxcli", "soperator", "backup", str(config_path)],
        archive_prefix="soperator-backup",
        source_kind="managed-backup",
    )

    assert backup.accounting_db_included is False
    extract_dir = tmp_path / "extracted"
    cli._soperator_restore_extract_archive(backup.path, extract_dir)
    cli._soperator_restore_verify_archive(extract_dir)
    with tarfile.open(backup.path, "r:gz") as archive:
        _assert_archive_has_unique_members(archive)
        names = set(archive.getnames())
        assert "accounting/slurm-accounting-db.sql" not in names
        assert "accounting/slurm-accounting-db-verification.json" in names
        assert "slurm/sacctmgr-clusters.txt" in names
        manifest = json.loads(archive.extractfile("backup-manifest.json").read())
        restore_plan = json.loads(archive.extractfile("restore-plan.json").read())
        sacctmgr_clusters = archive.extractfile("slurm/sacctmgr-clusters.txt").read().decode()

    assert manifest["sensitive_material"]["accounting_db_dump"] is False
    assert manifest["accounting_db"]["status"] == "not_collected"
    assert restore_plan["restore_material"]["accounting_db_dump"] is None
    assert restore_plan["accounting_db"]["dump"] is None
    assert restore_plan["security"]["contains_accounting_db_dump"] is False
    assert "# not_collected" in sacctmgr_clusters
    assert "accounting_storage/slurmdbd" in sacctmgr_clusters


def test_soperator_backup_recreation_material_records_pv_reclaim_warnings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    kubernetes_dir = tmp_path / "kubernetes"
    kubernetes_dir.mkdir()
    required_items = _required_recreation_items()
    (kubernetes_dir / "persistentvolumeclaims.json").write_text(
        json.dumps(
            {
                "items": [
                    *required_items["persistentvolumeclaims"],
                    {
                        "metadata": {"name": "image-storage-worker-0"},
                        "spec": {"volumeName": "pv-worker-0"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (kubernetes_dir / "secrets.json").write_text(
        json.dumps({"items": required_items["secrets"]}),
        encoding="utf-8",
    )
    (kubernetes_dir / "configmaps.json").write_text(
        json.dumps({"items": required_items["configmaps"]}),
        encoding="utf-8",
    )
    (kubernetes_dir / "slurmclusters.json").write_text(
        json.dumps({"items": required_items["slurmclusters"]}),
        encoding="utf-8",
    )
    (kubernetes_dir / "services.json").write_text(
        json.dumps({"items": []}),
        encoding="utf-8",
    )

    def fake_kubectl_cluster(
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        assert args[:2] == ["get", "persistentvolumes"]
        return _command_result(
            args,
            json.dumps(
                {
                    "items": [
                        *_required_recreation_pvs(),
                        {
                            "metadata": {"name": "pv-worker-0"},
                            "spec": {
                                "persistentVolumeReclaimPolicy": "Delete",
                                "claimRef": {"name": "image-storage-worker-0"},
                            },
                        },
                    ]
                }
            ),
        )

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if namespace == "flux-system":
            return _command_result(args, returncode=1, stderr="NotFound")
        assert namespace == "soperator"
        return _command_result(args, returncode=1, stderr="NotFound")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", fake_kubectl_cluster)
    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    included, coverage = cli._soperator_upgrade_collect_recreation_material(
        namespace="soperator",
        output_dir=tmp_path / "recreation",
        kubernetes_dir=kubernetes_dir,
    )

    assert "recreation/bound-persistentvolume-reclaim-policies.json" in included
    assert coverage["items"]["bound_persistentvolumes"]["status"] == "collected"
    assert "Delete" in coverage["warnings"][0]
    worker = coverage["items"]["worker_local_pvc_pv_evidence"]
    assert worker["status"] == "collected"


def test_soperator_backup_uses_portable_wckey_snapshot_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands: list[str] = []

    def fake_login_command(
        namespace: str,
        command: str,
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        assert namespace == "soperator"
        commands.append(command)
        return _command_result(["login", command], "snapshot\n")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_login_command", fake_login_command)

    included = cli._soperator_upgrade_collect_slurm_snapshots(
        namespace="soperator",
        output_dir=tmp_path / "slurm",
    )
    discovery = cli._collect_soperator_discovery_accounting_snapshot(
        namespace="soperator",
        kube_context=None,
    )

    assert "slurm/sacctmgr-wckeys.txt" in included
    assert discovery["commands"]["sacctmgr_wckeys"]["returncode"] == 0
    assert "sacctmgr -nP show wckey format=Cluster,User,WCKey" in commands
    assert all(
        "show wckey format=Cluster,Account,User,WCKey" not in command for command in commands
    )


def test_soperator_backup_derives_sacctmgr_dump_cluster_from_slurm_conf(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands: list[str] = []

    def fake_login_command(
        namespace: str,
        command: str,
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        assert namespace == "soperator"
        commands.append(command)
        if command.startswith("cat ${SLURM_CONF"):
            return _command_result(["login", command], "ClusterName=prod-cluster\n")
        return _command_result(["login", command], "snapshot\n")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_login_command", fake_login_command)

    cli._soperator_upgrade_collect_slurm_snapshots(
        namespace="soperator",
        output_dir=tmp_path / "slurm",
    )

    assert "sacctmgr dump cluster=prod-cluster" in commands
    assert "sacctmgr dump cluster=soperator" not in commands


def test_soperator_backup_resolves_target_mariadb_pod(monkeypatch) -> None:
    def fake_kubectl(
        namespace: str,
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        assert namespace == "soperator"
        assert args == ["get", "pods", "-o", "json"]
        return _command_result(
            args,
            _kubernetes_list(
                [
                    {
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {"name": "other-acct-db-0"},
                        "status": {"phase": "Running"},
                    },
                    {
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {"name": "soperator-upgrade-live-acct-db-0"},
                        "status": {"phase": "Running"},
                    },
                ]
            ),
        )

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    assert (
        cli._soperator_upgrade_resolve_mariadb_pod(
            "soperator",
            target_ref="soperator-upgrade-live",
        )
        == "soperator-upgrade-live-acct-db-0"
    )


def test_soperator_backup_long_cluster_dump_is_replay_safe_and_uses_password_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    exec_commands: list[str] = []
    cluster_name = "soperator-long-customer-cluster-name-with-many-characters"
    mariadb_pod = f"{cluster_name}-acct-db-0"

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        assert namespace == "soperator"
        if args == ["get", "pods", "-o", "json"]:
            return _command_result(
                args,
                _kubernetes_list(
                    [
                        {
                            "apiVersion": "v1",
                            "kind": "Pod",
                            "metadata": {"name": mariadb_pod},
                            "status": {"phase": "Running"},
                        }
                    ]
                ),
            )
        if args[:2] == ["exec", mariadb_pod]:
            command = args[-1]
            exec_commands.append(command)
            if "mariadb-dump" in command or "mysqldump" in command:
                return _command_result(args, "CREATE DATABASE slurm_acct_db;\n")
            return _command_result(args, "slurm_acct_db|12\n")
        raise AssertionError(f"unexpected kubectl args: {args}")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    cli._soperator_upgrade_dump_accounting_db(
        namespace="soperator",
        output_dir=tmp_path,
        target_ref=cluster_name,
    )

    assert len(exec_commands) == 2
    assert all("MARIADB_ROOT_PASSWORD" in command for command in exec_commands)
    assert all('--defaults-extra-file="$defaults_file"' in command for command in exec_commands)
    assert all("--password" not in command for command in exec_commands)
    dump_command = next(
        command for command in exec_commands if "mariadb-dump" in command or "mysqldump" in command
    )
    assert "--skip-extended-insert" in dump_command
    assert "--ignore-table=slurm_acct_db.table_defs_table" in dump_command
    assert "--ignore-table=slurm_acct_db.convert_version_table" in dump_command
    assert "--all-databases" in dump_command


def test_standalone_soperator_backup_does_not_require_generated_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    payload = _source_payload()
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _config_path: (_ for _ in ()).throw(RuntimeError("render missing")),
    )

    def fake_backup(**kwargs: Any) -> cli._SoperatorUpgradeBackupResult:
        captured.update(kwargs)
        return cli._SoperatorUpgradeBackupResult(
            path=tmp_path / "backups" / "soperator-backup.tar.gz",
            size_bytes=1,
            sha256="archive-sha",
            included_categories=("accounting", "kubernetes"),
            secret_material_included=True,
            accounting_db_included=True,
            manifest_sha256="manifest-sha",
        )

    monkeypatch.setattr(cli, "_create_restore_capable_soperator_upgrade_backup", fake_backup)

    backup = cli._run_standalone_soperator_backup(
        config_path=config_path,
        source_payload=payload,
        target_ref="mk8s",
        backup_dir=tmp_path / "backups",
        namespace=None,
        release_name=None,
        kube_context="ctx",
        dry_run=False,
        interactive=False,
        source_kind="managed-backup",
        command_group="soperator",
    )

    assert backup is not None
    assert captured["manifest"]["status"] == "not_collected"
    assert captured["generated_config"].client_info == payload["client_info"]
    assert captured["archive_prefix"] == "soperator-backup"


def test_standalone_external_soperator_backup_uses_external_archive_prefix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    payload = _source_payload()
    config_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _config_path: (_ for _ in ()).throw(RuntimeError("render missing")),
    )

    def fake_backup(**kwargs: Any) -> cli._SoperatorUpgradeBackupResult:
        captured.update(kwargs)
        return cli._SoperatorUpgradeBackupResult(
            path=tmp_path / "backups" / "external-soperator-backup.tar.gz",
            size_bytes=1,
            sha256="archive-sha",
            included_categories=("accounting", "kubernetes"),
            secret_material_included=True,
            accounting_db_included=True,
            manifest_sha256="manifest-sha",
        )

    monkeypatch.setattr(cli, "_create_restore_capable_soperator_upgrade_backup", fake_backup)

    backup = cli._run_standalone_soperator_backup(
        config_path=config_path,
        source_payload=payload,
        target_ref="mk8s",
        backup_dir=tmp_path / "backups",
        namespace=None,
        release_name=None,
        kube_context="ctx",
        dry_run=False,
        interactive=False,
        source_kind="external-soperator-backup",
        command_group="ext-soperator",
    )

    assert backup is not None
    assert captured["archive_prefix"] == "external-soperator-backup"
    assert captured["source_kind"] == "external-soperator-backup"


def _write_restore_archive(
    tmp_path: Path,
    *,
    corrupt_checksum: bool = False,
    apply_order: list[str] | None = None,
    cluster_apply_order: list[str] | None = None,
    accounting_dump: str = "accounting/slurm-accounting-db.sql",
    extra_files: dict[str, str] | None = None,
    omit_checksums: set[str] | None = None,
) -> Path:
    root = tmp_path / "restore-root"
    (root / "kubernetes" / "restore").mkdir(parents=True)
    (root / "accounting").mkdir()
    (root / "recreation").mkdir()
    apply_paths = apply_order or ["kubernetes/restore/configmaps.yaml"]
    required_recreation_paths = [
        path for path in apply_paths if path.startswith("recreation/restore/")
    ]
    files = {
        "backup-manifest.json": json.dumps(
            {
                "schema": "nebius-cxcli-soperator-backup/v1",
                "target_ref": "mk8s",
                "source_kind": "managed-backup",
                "namespace": "soperator",
                "accounting_db": {"dump_bytes": 24},
            },
            sort_keys=True,
        )
        + "\n",
        "restore-plan.json": json.dumps(
            {
                "schema": "nebius-cxcli-soperator-restore-plan/v1",
                "target_ref": "mk8s",
                "namespace": "soperator",
                "apply_order": apply_paths,
                "cluster_apply_order": cluster_apply_order or [],
                "restore_material": {
                    "kubernetes_apply": apply_paths,
                    "kubernetes_cluster_apply": cluster_apply_order or [],
                    "recreation_coverage": "recreation/recreation-coverage.json",
                },
                "recreation_runbook": {
                    "coverage": "recreation/recreation-coverage.json",
                },
                "accounting_db": {"dump": accounting_dump},
            },
            sort_keys=True,
        )
        + "\n",
        "recreation/recreation-coverage.json": json.dumps(
            {
                "schema": "nebius-cxcli-soperator-recreation-coverage/v1",
                "items": {
                    "required_recreation_material": {
                        "status": "collected",
                        "paths": required_recreation_paths,
                    },
                },
            },
            sort_keys=True,
        )
        + "\n",
        "kubernetes/restore/configmaps.yaml": (
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: slurm-conf\n"
            "data:\n  slurm.conf: ClusterName=test\n"
        ),
        "accounting/slurm-accounting-db.sql": "CREATE DATABASE slurm;\n",
    }
    if extra_files:
        files.update(extra_files)
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    skipped = omit_checksums or set()
    checksums = {
        relative_path: cli._soperator_upgrade_file_sha256(root / relative_path)
        for relative_path in files
        if relative_path not in skipped
    }
    if corrupt_checksum:
        checksums["backup-manifest.json"] = "0" * 64
    (root / "checksums.json").write_text(json.dumps(checksums, sort_keys=True) + "\n")
    archive_path = tmp_path / "soperator-backup.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(root.rglob("*")):
            archive.add(path, arcname=path.relative_to(root))
    return archive_path


def test_soperator_restore_dry_run_validates_archive_without_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_path = _write_restore_archive(tmp_path)
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_kubectl_cluster",
        lambda args, **_kwargs: calls.append(("cluster", tuple(args))),
    )
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_kubectl",
        lambda namespace, args, **_kwargs: calls.append(("namespaced", tuple(args))),
    )

    cli._run_soperator_restore_archive(
        archive_path=archive_path,
        target_ref="mk8s",
        namespace=None,
        kube_context="ctx",
        dry_run=True,
        approve=False,
        restore_accounting_db=True,
    )

    assert calls == []


def test_soperator_restore_execute_applies_manifests_and_imports_db(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_path = _write_restore_archive(tmp_path)
    calls: list[tuple[str, str, tuple[str, ...], str | None]] = []

    def fake_cluster(args: list[str], **_kwargs: Any) -> cli._SoperatorUpgradeCommandResult:
        calls.append(("cluster", "", tuple(args), None))
        if args[:2] == ["get", "namespace"]:
            return _command_result(args, returncode=1)
        return _command_result(args)

    def fake_kubectl(
        namespace: str,
        args: list[str],
        input_text: str | None = None,
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        calls.append(("namespaced", namespace, tuple(args), input_text))
        if args == ["get", "pods", "-o", "json"]:
            return _command_result(
                args,
                _kubernetes_list(
                    [
                        {
                            "apiVersion": "v1",
                            "kind": "Pod",
                            "metadata": {"name": "mk8s-acct-db-0"},
                            "status": {"phase": "Running"},
                        }
                    ]
                ),
            )
        if args[:2] == ["get", "deployment/accounting"]:
            return _command_result(args, json.dumps({"spec": {"replicas": 1}}))
        return _command_result(args)

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", fake_cluster)
    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    cli._run_soperator_restore_archive(
        archive_path=archive_path,
        target_ref="mk8s",
        namespace="restored",
        kube_context="ctx",
        dry_run=False,
        approve=True,
        restore_accounting_db=True,
    )

    assert ("cluster", "", ("create", "namespace", "restored"), None) in calls
    assert any(call[2][:2] == ("apply", "-f") for call in calls)
    assert any(
        call[2][:2] == ("exec", "mk8s-acct-db-0")
        and "MARIADB_ROOT_PASSWORD" in call[2][-1]
        and call[3] == "CREATE DATABASE slurm;\n"
        for call in calls
    )


def test_soperator_restore_applies_cluster_pvs_before_prebound_pvcs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_path = _write_restore_archive(
        tmp_path,
        apply_order=[
            "recreation/restore/persistentvolumes.yaml",
            "recreation/restore/persistentvolumeclaims.yaml",
            "kubernetes/restore/configmaps.yaml",
        ],
        cluster_apply_order=["recreation/restore/persistentvolumes.yaml"],
        extra_files={
            "recreation/restore/persistentvolumes.yaml": (
                "apiVersion: v1\n"
                "kind: PersistentVolume\n"
                "metadata:\n"
                "  name: pv-controller-spool\n"
                "spec:\n"
                "  claimRef:\n"
                "    name: controller-spool-controller-0\n"
                "    namespace: soperator\n"
            ),
            "recreation/restore/persistentvolumeclaims.yaml": (
                "apiVersion: v1\n"
                "kind: PersistentVolumeClaim\n"
                "metadata:\n"
                "  name: controller-spool-controller-0\n"
                "  namespace: soperator\n"
                "spec:\n"
                "  volumeName: pv-controller-spool\n"
            ),
        },
    )
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_cluster(args: list[str], **_kwargs: Any) -> cli._SoperatorUpgradeCommandResult:
        calls.append(("cluster", tuple(args)))
        if args == ["get", "PersistentVolume/pv-controller-spool"]:
            return _command_result(args, returncode=1)
        if args[:2] == ["get", "namespace"]:
            return _command_result(args, returncode=1)
        if args[:2] == ["apply", "-f"]:
            docs = list(yaml.safe_load_all(Path(args[2]).read_text(encoding="utf-8")))
            assert docs[0]["kind"] == "PersistentVolume"
            assert "namespace" not in docs[0]["metadata"]
            assert docs[0]["spec"]["claimRef"]["namespace"] == "restored"
        return _command_result(args)

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        calls.append(("namespaced", tuple(args)))
        if args[:2] == ["apply", "-f"] and args[2].endswith("persistentvolumeclaims.yaml"):
            docs = list(yaml.safe_load_all(Path(args[2]).read_text(encoding="utf-8")))
            assert namespace == "restored"
            assert docs[0]["metadata"]["namespace"] == "restored"
            assert docs[0]["spec"]["volumeName"] == "pv-controller-spool"
        return _command_result(args)

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl_cluster", fake_cluster)
    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    cli._run_soperator_restore_archive(
        archive_path=archive_path,
        target_ref="mk8s",
        namespace="restored",
        kube_context="ctx",
        dry_run=False,
        approve=True,
        restore_accounting_db=False,
    )

    cluster_apply_index = next(
        index
        for index, call in enumerate(calls)
        if call[0] == "cluster" and call[1][:2] == ("apply", "-f")
    )
    pvc_apply_index = next(
        index
        for index, call in enumerate(calls)
        if call[0] == "namespaced"
        and call[1][:2] == ("apply", "-f")
        and call[1][2].endswith("persistentvolumeclaims.yaml")
    )
    assert cluster_apply_index < pvc_apply_index
    assert calls[0] == ("cluster", ("get", "PersistentVolume/pv-controller-spool"))


def test_soperator_restore_rejects_unchecked_restore_plan_member(tmp_path: Path) -> None:
    archive_path = _write_restore_archive(
        tmp_path,
        apply_order=["kubernetes/restore/evil.yaml"],
        extra_files={
            "kubernetes/restore/evil.yaml": (
                "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: evil\n"
            )
        },
        omit_checksums={"kubernetes/restore/evil.yaml"},
    )

    try:
        cli._run_soperator_restore_archive(
            archive_path=archive_path,
            target_ref="mk8s",
            namespace=None,
            kube_context=None,
            dry_run=True,
            approve=False,
            restore_accounting_db=True,
        )
    except RuntimeError as exc:
        assert "unchecked archive member" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("restore accepted an unchecked restore-plan member")


def test_soperator_restore_rejects_incomplete_recreation_coverage(tmp_path: Path) -> None:
    archive_path = _write_restore_archive(
        tmp_path,
        extra_files={
            "recreation/recreation-coverage.json": json.dumps(
                {
                    "schema": "nebius-cxcli-soperator-recreation-coverage/v1",
                    "items": {
                        "required_recreation_material": {
                            "status": "missing",
                            "detail": "PersistentVolume/pv-accounting",
                        },
                    },
                },
                sort_keys=True,
            )
            + "\n",
        },
    )

    try:
        cli._run_soperator_restore_archive(
            archive_path=archive_path,
            target_ref="mk8s",
            namespace=None,
            kube_context=None,
            dry_run=True,
            approve=False,
            restore_accounting_db=True,
        )
    except RuntimeError as exc:
        assert "required_recreation_material=missing" in str(exc)
        assert "PersistentVolume/pv-accounting" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("restore accepted incomplete recreation coverage")


def test_soperator_restore_rejects_escaping_restore_plan_member(tmp_path: Path) -> None:
    archive_path = _write_restore_archive(
        tmp_path,
        apply_order=["../evil.yaml"],
    )

    try:
        cli._run_soperator_restore_archive(
            archive_path=archive_path,
            target_ref="mk8s",
            namespace=None,
            kube_context=None,
            dry_run=True,
            approve=False,
            restore_accounting_db=True,
        )
    except RuntimeError as exc:
        assert "unsafe archive path" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("restore accepted an escaping restore-plan member")


def test_soperator_restore_rejects_unchecked_accounting_dump(tmp_path: Path) -> None:
    archive_path = _write_restore_archive(
        tmp_path,
        accounting_dump="accounting/evil.sql",
        extra_files={"accounting/evil.sql": "CREATE DATABASE evil;\n"},
        omit_checksums={"accounting/evil.sql"},
    )

    try:
        cli._run_soperator_restore_archive(
            archive_path=archive_path,
            target_ref="mk8s",
            namespace=None,
            kube_context=None,
            dry_run=True,
            approve=False,
            restore_accounting_db=True,
        )
    except RuntimeError as exc:
        assert "unchecked archive member" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("restore accepted an unchecked accounting dump")


def test_soperator_restore_rejects_existing_target_objects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_path = _write_restore_archive(tmp_path)

    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_kubectl_cluster",
        lambda args, **_kwargs: _command_result(list(args)),
    )

    def fake_kubectl(
        namespace: str,
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        if args[:2] == ["get", "ConfigMap/slurm-conf"]:
            return _command_result(args, "exists")
        raise AssertionError(f"unexpected mutation after conflict check: {args}")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    try:
        cli._run_soperator_restore_archive(
            archive_path=archive_path,
            target_ref="mk8s",
            namespace="soperator",
            kube_context="ctx",
            dry_run=False,
            approve=True,
            restore_accounting_db=True,
        )
    except RuntimeError as exc:
        assert "requires an empty compatible target namespace" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("restore applied over an existing target object")


def test_soperator_restore_rejects_checksum_mismatch(tmp_path: Path) -> None:
    archive_path = _write_restore_archive(tmp_path, corrupt_checksum=True)

    try:
        cli._run_soperator_restore_archive(
            archive_path=archive_path,
            target_ref="mk8s",
            namespace=None,
            kube_context=None,
            dry_run=True,
            approve=False,
            restore_accounting_db=True,
        )
    except RuntimeError as exc:
        assert "checksum mismatch" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("restore accepted a corrupted backup archive")


def test_accounting_db_dump_failure_redacts_partial_sql(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_kubectl(
        namespace: str,
        args: list[str],
        **_kwargs: Any,
    ) -> cli._SoperatorUpgradeCommandResult:
        return cli._SoperatorUpgradeCommandResult(
            args=tuple(args),
            returncode=1,
            stdout="CREATE TABLE should_not_print(id int);\n",
            stderr="",
        )

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)

    try:
        cli._soperator_upgrade_dump_accounting_db(
            namespace="soperator",
            output_dir=tmp_path,
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "SQL dump stdout was redacted" in message
        assert "CREATE TABLE" not in message
    else:  # pragma: no cover - assertion guard
        raise AssertionError("accounting dump failure did not raise")


def test_ext_soperator_restore_execute_requires_kube_context(tmp_path: Path) -> None:
    archive_path = _write_restore_archive(tmp_path)

    result = runner.invoke(
        cli.app,
        ["ext-soperator", "restore", str(archive_path), "--execute", "--approve"],
    )

    assert result.exit_code != 0
    assert "requires --kube-context" in result.output
