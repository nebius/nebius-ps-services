from __future__ import annotations

import pytest

from nebius_cxcli.soperator_jail_mounts import (
    apply_jail_persistent_mount_values,
    jail_persistent_mounts_from_paths,
    jail_rootfs_active_source,
    normalize_jail_persistent_mounts,
)


def test_apply_external_persistent_mount_values_adopts_legacy_paths_in_place() -> None:
    values = apply_jail_persistent_mount_values(
        {"nodesets": [{"name": "worker"}]},
        target_ref="external-cluster",
        layout="external",
    )

    assert values["jailRootfs"]["store"]["mountPath"] == "/mnt/jail"
    assert values["jailRootfs"]["store"]["rootfsPath"] == "/mnt/jail/.cxcli/rootfs"
    assert values["jailRootfs"]["adoption"]["activeSource"] == "legacy-rootfs"
    assert values["jailPersistentMounts"] == [
        {"mountPath": "/home", "localPath": "/mnt/jail/home"},
        {"mountPath": "/data", "localPath": "/mnt/jail/data"},
        {"mountPath": "/scripts", "localPath": "/mnt/jail/scripts"},
        {"mountPath": "/models", "localPath": "/mnt/jail/models"},
    ]
    volume_sources = {item["name"]: item for item in values["volumeSources"]}
    assert set(volume_sources) == {
        "controller-spool",
        "jail",
    }
    assert volume_sources["controller-spool"]["persistentVolumeClaim"]["claimName"] == (
        "controller-spool-pvc"
    )
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == ("jail-pvc")
    assert "jail_home" not in values
    assert "home" not in values["jailRootfs"]


def test_apply_persistent_mount_values_removes_chart_rendered_volume_source_duplicates() -> None:
    values = apply_jail_persistent_mount_values(
        {
            "volumeSources": [
                {"name": "controller-spool", "persistentVolumeClaim": {"claimName": "spool"}},
                {
                    "name": "jail",
                    "csi": {"driver": "legacy.example.invalid"},
                    "glusterfs": {"endpoints": "legacy"},
                },
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
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == ("jail-pvc")
    assert "csi" not in volume_sources["jail"]
    assert "glusterfs" not in volume_sources["jail"]


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
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == ("jail-pvc")


def test_external_first_adoption_keeps_all_consumers_on_legacy_jail_pvc() -> None:
    configurable_jail_roles = ("controller", "login")
    values = apply_jail_persistent_mount_values(
        {
            "slurmNodes": {
                "controller": {
                    "volumes": {
                        "jail": {"persistentVolumeClaim": {"claimName": "stale-direct-pvc"}}
                    }
                },
                "login": {
                    "volumes": {
                        "jail": {
                            "volumeClaimTemplateSpec": {"resources": {}},
                        }
                    }
                },
                "exporter": {"volumes": {"jail": {"volumeSourceName": "jail-rootfs-slot-a"}}},
                "rest": {"volumes": {"jail": {"volumeSourceName": "jail-rootfs-slot-a"}}},
            },
            "nodesets": [
                {
                    "name": "worker",
                    "slurmd": {"volumes": {"jail": {"volumeSourceName": "jail-rootfs-slot-a"}}},
                }
            ],
        },
        target_ref="external-cluster",
        layout="external",
    )

    volume_sources = {item["name"]: item for item in values["volumeSources"]}
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == "jail-pvc"
    for role in configurable_jail_roles:
        assert values["slurmNodes"][role]["volumes"]["jail"] == {"volumeSourceName": "jail"}
    assert "volumes" not in values["slurmNodes"]["exporter"]
    assert "volumes" not in values["slurmNodes"]["rest"]
    assert values["nodesets"][0]["slurmd"]["volumes"]["jail"] == {
        "persistentVolumeClaim": {"claimName": "jail-pvc"}
    }


def test_apply_managed_persistent_mount_values_uses_same_store_home() -> None:
    values = apply_jail_persistent_mount_values(
        {},
        target_ref="cxcli-slurm",
        layout="managed",
    )

    assert values["jailRootfs"]["store"]["mountPath"] == "/mnt/jail-store"
    assert values["jailRootfs"]["store"]["rootfsPath"] == "/mnt/jail-store/rootfs"
    assert values["jailRootfs"]["adoption"] == {
        "activeSource": "slot",
        "rollbackSource": "slot",
    }
    assert values["jailPersistentMounts"] == [
        {"mountPath": "/home", "localPath": "/mnt/jail-store/shared/home"},
        {"mountPath": "/data", "localPath": "/mnt/jail-store/shared/data"},
        {"mountPath": "/scripts", "localPath": "/mnt/jail-store/shared/scripts"},
        {"mountPath": "/models", "localPath": "/mnt/jail-store/shared/models"},
    ]
    assert values["volume"]["jail"]["localPath"] == "/mnt/jail-store"


def test_active_source_resolver_distinguishes_slot_defaults_from_legacy_values() -> None:
    assert jail_rootfs_active_source({"jailRootfs": {"strategy": "activePassive"}}) == "slot"
    assert (
        jail_rootfs_active_source({"jailRootfs": {"strategy": "activePassive", "adoption": {}}})
        == "slot"
    )
    assert jail_rootfs_active_source({}) == "legacy-rootfs"
    assert (
        jail_rootfs_active_source({"jailRootfs": {"adoption": {"activeSource": "legacy-rootfs"}}})
        == "legacy-rootfs"
    )
    with pytest.raises(ValueError, match="must be slot or legacy-rootfs"):
        jail_rootfs_active_source({"jailRootfs": {"adoption": {"activeSource": "unknown"}}})


@pytest.mark.parametrize(
    "values, message",
    [
        ({"jailRootfs": None}, "jailRootfs must be a mapping"),
        ({"jailRootfs": []}, "jailRootfs must be a mapping"),
        (
            {"jailRootfs": {"strategy": "legacy"}},
            "jailRootfs.strategy must be activePassive when present",
        ),
        (
            {"jailRootfs": {"strategy": False}},
            "jailRootfs.strategy must be activePassive when present",
        ),
        (
            {"jailRootfs": {"strategy": "activePassive", "adoption": None}},
            "jailRootfs.adoption must be a mapping",
        ),
        (
            {"jailRootfs": {"strategy": "activePassive", "adoption": []}},
            "jailRootfs.adoption must be a mapping",
        ),
        (
            {
                "jailRootfs": {
                    "strategy": "activePassive",
                    "adoption": {"activeSource": None},
                }
            },
            "jailRootfs.adoption.activeSource must be a non-empty string",
        ),
        (
            {
                "jailRootfs": {
                    "strategy": "activePassive",
                    "adoption": {"activeSource": False},
                }
            },
            "jailRootfs.adoption.activeSource must be a non-empty string",
        ),
        (
            {
                "jailRootfs": {
                    "strategy": "activePassive",
                    "adoption": {"activeSource": 0},
                }
            },
            "jailRootfs.adoption.activeSource must be a non-empty string",
        ),
        (
            {
                "jailRootfs": {
                    "strategy": "activePassive",
                    "adoption": {"activeSource": ""},
                }
            },
            "jailRootfs.adoption.activeSource must be a non-empty string",
        ),
    ],
)
def test_active_source_resolver_rejects_malformed_explicit_state(
    values: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        jail_rootfs_active_source(values)


def test_apply_jail_mount_values_rejects_malformed_explicit_rootfs_state() -> None:
    with pytest.raises(ValueError, match="jailRootfs.strategy must be activePassive"):
        apply_jail_persistent_mount_values(
            {"jailRootfs": {"strategy": "legacy"}},
            target_ref="cxcli-slurm",
            layout="managed",
        )


def test_apply_managed_first_adoption_submounts_legacy_customer_paths_without_copy() -> None:
    values = apply_jail_persistent_mount_values(
        {
            "jailRootfs": {
                "adoption": {
                    "activeSource": "slot",
                    "rollbackSource": "slot",
                }
            }
        },
        target_ref="cxcli-slurm",
        layout="managed",
        legacy_active_source=True,
    )

    assert values["jailPersistentMounts"] == [
        {"mountPath": "/home", "localPath": "/mnt/jail-store/home"},
        {"mountPath": "/data", "localPath": "/mnt/jail-store/data"},
        {"mountPath": "/scripts", "localPath": "/mnt/jail-store/scripts"},
        {"mountPath": "/models", "localPath": "/mnt/jail-store/models"},
    ]
    assert values["jailRootfs"]["adoption"]["activeSource"] == "legacy-rootfs"
    assert values["jailRootfs"]["adoption"]["rollbackSource"] == "legacy-rootfs"


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
        {"mountPath": "/data", "localPath": "/mnt/jail/data"},
        {"mountPath": "/scripts", "localPath": "/mnt/jail/scripts"},
        {"mountPath": "/models", "localPath": "/mnt/jail/models"},
    ]


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

    with pytest.raises(ValueError, match="must not overlap"):
        normalize_jail_persistent_mounts(
            [
                {"mountPath": "/data", "localPath": "/mnt/jail/data"},
                {"mountPath": "/models", "localPath": "/mnt/jail/data/models"},
            ],
            include_home=False,
        )


def test_optional_paths_derive_layout_owned_backing_paths() -> None:
    assert [
        mount.as_values()
        for mount in jail_persistent_mounts_from_paths(["/opt/customer-data"], layout="external")
    ] == [{"mountPath": "/opt/customer-data", "localPath": "/mnt/jail/opt/customer-data"}]
    assert [
        mount.as_values()
        for mount in jail_persistent_mounts_from_paths(["/srv/results"], layout="managed")
    ] == [
        {
            "mountPath": "/srv/results",
            "localPath": "/mnt/jail-store/shared/srv/results",
        }
    ]
    assert [
        mount.as_values()
        for mount in jail_persistent_mounts_from_paths(
            ["/srv/results"], layout="managed", legacy_active_source=True
        )
    ] == [
        {
            "mountPath": "/srv/results",
            "localPath": "/mnt/jail-store/srv/results",
        }
    ]


@pytest.mark.parametrize("path", ["/usr", "/opt", "/etc", "/proc/data", "/tmp/cache"])
def test_optional_paths_reject_system_roots_and_runtime_trees(path: str) -> None:
    with pytest.raises(ValueError):
        jail_persistent_mounts_from_paths([path], layout="external")
