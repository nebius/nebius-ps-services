from __future__ import annotations

import pytest

from nebius_cxcli.soperator_home import (
    HOME_SFS_TARGET_PVC,
    apply_home_sfs_migration_values,
    compute_home_sfs_size_gib,
    soperator_home_preservation_status,
)


def test_home_preservation_detects_external_nfs() -> None:
    status = soperator_home_preservation_status(
        {
            "externalNfs": {
                "enabled": True,
                "server": "nfs.example.invalid",
                "path": "/exports/home",
                "mountPath": "/home",
            }
        }
    )

    assert status.status == "verified"
    assert status.external is True
    assert status.source == "externalNfs:nfs.example.invalid:/exports/home"


def test_home_preservation_detects_existing_home_submount() -> None:
    status = soperator_home_preservation_status(
        {
            "volumeSources": [
                {
                    "name": "home",
                    "persistentVolumeClaim": {"claimName": "home-pvc"},
                }
            ],
            "slurmNodes": {
                "login": {
                    "volumes": {
                        "jailSubMounts": [
                            {
                                "name": "home",
                                "mountPath": "/home",
                                "volumeSourceName": "home",
                            }
                        ]
                    }
                }
            },
        }
    )

    assert status.status == "verified"
    assert status.source == "volumeSource:home"


def test_home_preservation_requires_migration_when_not_external() -> None:
    status = soperator_home_preservation_status({"slurmNodes": {"login": {"volumes": {}}}})

    assert status.status == "needs_home_sfs_migration"
    assert status.needs_migration is True


def test_compute_home_sfs_size_uses_headroom_and_rejects_undersized_override() -> None:
    assert compute_home_sfs_size_gib(usage_bytes=10 * 1024**3, multiplier=1.3) == 13

    with pytest.raises(ValueError, match="must not be smaller"):
        compute_home_sfs_size_gib(
            usage_bytes=10 * 1024**3,
            multiplier=1.3,
            explicit_size_gib=9,
        )


def test_apply_home_sfs_migration_values_adds_sfs_volume_and_submounts() -> None:
    values = apply_home_sfs_migration_values(
        {
            "nodesets": [
                {"name": "worker", "slurmd": {"volumes": {"jailSubMounts": []}}},
            ],
            "slurmNodes": {"login": {"volumes": {"jailSubMounts": []}}},
        },
        target_ref="external-cluster",
        size_gib=42,
    )

    assert values["homeSfsMigration"]["enabled"] is True
    assert values["homeSfsMigration"]["targetPvc"] == HOME_SFS_TARGET_PVC
    assert values["sfs"]["filesystems"]["home"]["size_gib"] == 42
    assert values["volume"]["jailSubMounts"][0]["name"] == "home"
    assert values["volumeSources"][-1]["persistentVolumeClaim"]["claimName"] == HOME_SFS_TARGET_PVC
    assert values["slurmNodes"]["login"]["volumes"]["jailSubMounts"][0]["mountPath"] == "/home"
    assert values["nodesets"][0]["slurmd"]["volumes"]["jailSubMounts"][0]["mountPath"] == "/home"
