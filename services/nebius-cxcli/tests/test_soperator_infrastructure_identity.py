from __future__ import annotations

from dataclasses import replace
from enum import Enum
from types import SimpleNamespace

import pytest

import nebius_cxcli.soperator_infrastructure_identity as infrastructure_identity
from nebius_cxcli.soperator_infrastructure_identity import (
    _authoritative_sfs_observations,
    build_soperator_infrastructure_receipt,
    discover_soperator_infrastructure_receipt,
    infrastructure_discovery_inputs_from_receipt,
    sfs_filesystem_observations_from_node_groups,
    soperator_infrastructure_receipt_from_payload,
    verify_soperator_infrastructure_identity,
)


def _instance(*, instance_id: str = "nfs-vm", address: str = "10.0.0.8"):
    data_disk = SimpleNamespace(
        existing_disk=SimpleNamespace(id="nfs-data-disk"),
        managed_disk=SimpleNamespace(name=""),
        device_id="data",
        attach_mode="READ_WRITE",
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(id=instance_id),
        spec=SimpleNamespace(secondary_disks=[data_disk]),
        status=SimpleNamespace(
            network_interfaces=[SimpleNamespace(ip_address=SimpleNamespace(address=address))],
            disk_attachments=[SimpleNamespace(id="nfs-data-disk", name="data", is_managed=False)],
        ),
    )


def _allocation(*, allocation_id: str = "allocation-login", cidr: str = "203.0.113.9/32"):
    return SimpleNamespace(
        metadata=SimpleNamespace(id=allocation_id),
        status=SimpleNamespace(details=SimpleNamespace(allocated_cidr=cidr)),
    )


def _login_observation():
    return {
        "status": "available",
        "services": [
            {
                "name": "soperator-login-svc",
                "uid": "service-uid",
                "type": "LoadBalancer",
                "clusterIP": "10.96.0.20",
                "clusterIPs": ["10.96.0.20"],
                "ingress": ["203.0.113.9"],
                "ports": [{"name": "ssh", "port": 22, "nodePort": 30222}],
                "selector": {"app": "login"},
                "annotations": {"service.beta.kubernetes.io/example": "value"},
            }
        ],
    }


def _receipt(*, instances=None, allocations=None):
    return build_soperator_infrastructure_receipt(
        project_id="project-a",
        nebius_cluster_id="mk8s-a",
        kubernetes_uid="kube-system-uid",
        storage_kind="vm-nfs",
        nfs_server="nfs.internal",
        nfs_export={"server": "nfs.internal", "path": "/export/home"},
        resolved_nfs_addresses=("10.0.0.8",),
        login_observation=_login_observation(),
        instances=instances or [_instance()],
        allocations=allocations or [_allocation()],
    )


def test_receipt_binds_nfs_vm_data_disk_and_login_allocation() -> None:
    receipt = _receipt()

    assert receipt.storage.kind == "vm-nfs"
    assert receipt.storage.vm_nfs is not None
    assert receipt.storage.vm_nfs.instance_id == "nfs-vm"
    assert receipt.storage.vm_nfs.data_disk_ids == ("nfs-data-disk",)
    assert receipt.login.service_uid == "service-uid"
    assert receipt.login.allocation_ids == ("allocation-login",)
    assert receipt.receipt_sha256.startswith("sha256:")
    assert "service.beta.kubernetes.io/example" not in str(receipt.as_payload())


def test_receipt_projects_exact_vm_nfs_repair_selectors() -> None:
    receipt = _receipt()

    assert infrastructure_discovery_inputs_from_receipt(receipt) == {
        "storage_kind": "vm-nfs",
        "nfs_server": "nfs.internal",
        "nfs_allocation_ids": (),
        "nfs_export": {"server": "nfs.internal", "path": "/export/home"},
    }


def test_ambiguous_nfs_instance_or_login_allocation_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="expected exactly one"):
        _receipt(instances=[_instance(instance_id="a"), _instance(instance_id="b")])
    with pytest.raises(RuntimeError, match="expected one allocation"):
        _receipt(
            allocations=[
                _allocation(allocation_id="a"),
                _allocation(allocation_id="b"),
            ]
        )


def test_missing_nfs_data_disk_is_rejected() -> None:
    instance = _instance()
    instance.spec.secondary_disks = []

    with pytest.raises(RuntimeError, match="no secondary data disk"):
        _receipt(instances=[instance])


def test_nfs_drift_is_strict_and_login_drift_is_advisory() -> None:
    before = _receipt()
    changed_nfs = replace(
        before,
        storage=replace(
            before.storage,
            vm_nfs=replace(
                before.storage.vm_nfs,
                instance_id="replacement-nfs-vm",
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="infrastructure identity drifted"):
        verify_soperator_infrastructure_identity(before=before, after=changed_nfs)

    changed_login = replace(
        before,
        login=replace(
            before.login,
            ingress_addresses=("203.0.113.10",),
            assignment_sha256="sha256:" + "a" * 64,
        ),
    )
    result = verify_soperator_infrastructure_identity(before=before, after=changed_login)

    assert result["status"] == "verified"
    assert result["advisories"][0]["code"] == "login-identity-drift"


def test_sfs_receipt_is_canonical_and_tamper_evident() -> None:
    receipt = build_soperator_infrastructure_receipt(
        project_id="project-a",
        nebius_cluster_id="mk8s-a",
        kubernetes_uid="kube-system-uid",
        storage_kind="sfs",
        sfs_filesystems=(
            {
                "role": "jail",
                "filesystem_id": "filesystem-jail",
                "mount_tag": "cluster-a-jail",
                "node_group_ids": ["worker", "controller"],
                "pv_names": ["pv-jail"],
                "pvc_names": ["pvc-jail"],
            },
        ),
        login_observation=_login_observation(),
        allocations=[_allocation()],
    )

    assert receipt.storage.sfs is not None
    assert receipt.storage.sfs.filesystems[0].node_group_ids == ("controller", "worker")
    round_trip = soperator_infrastructure_receipt_from_payload(receipt.as_payload())
    assert round_trip == receipt

    tampered = receipt.as_payload()
    tampered["project_id"] = "different"
    with pytest.raises(RuntimeError, match="modified"):
        soperator_infrastructure_receipt_from_payload(tampered)

    historical = receipt.as_payload()
    historical["schema"] = "nebius-cxcli.soperator-infrastructure-identity.v1"
    with pytest.raises(ValueError, match="schema is unsupported"):
        soperator_infrastructure_receipt_from_payload(historical)


def test_sfs_identity_allows_added_bindings_but_requires_original_bindings() -> None:
    before = build_soperator_infrastructure_receipt(
        project_id="project-a",
        nebius_cluster_id="mk8s-a",
        kubernetes_uid="kube-system-uid",
        storage_kind="sfs",
        sfs_filesystems=(
            {
                "role": "jail",
                "filesystem_id": "filesystem-jail",
                "mount_tag": "cluster-a-jail",
                "node_group_ids": ["controller", "worker"],
                "pv_names": ["jail-pv"],
                "pvc_names": ["jail-pvc"],
            },
        ),
        login_observation=_login_observation(),
        allocations=[_allocation()],
    )
    assert before.storage.sfs is not None
    expanded_filesystem = replace(
        before.storage.sfs.filesystems[0],
        pv_names=("jail-pv", "jail-rootfs-slot-a-pv"),
        pvc_names=("jail-pvc", "jail-rootfs-slot-a-pvc"),
    )
    after = replace(
        before,
        storage=replace(
            before.storage,
            sfs=replace(before.storage.sfs, filesystems=(expanded_filesystem,)),
        ),
    )

    result = verify_soperator_infrastructure_identity(before=before, after=after)

    assert result["bindingAdditions"] == [
        {
            "role": "jail",
            "pvNames": ["jail-rootfs-slot-a-pv"],
            "pvcNames": ["jail-rootfs-slot-a-pvc"],
        }
    ]
    removed = replace(
        after,
        storage=replace(
            after.storage,
            sfs=replace(
                after.storage.sfs,
                filesystems=(
                    replace(
                        expanded_filesystem,
                        pv_names=("jail-rootfs-slot-a-pv",),
                        pvc_names=("jail-rootfs-slot-a-pvc",),
                    ),
                ),
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="infrastructure identity drifted"):
        verify_soperator_infrastructure_identity(before=before, after=removed)


def test_sfs_receipt_requires_exactly_one_storage_variant() -> None:
    with pytest.raises(ValueError, match="no filesystems"):
        build_soperator_infrastructure_receipt(
            project_id="project-a",
            nebius_cluster_id="mk8s-a",
            kubernetes_uid="kube-system-uid",
            storage_kind="sfs",
            sfs_filesystems=(),
            login_observation=_login_observation(),
            allocations=[_allocation()],
        )


@pytest.mark.parametrize("forbid_deletion", [False, True])
def test_sfs_authoritative_identity_is_provider_flag_neutral(
    forbid_deletion: bool,
) -> None:
    observed_ids: list[str] = []

    def _get(filesystem_id: str):
        observed_ids.append(filesystem_id)
        return SimpleNamespace(
            metadata=SimpleNamespace(id=filesystem_id),
            spec=SimpleNamespace(forbid_deletion=forbid_deletion),
        )

    result = _authoritative_sfs_observations(
        (
            {
                "role": "jail",
                "filesystem_id": "filesystem-jail",
                "mount_tag": "jail",
            },
        ),
        get_filesystem=_get,
    )

    assert observed_ids == ["filesystem-jail"]
    assert result == (
        {
            "role": "jail",
            "filesystem_id": "filesystem-jail",
            "mount_tag": "jail",
        },
    )


def test_sfs_provider_flag_does_not_change_protected_storage_digest() -> None:
    def _receipt(forbid_deletion: bool):
        return build_soperator_infrastructure_receipt(
            project_id="project-a",
            nebius_cluster_id="mk8s-a",
            kubernetes_uid="kube-system-uid",
            storage_kind="sfs",
            sfs_filesystems=(
                {
                    "role": "jail",
                    "filesystem_id": "filesystem-jail",
                    "mount_tag": "jail",
                    "node_group_ids": ["worker"],
                    "pv_names": ["pv-jail"],
                    "pvc_names": ["pvc-jail"],
                    "forbid_deletion": forbid_deletion,
                },
            ),
            login_observation=_login_observation(),
            allocations=[_allocation()],
        )

    assert _receipt(False).receipt_sha256 == _receipt(True).receipt_sha256


def test_sfs_identity_comes_from_exact_node_group_attachments() -> None:
    def _node_group(node_group_id: str, *attachments: tuple[str, str]):
        return SimpleNamespace(
            metadata=SimpleNamespace(id=node_group_id),
            spec=SimpleNamespace(
                template=SimpleNamespace(
                    filesystems=[
                        SimpleNamespace(
                            mount_tag=role,
                            attach_mode="READ_WRITE",
                            existing_filesystem=SimpleNamespace(id=filesystem_id),
                        )
                        for role, filesystem_id in attachments
                    ]
                )
            ),
        )

    observations = sfs_filesystem_observations_from_node_groups(
        (
            _node_group(
                "group-controller",
                ("jail", "filesystem-jail"),
                ("controller-spool", "filesystem-controller"),
                ("accounting", "filesystem-accounting"),
            ),
            _node_group("group-worker", ("jail", "filesystem-jail")),
        ),
        kubernetes_bindings={
            "jail": {"pv_names": ["jail-pv"], "pvc_names": ["jail-pvc"]},
            "controller-spool": {
                "pv_names": ["controller-spool-pv"],
                "pvc_names": ["controller-spool-pvc"],
            },
        },
    )

    by_role = {str(item["role"]): item for item in observations}
    assert by_role["jail"]["filesystem_id"] == "filesystem-jail"
    assert by_role["jail"]["node_group_ids"] == ["group-controller", "group-worker"]
    assert by_role["jail"]["pv_names"] == ["jail-pv"]
    assert by_role["accounting"]["pvc_names"] == []


def test_sfs_identity_rejects_multiple_filesystems_for_one_role() -> None:
    node_groups = (
        SimpleNamespace(
            metadata=SimpleNamespace(id="group-a"),
            spec=SimpleNamespace(
                template=SimpleNamespace(
                    filesystems=[
                        SimpleNamespace(
                            mount_tag="jail",
                            attach_mode="READ_WRITE",
                            existing_filesystem=SimpleNamespace(id="filesystem-a"),
                        )
                    ]
                )
            ),
        ),
        SimpleNamespace(
            metadata=SimpleNamespace(id="group-b"),
            spec=SimpleNamespace(
                template=SimpleNamespace(
                    filesystems=[
                        SimpleNamespace(
                            mount_tag="jail",
                            attach_mode="READ_WRITE",
                            existing_filesystem=SimpleNamespace(id="filesystem-b"),
                        )
                    ]
                )
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="multiple filesystem IDs"):
        sfs_filesystem_observations_from_node_groups(node_groups)


def test_sfs_identity_accepts_typed_nebius_read_write_mode() -> None:
    class AttachMode(Enum):
        READ_WRITE = 2

    node_group = SimpleNamespace(
        metadata=SimpleNamespace(id="group-a"),
        spec=SimpleNamespace(
            template=SimpleNamespace(
                filesystems=[
                    SimpleNamespace(
                        mount_tag=role,
                        attach_mode=AttachMode.READ_WRITE,
                        existing_filesystem=SimpleNamespace(id=f"filesystem-{role}"),
                    )
                    for role in ("accounting", "controller-spool", "jail")
                ]
            )
        ),
    )

    observations = sfs_filesystem_observations_from_node_groups((node_group,))

    assert {item["role"] for item in observations} == {
        "accounting",
        "controller-spool",
        "jail",
    }


def test_discovery_authoritatively_reads_node_group_filesystems(monkeypatch) -> None:
    from nebius.api.nebius.compute import v1 as compute_v1
    from nebius.api.nebius.mk8s import v1 as mk8s_v1
    from nebius.api.nebius.vpc import v1 as vpc_v1

    filesystem_ids = {
        role: f"computefilesystem-{role}" for role in ("accounting", "controller-spool", "jail")
    }
    node_group = SimpleNamespace(
        metadata=SimpleNamespace(id="mk8snodegroup-controller"),
        spec=SimpleNamespace(
            template=SimpleNamespace(
                filesystems=[
                    SimpleNamespace(
                        mount_tag=role,
                        attach_mode="READ_WRITE",
                        existing_filesystem=SimpleNamespace(id=filesystem_id),
                    )
                    for role, filesystem_id in filesystem_ids.items()
                ]
            )
        ),
    )
    observed_filesystems: list[str] = []

    class _Sdk:
        def sync_close(self) -> None:
            return None

    class _Operation:
        def __init__(self, value):
            self.value = value

        def wait(self):
            return self.value

    class _NodeGroupClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            assert request.id == "mk8snodegroup-controller"
            return _Operation(node_group)

    class _FilesystemClient:
        def __init__(self, _sdk):
            pass

        def get(self, request):
            observed_filesystems.append(request.id)
            return _Operation(
                SimpleNamespace(
                    metadata=SimpleNamespace(id=request.id),
                    spec=SimpleNamespace(forbid_deletion=False),
                )
            )

    class _AllocationClient:
        def __init__(self, _sdk):
            pass

        def list(self, _request):
            return _Operation(SimpleNamespace(items=[], next_page_token=""))

    monkeypatch.setattr(
        infrastructure_identity,
        "init_nebius_sdk",
        lambda **_kwargs: _Sdk(),
    )
    monkeypatch.setattr(mk8s_v1, "NodeGroupServiceClient", _NodeGroupClient)
    monkeypatch.setattr(compute_v1, "FilesystemServiceClient", _FilesystemClient)
    monkeypatch.setattr(vpc_v1, "AllocationServiceClient", _AllocationClient)

    receipt = discover_soperator_infrastructure_receipt(
        project_id="project-a",
        nebius_cluster_id="mk8s-a",
        kubernetes_uid="kube-system-uid",
        storage_kind="sfs",
        login_observation={"status": "available", "services": []},
        sfs_node_group_ids=("mk8snodegroup-controller",),
    )

    assert observed_filesystems == sorted(filesystem_ids.values())
    assert receipt.storage.sfs is not None
    assert {item.filesystem_id for item in receipt.storage.sfs.filesystems} == set(
        filesystem_ids.values()
    )
