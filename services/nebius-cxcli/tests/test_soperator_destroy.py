from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_cxcli.soperator_destroy import (
    build_soperator_destroy_receipt,
    delete_onboarded_soperator_cluster,
    expected_soperator_destroy_confirmation,
    format_soperator_destroy_inventory,
    load_soperator_destroy_receipt,
    run_soperator_destroy,
    validate_soperator_destroy_terraform_plan,
    verify_soperator_filesystems_exist,
    verify_soperator_vm_nfs_exists,
    write_soperator_destroy_receipt,
)
from nebius_cxcli.soperator_infrastructure_identity import VmNfsProtectedStorageIdentity
from soperator_fixtures import sample_infrastructure_receipt


def _receipt():
    infrastructure = sample_infrastructure_receipt()
    return build_soperator_destroy_receipt(
        target_ref="cluster-a",
        ownership="managed",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        kubernetes_uid="kube-system-uid",
        destroy_inventory=("mk8s:mk8scluster-a", "namespace:soperator"),
        preserve_inventory=("sfs:filesystem-jail",),
        protected_storage_sha256=infrastructure.receipt_sha256,
        infrastructure_receipt=infrastructure.as_payload(),
        config_sha256="sha256:" + "b" * 64,
        post_cleanup_config_sha256="sha256:" + "c" * 64,
    )


def test_dry_run_is_owner_only_and_performs_no_mutation(tmp_path: Path) -> None:
    path = tmp_path / "destroy.json"
    write_soperator_destroy_receipt(path, _receipt())
    calls: list[str] = []

    result = run_soperator_destroy(
        receipt_path=path,
        dry_run=True,
        interactive=False,
        confirmation=None,
        verify_storage_before_cleanup=lambda: calls.append("precheck"),
        cleanup_cluster=lambda: calls.append("cleanup"),
        request_cluster_delete=lambda: calls.append("delete") or "operation-a",
        cluster_is_absent=lambda _operation: calls.append("poll") or True,
        verify_preserved_storage=lambda: calls.append("storage"),
        commit_config_cleanup=lambda: calls.append("config"),
    )

    assert result.status == "planned"
    assert calls == []
    assert path.stat().st_mode & 0o777 == 0o600
    assert format_soperator_destroy_inventory(result) == (
        "DESTROY:",
        "  - mk8s:mk8scluster-a",
        "  - namespace:soperator",
        "PRESERVE:",
        "  - sfs:filesystem-jail",
    )


@pytest.mark.parametrize(
    ("interactive", "confirmation", "message"),
    [
        (False, None, "interactive TTY"),
        (True, "yes", "exactly match"),
    ],
)
def test_execution_requires_tty_and_exact_cluster_phrase(
    tmp_path: Path,
    interactive: bool,
    confirmation: str | None,
    message: str,
) -> None:
    path = tmp_path / "destroy.json"
    write_soperator_destroy_receipt(path, _receipt())

    with pytest.raises(RuntimeError, match=message):
        run_soperator_destroy(
            receipt_path=path,
            dry_run=False,
            interactive=interactive,
            confirmation=confirmation,
            verify_storage_before_cleanup=lambda: None,
            cleanup_cluster=lambda: None,
            request_cluster_delete=lambda: "operation-a",
            cluster_is_absent=lambda _operation: True,
            verify_preserved_storage=lambda: None,
            commit_config_cleanup=lambda: None,
        )


def test_destroy_orders_frontiers_and_resume_never_repeats_delete(tmp_path: Path) -> None:
    path = tmp_path / "destroy.json"
    write_soperator_destroy_receipt(path, _receipt())
    calls: list[str] = []

    with pytest.raises(RuntimeError, match="still in progress"):
        run_soperator_destroy(
            receipt_path=path,
            dry_run=False,
            interactive=True,
            confirmation=expected_soperator_destroy_confirmation("mk8scluster-a"),
            verify_storage_before_cleanup=lambda: calls.append("precheck"),
            cleanup_cluster=lambda: calls.append("cleanup"),
            request_cluster_delete=lambda: calls.append("delete") or "operation-a",
            cluster_is_absent=lambda _operation: calls.append("poll") or False,
            verify_preserved_storage=lambda: calls.append("storage"),
            commit_config_cleanup=lambda: calls.append("config"),
        )
    assert calls == ["precheck", "cleanup", "delete", "poll"]

    calls.clear()
    result = run_soperator_destroy(
        receipt_path=path,
        dry_run=False,
        interactive=False,
        confirmation=None,
        verify_storage_before_cleanup=lambda: calls.append("precheck"),
        cleanup_cluster=lambda: calls.append("cleanup"),
        request_cluster_delete=lambda: calls.append("delete") or "different-operation",
        cluster_is_absent=lambda operation: calls.append(f"poll:{operation}") or True,
        verify_preserved_storage=lambda: calls.append("storage"),
        commit_config_cleanup=lambda: calls.append("config"),
    )

    assert calls == ["poll:operation-a", "storage", "config"]
    assert result.status == "complete"


def test_destroy_failure_receipt_persists_classification_not_exception_text(
    tmp_path: Path,
) -> None:
    path = tmp_path / "destroy.json"
    write_soperator_destroy_receipt(
        path,
        replace(
            _receipt(),
            checkpoints=("approved", "storage_verified_before_cleanup"),
            status="running",
        ),
    )

    with pytest.raises(RuntimeError, match="must-not-persist"):
        run_soperator_destroy(
            receipt_path=path,
            dry_run=False,
            interactive=False,
            confirmation=None,
            verify_storage_before_cleanup=lambda: None,
            cleanup_cluster=lambda: (_ for _ in ()).throw(
                RuntimeError("credential=must-not-persist")
            ),
            request_cluster_delete=lambda: "operation-a",
            cluster_is_absent=lambda _operation: True,
            verify_preserved_storage=lambda: None,
            commit_config_cleanup=lambda: None,
        )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["failure_classification"] == "runtime-error"
    assert "must-not-persist" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "failed_step",
    ["precheck", "cleanup", "delete", "poll", "storage", "config"],
)
def test_destroy_resumes_from_every_mutation_frontier(
    tmp_path: Path,
    failed_step: str,
) -> None:
    path = tmp_path / "destroy.json"
    write_soperator_destroy_receipt(path, _receipt())
    calls: list[str] = []
    failed = False

    def _step(name: str, result=None):
        def _run(*_args):
            nonlocal failed
            calls.append(name)
            if name == failed_step and not failed:
                failed = True
                raise RuntimeError(f"{name} failed")
            return result

        return _run

    callbacks = {
        "verify_storage_before_cleanup": _step("precheck"),
        "cleanup_cluster": _step("cleanup"),
        "request_cluster_delete": _step("delete", "operation-a"),
        "cluster_is_absent": _step("poll", True),
        "verify_preserved_storage": _step("storage"),
        "commit_config_cleanup": _step("config"),
    }
    with pytest.raises(RuntimeError, match=f"{failed_step} failed"):
        run_soperator_destroy(
            receipt_path=path,
            dry_run=False,
            interactive=True,
            confirmation=expected_soperator_destroy_confirmation("mk8scluster-a"),
            **callbacks,
        )

    result = run_soperator_destroy(
        receipt_path=path,
        dry_run=False,
        interactive=False,
        confirmation=None,
        **callbacks,
    )

    ordered_steps = ["precheck", "cleanup", "delete", "poll", "storage", "config"]
    failed_index = ordered_steps.index(failed_step)
    assert result.status == "complete"
    for index, name in enumerate(ordered_steps):
        assert calls.count(name) == (2 if index == failed_index else 1)


def test_receipt_tampering_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "destroy.json"
    write_soperator_destroy_receipt(path, _receipt())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cluster_id"] = "different"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(RuntimeError, match="modified"):
        load_soperator_destroy_receipt(path)


def test_targeted_terraform_plan_rejects_sfs_replacement_and_foreign_changes() -> None:
    accepted = {
        "resource_changes": [
            {
                "address": "module.cluster_a.nebius_mk8s_v1_cluster.this",
                "change": {
                    "actions": ["delete"],
                    "before": {"id": "mk8scluster-a"},
                },
            }
        ]
    }
    assert validate_soperator_destroy_terraform_plan(
        accepted,
        allowed_module_names=("cluster_a",),
        expected_cluster_id="mk8scluster-a",
    ) == ("module.cluster_a.nebius_mk8s_v1_cluster.this",)

    for address, actions in (
        ("module.sfs.nebius_compute_v1_filesystem.this", ["delete"]),
        ("module.cluster_a.nebius_mk8s_v1_cluster.this", ["delete", "create"]),
        ("module.other.nebius_mk8s_v1_cluster.this", ["delete"]),
        ("module.cluster_a.nebius_mk8s_v1_node_group.worker", ["delete"]),
    ):
        with pytest.raises(RuntimeError):
            validate_soperator_destroy_terraform_plan(
                {"resource_changes": [{"address": address, "change": {"actions": actions}}]},
                allowed_module_names=("cluster_a",),
                expected_cluster_id="mk8scluster-a",
            )


@pytest.mark.parametrize(
    "cluster_ids",
    (("different-cluster",), ("mk8scluster-a", "different-cluster")),
)
def test_targeted_terraform_plan_requires_one_exact_cluster_id(
    cluster_ids: tuple[str, ...],
) -> None:
    changes = [
        {
            "address": f"module.cluster_a.nebius_mk8s_v1_cluster.cluster_{index}",
            "change": {"actions": ["delete"], "before": {"id": cluster_id}},
        }
        for index, cluster_id in enumerate(cluster_ids)
    ]

    with pytest.raises(RuntimeError, match="exactly one approved MK8s cluster"):
        validate_soperator_destroy_terraform_plan(
            {"resource_changes": changes},
            allowed_module_names=("cluster_a",),
            expected_cluster_id="mk8scluster-a",
        )


def test_onboarded_delete_uses_exact_cluster_id_and_plain_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class _Sdk:
        def sync_close(self) -> None:
            calls["closed"] = True

    class _Request:
        def __init__(self, *, id: str) -> None:
            self.id = id

    class _OperationRequest:
        def wait(self):
            return SimpleNamespace(id="operation-a")

    class _Client:
        def __init__(self, sdk) -> None:
            calls["sdk"] = sdk

        def delete(self, request, **kwargs):
            calls["cluster_id"] = request.id
            calls["idempotency_key"] = kwargs.get("idempotency_key")
            return _OperationRequest()

    monkeypatch.setattr("nebius_cxcli.soperator_destroy.init_nebius_sdk", lambda **_kwargs: _Sdk())
    monkeypatch.setattr(
        "nebius_cxcli.soperator_destroy.nebius_operation_id", lambda _op: "operation-a"
    )
    monkeypatch.setattr(
        "nebius_cxcli.soperator_destroy.wait_nebius_operation", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr("nebius.api.nebius.mk8s.v1.ClusterServiceClient", _Client)
    monkeypatch.setattr("nebius.api.nebius.mk8s.v1.DeleteClusterRequest", _Request)

    operation_id = delete_onboarded_soperator_cluster(
        project_id="project-a",
        cluster_id="mk8scluster-a",
        idempotency_key="a" * 64,
    )

    assert operation_id == "operation-a"
    assert isinstance(calls["sdk"], _Sdk)
    assert calls["cluster_id"] == "mk8scluster-a"
    assert calls["idempotency_key"] == "a" * 64
    assert calls["closed"] is True


@pytest.mark.parametrize("forbid_deletion", [False, True])
def test_sfs_survival_verification_is_provider_flag_neutral(
    monkeypatch: pytest.MonkeyPatch,
    forbid_deletion: bool,
) -> None:
    observed: list[str] = []

    class _Sdk:
        def sync_close(self) -> None:
            observed.append("closed")

    class _Request:
        def __init__(self, *, id: str) -> None:
            self.id = id

    class _Pending:
        def __init__(self, filesystem_id: str) -> None:
            self.filesystem_id = filesystem_id

        def wait(self):
            observed.append(self.filesystem_id)
            return SimpleNamespace(
                metadata=SimpleNamespace(id=self.filesystem_id),
                spec=SimpleNamespace(forbid_deletion=forbid_deletion),
            )

    class _Client:
        def __init__(self, _sdk) -> None:
            pass

        def get(self, request):
            return _Pending(request.id)

    monkeypatch.setattr("nebius_cxcli.soperator_destroy.init_nebius_sdk", lambda **_kwargs: _Sdk())
    monkeypatch.setattr("nebius.api.nebius.compute.v1.FilesystemServiceClient", _Client)
    monkeypatch.setattr("nebius.api.nebius.compute.v1.GetFilesystemRequest", _Request)

    verify_soperator_filesystems_exist(
        project_id="project-a",
        filesystem_ids=("filesystem-b", "filesystem-a"),
    )

    assert observed == ["filesystem-a", "filesystem-b", "closed"]


def test_sfs_survival_verification_rejects_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Sdk:
        def sync_close(self) -> None:
            return None

    class _Request:
        def __init__(self, *, id: str) -> None:
            self.id = id

    class _Pending:
        def wait(self):
            return SimpleNamespace(metadata=SimpleNamespace(id="filesystem-other"))

    class _Client:
        def __init__(self, _sdk) -> None:
            pass

        def get(self, _request):
            return _Pending()

    monkeypatch.setattr("nebius_cxcli.soperator_destroy.init_nebius_sdk", lambda **_kwargs: _Sdk())
    monkeypatch.setattr("nebius.api.nebius.compute.v1.FilesystemServiceClient", _Client)
    monkeypatch.setattr("nebius.api.nebius.compute.v1.GetFilesystemRequest", _Request)

    with pytest.raises(RuntimeError, match="identity mismatch"):
        verify_soperator_filesystems_exist(
            project_id="project-a",
            filesystem_ids=("filesystem-a",),
        )


def test_vm_nfs_survival_verification_reads_every_frozen_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, str]] = []

    class _Sdk:
        def sync_close(self) -> None:
            observed.append(("sdk", "closed"))

    class _Request:
        def __init__(self, *, id: str) -> None:
            self.id = id

    class _Pending:
        def __init__(self, kind: str, identity: str) -> None:
            self.kind = kind
            self.identity = identity

        def wait(self):
            observed.append((self.kind, self.identity))
            if self.kind == "instance":
                secondary_disks = [
                    SimpleNamespace(
                        existing_disk=SimpleNamespace(id=disk_id),
                        managed_disk=SimpleNamespace(name=""),
                        device_id=f"data-{suffix}",
                        attach_mode="READ_WRITE",
                    )
                    for disk_id, suffix in (("disk-a", "a"), ("disk-b", "b"))
                ]
                return SimpleNamespace(
                    metadata=SimpleNamespace(id=self.identity),
                    spec=SimpleNamespace(secondary_disks=secondary_disks),
                    status=SimpleNamespace(
                        network_interfaces=[
                            SimpleNamespace(ip_address=SimpleNamespace(address="10.0.0.8"))
                        ],
                        disk_attachments=[
                            SimpleNamespace(id="disk-a", name="", is_managed=False),
                            SimpleNamespace(id="disk-b", name="", is_managed=False),
                        ],
                    ),
                )
            return SimpleNamespace(metadata=SimpleNamespace(id=self.identity))

    def _client(kind: str):
        class _Client:
            def __init__(self, _sdk) -> None:
                pass

            def get(self, request):
                return _Pending(kind, request.id)

        return _Client

    monkeypatch.setattr("nebius_cxcli.soperator_destroy.init_nebius_sdk", lambda **_kwargs: _Sdk())
    monkeypatch.setattr("nebius.api.nebius.compute.v1.GetInstanceRequest", _Request)
    monkeypatch.setattr("nebius.api.nebius.compute.v1.GetDiskRequest", _Request)
    monkeypatch.setattr("nebius.api.nebius.vpc.v1.GetAllocationRequest", _Request)
    monkeypatch.setattr(
        "nebius.api.nebius.compute.v1.InstanceServiceClient",
        _client("instance"),
    )
    monkeypatch.setattr(
        "nebius.api.nebius.compute.v1.DiskServiceClient",
        _client("disk"),
    )
    monkeypatch.setattr(
        "nebius.api.nebius.vpc.v1.AllocationServiceClient",
        _client("allocation"),
    )

    attachments = [
        {
            "id": disk_id,
            "deviceId": f"data-{suffix}",
            "attachMode": "READ_WRITE",
            "managed": "false",
        }
        for disk_id, suffix in (("disk-a", "a"), ("disk-b", "b"))
    ]

    def digest(value: object) -> str:
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )

    identity = VmNfsProtectedStorageIdentity(
        instance_id="instance-a",
        server_address="10.0.0.8",
        private_ips=("10.0.0.8",),
        data_disk_ids=("disk-a", "disk-b"),
        attachment_sha256=digest(attachments),
        allocation_ids=("allocation-a",),
        export_path="/export/home",
        export_sha256=digest({"server": "10.0.0.8", "path": "/export/home"}),
    )

    verify_soperator_vm_nfs_exists(
        project_id="project-a",
        identity=identity,
        export_probe=lambda server, path: observed.append(("export", f"{server}:{path}")),
    )

    assert observed == [
        ("instance", "instance-a"),
        ("disk", "disk-a"),
        ("disk", "disk-b"),
        ("allocation", "allocation-a"),
        ("export", "10.0.0.8:/export/home"),
        ("sdk", "closed"),
    ]
