from __future__ import annotations

import json
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from typer.testing import CliRunner

import nebius_cxcli.cli as cli

runner = CliRunner()


def _command_result(args: list[str], stdout: str = "", returncode: int = 0) -> Any:
    return cli._SoperatorUpgradeCommandResult(
        args=tuple(args),
        returncode=returncode,
        stdout=stdout,
        stderr="" if returncode == 0 else "failed",
    )


def _kubernetes_list(items: list[dict[str, Any]]) -> str:
    return json.dumps({"apiVersion": "v1", "kind": "List", "items": items})


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
            "components": [
                {"id": "mk8s", "instance_id": "mk8s", "enabled": True, "inputs": {}}
            ]
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
                    "values": {
                        "slurmNodes": {
                            "accounting": {"externalDB": {"enabled": False}}
                        }
                    },
                }
            ]
        },
    }


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
                "secrets": [
                    {
                        "apiVersion": "v1",
                        "kind": "Secret",
                        "metadata": {"name": "slurm-secret", "resourceVersion": "1"},
                        "data": {"password": "c2VjcmV0"},
                    }
                ],
                "configmaps": [
                    {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": "slurm-conf", "uid": "uid"},
                        "data": {"slurm.conf": "ClusterName=test\n"},
                    }
                ],
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
                "slurmclusters": [
                    {
                        "apiVersion": "soperator.nebius.ai/v1",
                        "kind": "SlurmCluster",
                        "metadata": {"name": "slurm"},
                        "spec": {"clusterName": "slurm"},
                    }
                ],
            }
            return _command_result(args, _kubernetes_list(items_by_resource.get(resource, [])))
        raise AssertionError(f"unexpected kubectl args: {args}")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", fake_kubectl)
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
    with tarfile.open(backup.path, "r:gz") as archive:
        names = set(archive.getnames())
        assert "backup-manifest.json" in names
        assert "restore-plan.json" in names
        assert "checksums.json" in names
        assert "kubernetes/secrets.json" in names
        assert "kubernetes/restore/secrets.yaml" in names
        assert "kubernetes/restore/deployments.yaml" in names
        assert "kubernetes/restore/services.yaml" in names
        assert "accounting/slurm-accounting-db.sql" in names
        manifest = json.loads(archive.extractfile("backup-manifest.json").read())
        restore_plan = json.loads(archive.extractfile("restore-plan.json").read())
        service_restore = archive.extractfile("kubernetes/restore/services.yaml").read().decode()
        deployment_restore = (
            archive.extractfile("kubernetes/restore/deployments.yaml").read().decode()
        )

    assert manifest["schema"] == "nebius-cxcli-soperator-backup/v1"
    assert manifest["source_kind"] == "managed-backup"
    assert manifest["kubernetes_restore_material"]["resource_counts"]["deployments"] == 1
    assert "kubernetes/restore/deployments.yaml" in restore_plan["apply_order"]
    assert "clusterIP" not in service_restore
    assert "nodePort" not in service_restore
    assert "status" not in deployment_restore


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


def _write_restore_archive(
    tmp_path: Path,
    *,
    corrupt_checksum: bool = False,
    apply_order: list[str] | None = None,
    accounting_dump: str = "accounting/slurm-accounting-db.sql",
    extra_files: dict[str, str] | None = None,
    omit_checksums: set[str] | None = None,
) -> Path:
    root = tmp_path / "restore-root"
    (root / "kubernetes" / "restore").mkdir(parents=True)
    (root / "accounting").mkdir()
    apply_paths = apply_order or ["kubernetes/restore/configmaps.yaml"]
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
                "accounting_db": {"dump": accounting_dump},
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
        call[2][:2] == ("exec", "soperator-acct-db-0")
        and call[3] == "CREATE DATABASE slurm;\n"
        for call in calls
    )


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
