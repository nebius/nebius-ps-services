from __future__ import annotations

import pytest

from nebius_cxcli.soperator_jail_mounts import (
    apply_jail_persistent_mount_values,
    jail_persistent_mount_decisions,
    jail_persistent_mount_status,
    normalize_jail_persistent_mounts,
    parse_jail_persistent_mount_spec,
)


def test_apply_external_persistent_mount_values_adds_home_in_shared_area() -> None:
    values = apply_jail_persistent_mount_values(
        {"nodesets": [{"name": "worker"}]},
        target_ref="external-cluster",
        layout="external",
    )

    assert values["jailRootfs"]["store"]["mountPath"] == "/mnt/jail"
    assert values["jailRootfs"]["store"]["rootfsPath"] == "/mnt/jail/.cxcli/rootfs"
    assert values["jailRootfs"]["adoption"]["activeSource"] == "legacy-rootfs"
    assert values["jailPersistentMounts"] == [
        {"mountPath": "/home", "localPath": "/mnt/jail/shared/home"},
        {"mountPath": "/data", "localPath": "/mnt/jail/shared/data"},
        {"mountPath": "/scripts", "localPath": "/mnt/jail/shared/scripts"},
        {"mountPath": "/models", "localPath": "/mnt/jail/shared/models"},
    ]
    volume_sources = {item["name"]: item for item in values["volumeSources"]}
    assert set(volume_sources) == {
        "jail",
    }
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == (
        "jail-rootfs-slot-a-pvc"
    )
    assert "jail_home" not in values
    assert "home" not in values["jailRootfs"]


def test_external_persistent_mount_decisions_record_auto_explicit_and_existing_submounts() -> None:
    source_values = {
        "externalNfs": {
            "enabled": True,
            "mountPath": "/home",
            "server": "nfs.example.invalid",
            "path": "/exports/home",
        }
    }
    explicit_data = parse_jail_persistent_mount_spec("/data=/mnt/jail/shared/data")
    values = apply_jail_persistent_mount_values(
        source_values,
        target_ref="external-cluster",
        persistent_mounts=[explicit_data],
        layout="external",
    )

    decisions = jail_persistent_mount_decisions(
        original_values=source_values,
        patched_values=values,
        explicit_mounts=[explicit_data],
    )

    assert {item["mount_path"]: item["status"] for item in decisions} == {
        "/home": "existing-submount",
        "/data": "explicit",
        "/scripts": "pending-probe",
        "/models": "pending-probe",
    }
    assert {
        item["mount_path"]: item["copy_required"] for item in decisions
    } == {
        "/home": False,
        "/data": True,
        "/scripts": True,
        "/models": True,
    }


def test_apply_persistent_mount_values_removes_chart_rendered_volume_source_duplicates() -> None:
    values = apply_jail_persistent_mount_values(
        {
            "volumeSources": [
                {"name": "controller-spool", "persistentVolumeClaim": {"claimName": "spool"}},
                {
                    "name": "jail-rootfs-slot-a",
                    "persistentVolumeClaim": {"claimName": "stale-a"},
                },
                {
                    "name": "jail-persistent-data",
                    "persistentVolumeClaim": {"claimName": "stale-data"},
                },
            ]
        },
        target_ref="external-cluster",
        layout="external",
    )

    volume_sources = {item["name"]: item for item in values["volumeSources"]}
    assert set(volume_sources) == {"controller-spool", "jail"}
    assert volume_sources["controller-spool"]["persistentVolumeClaim"]["claimName"] == "spool"
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == (
        "jail-rootfs-slot-a-pvc"
    )


def test_apply_persistent_mount_values_adds_referenced_controller_spool_source() -> None:
    values = apply_jail_persistent_mount_values(
        {
            "slurmNodes": {
                "controller": {
                    "volumes": {
                        "spool": {
                            "volumeSourceName": "controller-spool",
                        }
                    }
                }
            }
        },
        target_ref="external-cluster",
        layout="external",
    )

    volume_sources = {item["name"]: item for item in values["volumeSources"]}
    assert volume_sources["controller-spool"]["persistentVolumeClaim"]["claimName"] == (
        "controller-spool-pvc"
    )
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == (
        "jail-rootfs-slot-a-pvc"
    )


def test_apply_managed_persistent_mount_values_uses_same_store_home() -> None:
    values = apply_jail_persistent_mount_values(
        {},
        target_ref="cxcli-slurm",
        layout="managed",
    )

    assert values["jailRootfs"]["store"]["mountPath"] == "/mnt/jail-store"
    assert values["jailRootfs"]["store"]["rootfsPath"] == "/mnt/jail-store/rootfs"
    assert values["jailPersistentMounts"] == [
        {"mountPath": "/home", "localPath": "/mnt/jail-store/shared/home"}
    ]
    assert values["volume"]["jail"]["localPath"] == "/mnt/jail-store"


def test_apply_managed_first_adoption_adds_customer_shared_paths() -> None:
    values = apply_jail_persistent_mount_values(
        {},
        target_ref="cxcli-slurm",
        layout="managed",
        include_default_shared_mounts=True,
        legacy_active_source=True,
    )

    assert values["jailPersistentMounts"] == [
        {"mountPath": "/home", "localPath": "/mnt/jail-store/shared/home"},
        {"mountPath": "/data", "localPath": "/mnt/jail-store/shared/data"},
        {"mountPath": "/scripts", "localPath": "/mnt/jail-store/shared/scripts"},
        {"mountPath": "/models", "localPath": "/mnt/jail-store/shared/models"},
    ]
    assert values["jailRootfs"]["adoption"]["activeSource"] == "legacy-rootfs"


def test_parse_explicit_persistent_mount_spec() -> None:
    mount = parse_jail_persistent_mount_spec("/data=/mnt/jail/shared/data")

    assert mount.mount_path == "/data"
    assert mount.local_path == "/mnt/jail/shared/data"
    assert mount.name == "jail-persistent-data"


def test_parse_multiple_shared_persistent_mount_specs() -> None:
    data = parse_jail_persistent_mount_spec("/data=/mnt/jail/shared/data")
    scripts = parse_jail_persistent_mount_spec("/scripts=/mnt/jail/shared/scripts")

    assert data.local_path == "/mnt/jail/shared/data"
    assert scripts.local_path == "/mnt/jail/shared/scripts"


def test_explicit_home_persistent_mount_replaces_default_home() -> None:
    values = apply_jail_persistent_mount_values(
        {},
        target_ref="external-cluster",
        persistent_mounts=[parse_jail_persistent_mount_spec("/home=/mnt/jail/customer-home")],
        layout="external",
    )

    assert values["jailPersistentMounts"] == [
        {"mountPath": "/home", "localPath": "/mnt/jail/customer-home"},
        {"mountPath": "/data", "localPath": "/mnt/jail/shared/data"},
        {"mountPath": "/scripts", "localPath": "/mnt/jail/shared/scripts"},
        {"mountPath": "/models", "localPath": "/mnt/jail/shared/models"},
    ]


def test_existing_external_home_submount_prevents_duplicate_default_home() -> None:
    values = apply_jail_persistent_mount_values(
        {
            "externalNfs": {
                "enabled": True,
                "mountPath": "/home",
                "server": "nfs.example.invalid",
                "path": "/exports/home",
            }
        },
        target_ref="external-cluster",
        layout="external",
    )

    assert values["jailPersistentMounts"] == [
        {"mountPath": "/data", "localPath": "/mnt/jail/shared/data"},
        {"mountPath": "/scripts", "localPath": "/mnt/jail/shared/scripts"},
        {"mountPath": "/models", "localPath": "/mnt/jail/shared/models"},
    ]
    assert jail_persistent_mount_status(values).status == "verified"


def test_persistent_mount_validation_rejects_bad_paths() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        normalize_jail_persistent_mounts(
            [
                {"mountPath": "/data", "localPath": "/mnt/jail/shared/data"},
                {"mountPath": "/data", "localPath": "/mnt/jail/other"},
            ],
            include_home=False,
        )

    with pytest.raises(ValueError, match="must not overlap"):
        normalize_jail_persistent_mounts(
            [{"mountPath": "/data", "localPath": "/mnt/jail/.cxcli/rootfs/slot-a/data"}],
            include_home=False,
        )

    with pytest.raises(ValueError, match="inside the physical jail store"):
        normalize_jail_persistent_mounts(
            [{"mountPath": "/data", "localPath": "/mnt/other/data"}],
            include_home=False,
            store_path="/mnt/jail",
        )
