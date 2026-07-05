from __future__ import annotations

import pytest

from nebius_cxcli.soperator_jail_mounts import (
    apply_jail_persistent_mount_values,
    jail_persistent_mount_status,
    normalize_jail_persistent_mounts,
    parse_jail_persistent_mount_spec,
)


def test_apply_external_persistent_mount_values_adds_home_without_copy() -> None:
    values = apply_jail_persistent_mount_values(
        {"nodesets": [{"name": "worker"}]},
        target_ref="external-cluster",
        layout="external",
    )

    assert values["jailRootfs"]["store"]["mountPath"] == "/mnt/jail"
    assert values["jailRootfs"]["store"]["rootfsPath"] == "/mnt/jail/.cxcli/rootfs"
    assert values["jailRootfs"]["adoption"]["activeSource"] == "legacy-rootfs"
    assert values["jailPersistentMounts"] == [
        {"mountPath": "/home", "localPath": "/mnt/jail/home"}
    ]
    assert "jail_home" not in values
    assert "home" not in values["jailRootfs"]


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


def test_parse_explicit_persistent_mount_spec() -> None:
    mount = parse_jail_persistent_mount_spec("/data=/mnt/jail/data")

    assert mount.mount_path == "/data"
    assert mount.local_path == "/mnt/jail/data"
    assert mount.name == "jail-persistent-data"


def test_explicit_home_persistent_mount_replaces_default_home() -> None:
    values = apply_jail_persistent_mount_values(
        {},
        target_ref="external-cluster",
        persistent_mounts=[parse_jail_persistent_mount_spec("/home=/mnt/jail/customer-home")],
        layout="external",
    )

    assert values["jailPersistentMounts"] == [
        {"mountPath": "/home", "localPath": "/mnt/jail/customer-home"}
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

    assert values["jailPersistentMounts"] == []
    assert jail_persistent_mount_status(values).status == "verified"


def test_persistent_mount_validation_rejects_bad_paths() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        normalize_jail_persistent_mounts(
            [
                {"mountPath": "/data", "localPath": "/mnt/jail/data"},
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
