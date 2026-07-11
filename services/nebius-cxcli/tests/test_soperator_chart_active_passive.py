from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CHART = REPO_ROOT / "helm-charts" / "soperator"
_CHART_DEPENDENCIES_READY = False


def _require_helm() -> None:
    if shutil.which("helm") is None:
        pytest.skip("helm is not installed")


def _chart_dependency_archives_missing() -> bool:
    return any(
        not _chart_dependency_archive_path(dependency).exists() for dependency in _dependencies()
    )


def _dependencies() -> list[dict[str, Any]]:
    chart_yaml = yaml.safe_load((CHART / "Chart.yaml").read_text(encoding="utf-8"))
    dependencies = chart_yaml.get("dependencies") if isinstance(chart_yaml, dict) else []
    if not isinstance(dependencies, list):
        return []
    return [dependency for dependency in dependencies if isinstance(dependency, dict)]


def _chart_dependency_archive_path(dependency: dict[str, Any]) -> Path:
    name = str(dependency.get("name") or "")
    version = str(dependency.get("version") or "")
    return CHART / "charts" / f"{name}-{version}.tgz"


def _remote_dependency_repositories() -> dict[str, str]:
    repositories: dict[str, str] = {}
    for dependency in _dependencies():
        name = str(dependency.get("name") or "")
        repository = str(dependency.get("repository") or "")
        if name and "://" in repository and not repository.startswith("file://"):
            repositories[f"cxcli-{name}"] = repository
    return repositories


def _ensure_dependency_repositories() -> None:
    for name, repository in _remote_dependency_repositories().items():
        result = subprocess.run(
            ["helm", "repo", "add", name, repository, "--force-update"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.fail(
                f"helm repo add failed before rendering {CHART}: {result.stderr or result.stdout}"
            )


def _ensure_chart_dependencies() -> None:
    global _CHART_DEPENDENCIES_READY
    if _CHART_DEPENDENCIES_READY:
        return
    if _chart_dependency_archives_missing():
        _ensure_dependency_repositories()
        result = subprocess.run(
            ["helm", "dependency", "build", str(CHART)],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.fail(
                "helm dependency build failed before rendering "
                f"{CHART}: {result.stderr or result.stdout}"
            )
    _CHART_DEPENDENCIES_READY = True


def _helm_template(*args: str, check: bool) -> subprocess.CompletedProcess[str]:
    _require_helm()
    _ensure_chart_dependencies()
    result = subprocess.run(
        ["helm", "template", "test", str(CHART), "-n", "soperator", *args],
        text=True,
        capture_output=True,
        check=check,
    )
    return result


def _render(*args: str) -> list[dict[str, Any]]:
    result = _helm_template(*args, check=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]


def _render_values(tmp_path: Path, values: dict[str, Any]) -> list[dict[str, Any]]:
    values_path = tmp_path / "values.yaml"
    values_path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return _render("-f", str(values_path))


def _chart_values() -> dict[str, Any]:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert isinstance(values, dict)
    return values


def _by_kind_name(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name:
            return doc
    raise AssertionError(f"{kind}/{name} was not rendered")


def test_active_passive_jail_rootfs_default_storage_contract() -> None:
    docs = _render()
    slurm_cluster = _by_kind_name(docs, "SlurmCluster", "soperator")
    worker_nodeset = _by_kind_name(docs, "NodeSet", "worker")

    assert (
        _by_kind_name(docs, "PersistentVolume", "jail-rootfs-slot-a-pv")["spec"]["local"]["path"]
        == "/mnt/jail-store/rootfs/slot-a"
    )
    assert (
        _by_kind_name(docs, "PersistentVolume", "jail-rootfs-slot-b-pv")["spec"]["local"]["path"]
        == "/mnt/jail-store/rootfs/slot-b"
    )
    assert (
        _by_kind_name(docs, "PersistentVolume", "jail-persistent-home-pv")["spec"]["local"]["path"]
        == "/mnt/jail-store/shared/home"
    )
    assert (
        _by_kind_name(docs, "PersistentVolume", "jail-rootfs-slot-a-pv")["spec"]["capacity"][
            "storage"
        ]
        == "2Ti"
    )
    assert (
        _by_kind_name(docs, "PersistentVolume", "jail-rootfs-slot-b-pv")["spec"]["capacity"][
            "storage"
        ]
        == "2Ti"
    )
    assert (
        _by_kind_name(docs, "PersistentVolume", "jail-persistent-home-pv")["spec"]["capacity"][
            "storage"
        ]
        == "2Ti"
    )
    assert _by_kind_name(docs, "PersistentVolumeClaim", "jail-rootfs-slot-a-pvc")
    assert _by_kind_name(docs, "PersistentVolumeClaim", "jail-rootfs-slot-b-pvc")
    assert _by_kind_name(docs, "PersistentVolumeClaim", "jail-persistent-home-pvc")
    assert (
        _by_kind_name(docs, "PersistentVolumeClaim", "jail-rootfs-slot-a-pvc")["spec"]["resources"][
            "requests"
        ]["storage"]
        == "2Ti"
    )

    volume_sources = {item["name"]: item for item in slurm_cluster["spec"]["volumeSources"]}
    assert set(volume_sources) >= {
        "jail-rootfs-slot-a",
        "jail-rootfs-slot-b",
        "jail-persistent-home",
    }
    assert volume_sources["jail-rootfs-slot-a"]["persistentVolumeClaim"]["claimName"] == (
        "jail-rootfs-slot-a-pvc"
    )
    assert volume_sources["jail-rootfs-slot-b"]["persistentVolumeClaim"]["claimName"] == (
        "jail-rootfs-slot-b-pvc"
    )
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == (
        "jail-rootfs-slot-a-pvc"
    )
    assert volume_sources["jail-persistent-home"]["persistentVolumeClaim"]["claimName"] == (
        "jail-persistent-home-pvc"
    )
    assert slurm_cluster["spec"]["slurmNodes"]["login"]["size"] == 2
    assert slurm_cluster["spec"]["slurmNodes"]["login"]["volumes"]["jail"] == {
        "volumeSourceName": "jail-rootfs-slot-a"
    }
    assert "SlurmdParameters=l3cache_as_socket" in slurm_cluster["spec"]["customSlurmConfig"]
    assert worker_nodeset["spec"]["customInitContainers"][0]["name"] == "cxcli-slurm-config-jail"
    assert {
        "name": "jail",
        "mountPath": "/mnt/jail",
    } in worker_nodeset["spec"]["customInitContainers"][0]["volumeMounts"]
    assert {
        "name": "slurm-configs-jail",
        "mountPath": "/mnt/slurm-configs",
        "readOnly": True,
    } in worker_nodeset["spec"]["customInitContainers"][0]["volumeMounts"]
    assert (
        worker_nodeset["spec"]["nodeConfig"]["static"]
        == "Boards=1 SocketsPerBoard=1 CoresPerSocket=64 ThreadsPerCore=1 Gres=gpu:8"
    )
    assert {
        "name": "jail-persistent-home",
        "mountPath": "/home",
        "volumeSourceName": "jail-persistent-home",
    } in slurm_cluster["spec"]["slurmNodes"]["login"]["volumes"]["jailSubMounts"]
    assert {
        "name": "jail-persistent-home",
        "mountPath": "/home",
        "volumeSource": {
            "persistentVolumeClaim": {
                "claimName": "jail-persistent-home-pvc",
            },
        },
    } in worker_nodeset["spec"]["slurmd"]["volumes"]["jailSubMounts"]
    for mount in worker_nodeset["spec"]["slurmd"]["volumes"]["jailSubMounts"]:
        assert "volumeSourceName" not in mount
    assert {
        "name": "slurm-configs-jail",
        "mountPath": "/mnt/jail/etc/slurm",
        "readOnly": True,
        "volumeSource": {
            "configMap": {
                "name": "soperator-slurm-configs",
            },
        },
    } in worker_nodeset["spec"]["slurmd"]["volumes"]["customVolumeMounts"]


def test_external_single_sfs_layout_uses_legacy_jail_store_paths(tmp_path: Path) -> None:
    values = _chart_values()
    values["jailRootfs"]["store"] = {
        "mountPath": "/mnt/jail",
        "rootfsPath": "/mnt/jail/.cxcli/rootfs",
        "volumeKey": "jail",
    }
    values["jailRootfs"]["adoption"] = {"activeSource": "legacy-rootfs"}
    values["jailPersistentMounts"] = [{"mountPath": "/home", "localPath": "/mnt/jail/home"}]

    docs = _render_values(tmp_path, values)

    assert (
        _by_kind_name(docs, "PersistentVolume", "jail-pv")["spec"]["local"]["path"] == "/mnt/jail"
    )
    assert (
        _by_kind_name(docs, "PersistentVolume", "jail-rootfs-slot-b-pv")["spec"]["local"]["path"]
        == "/mnt/jail/.cxcli/rootfs/slot-b"
    )
    assert (
        _by_kind_name(docs, "PersistentVolume", "jail-persistent-home-pv")["spec"]["local"]["path"]
        == "/mnt/jail/home"
    )

    slurm_cluster = _by_kind_name(docs, "SlurmCluster", "soperator")
    worker_nodeset = _by_kind_name(docs, "NodeSet", "worker")
    volume_sources = {item["name"]: item for item in slurm_cluster["spec"]["volumeSources"]}
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == "jail-pvc"
    assert slurm_cluster["spec"]["slurmNodes"]["controller"]["volumes"]["jail"] == {
        "volumeSourceName": "jail"
    }
    assert slurm_cluster["spec"]["slurmNodes"]["login"]["volumes"]["jail"] == {
        "volumeSourceName": "jail"
    }
    assert worker_nodeset["spec"]["slurmd"]["volumes"]["jail"] == {
        "persistentVolumeClaim": {"claimName": "jail-pvc", "readOnly": False}
    }
    assert not slurm_cluster["spec"]["slurmNodes"]["login"]["volumes"]["jailSubMounts"]
    assert not worker_nodeset["spec"]["slurmd"]["volumes"].get("jailSubMounts")


def test_legacy_rootfs_with_external_home_renders_worker_jail_submount(
    tmp_path: Path,
) -> None:
    values = _chart_values()
    values["jailRootfs"]["adoption"] = {"activeSource": "legacy-rootfs"}
    values["externalNfs"] = {
        "enabled": True,
        "server": "example.invalid",
        "path": "/share",
        "mountPath": "/home",
        "readOnly": False,
    }

    docs = _render_values(tmp_path, values)

    worker_nodeset = _by_kind_name(docs, "NodeSet", "worker")
    assert {
        "name": "external-home",
        "mountPath": "/home",
        "volumeSourceName": "external-home",
        "readOnly": False,
    } in worker_nodeset["spec"]["slurmd"]["volumes"]["jailSubMounts"]


def test_custom_legacy_jail_pvc_is_referenced_without_chart_owned_duplicate(
    tmp_path: Path,
) -> None:
    values = _chart_values()
    values["jailRootfs"]["adoption"] = {
        "activeSource": "legacy-rootfs",
        "rollbackSource": "legacy-rootfs",
        "legacyPvcName": "source-jail-pvc",
    }

    docs = _render_values(tmp_path, values)

    assert not any(
        doc.get("kind") == "PersistentVolume" and doc.get("metadata", {}).get("name") == "jail-pv"
        for doc in docs
    )
    assert not any(
        doc.get("kind") == "PersistentVolumeClaim"
        and doc.get("metadata", {}).get("name") == "jail-pvc"
        for doc in docs
    )
    slurm_cluster = _by_kind_name(docs, "SlurmCluster", "soperator")
    worker_nodeset = _by_kind_name(docs, "NodeSet", "worker")
    volume_sources = {item["name"]: item for item in slurm_cluster["spec"]["volumeSources"]}
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == ("source-jail-pvc")
    assert slurm_cluster["spec"]["slurmNodes"]["controller"]["volumes"]["jail"] == {
        "volumeSourceName": "jail"
    }
    assert slurm_cluster["spec"]["slurmNodes"]["login"]["volumes"]["jail"] == {
        "volumeSourceName": "jail"
    }
    assert worker_nodeset["spec"]["slurmd"]["volumes"]["jail"] == {
        "persistentVolumeClaim": {"claimName": "source-jail-pvc", "readOnly": False}
    }


def test_explicit_canonical_legacy_jail_pvc_stays_chart_owned(
    tmp_path: Path,
) -> None:
    values = _chart_values()
    values["jailRootfs"]["adoption"] = {
        "activeSource": "legacy-rootfs",
        "rollbackSource": "legacy-rootfs",
        "legacyPvcName": "jail-pvc",
    }

    docs = _render_values(tmp_path, values)

    assert _by_kind_name(docs, "PersistentVolume", "jail-pv")
    assert _by_kind_name(docs, "PersistentVolumeClaim", "jail-pvc")


def test_external_single_sfs_legacy_jail_pvc_stays_rendered_for_rollback(
    tmp_path: Path,
) -> None:
    values = _chart_values()
    values["jailRootfs"].update(
        {
            "activeSlot": "slot-b",
            "passiveSlot": "slot-a",
            "store": {
                "mountPath": "/mnt/jail",
                "rootfsPath": "/mnt/jail/.cxcli/rootfs",
                "volumeKey": "jail",
            },
            "adoption": {
                "activeSource": "slot",
                "rollbackSource": "legacy-rootfs",
            },
        }
    )
    for role in ("controller", "login"):
        values["slurmNodes"][role]["volumes"]["jail"] = {"volumeSourceName": "jail-rootfs-slot-b"}
    values["nodesets"][0]["slurmd"]["volumes"]["jail"] = {
        "persistentVolumeClaim": {"claimName": "jail-rootfs-slot-b-pvc"}
    }
    values["jailPersistentMounts"] = [{"mountPath": "/home", "localPath": "/mnt/jail/home"}]

    docs = _render_values(tmp_path, values)

    assert (
        _by_kind_name(docs, "PersistentVolume", "jail-pv")["spec"]["local"]["path"] == "/mnt/jail"
    )
    assert _by_kind_name(docs, "PersistentVolumeClaim", "jail-pvc")

    slurm_cluster = _by_kind_name(docs, "SlurmCluster", "soperator")
    worker_nodeset = _by_kind_name(docs, "NodeSet", "worker")
    volume_sources = {item["name"]: item for item in slurm_cluster["spec"]["volumeSources"]}
    assert volume_sources["jail"]["persistentVolumeClaim"]["claimName"] == (
        "jail-rootfs-slot-b-pvc"
    )
    assert slurm_cluster["spec"]["slurmNodes"]["controller"]["volumes"]["jail"] == {
        "volumeSourceName": "jail-rootfs-slot-b"
    }
    assert slurm_cluster["spec"]["slurmNodes"]["login"]["volumes"]["jail"] == {
        "volumeSourceName": "jail-rootfs-slot-b"
    }
    assert worker_nodeset["spec"]["slurmd"]["volumes"]["jail"] == {
        "persistentVolumeClaim": {"claimName": "jail-rootfs-slot-b-pvc"}
    }
    assert {
        "name": "jail-persistent-home",
        "mountPath": "/home",
        "volumeSourceName": "jail-persistent-home",
    } in slurm_cluster["spec"]["slurmNodes"]["login"]["volumes"]["jailSubMounts"]
    assert {
        "name": "jail-persistent-home",
        "mountPath": "/home",
        "volumeSource": {"persistentVolumeClaim": {"claimName": "jail-persistent-home-pvc"}},
    } in worker_nodeset["spec"]["slurmd"]["volumes"]["jailSubMounts"]


def test_persistent_mount_names_do_not_collide_when_long_paths_share_prefix() -> None:
    docs = _render(
        "--set",
        "jailPersistentMounts[0].mountPath=/customer/projects/model-training/checkpoints/experiments/team-a-alpha",
        "--set",
        "jailPersistentMounts[0].localPath=/mnt/jail-store/shared/team-a-alpha",
        "--set",
        "jailPersistentMounts[1].mountPath=/customer/projects/model-training/checkpoints/experiments/team-a-beta",
        "--set",
        "jailPersistentMounts[1].localPath=/mnt/jail-store/shared/team-a-beta",
    )
    slurm_cluster = _by_kind_name(docs, "SlurmCluster", "soperator")

    persistent_pv_names = [
        doc["metadata"]["name"]
        for doc in docs
        if doc.get("kind") == "PersistentVolume"
        and doc.get("metadata", {}).get("name", "").startswith("jail-persistent-")
    ]
    persistent_pvc_names = [
        doc["metadata"]["name"]
        for doc in docs
        if doc.get("kind") == "PersistentVolumeClaim"
        and doc.get("metadata", {}).get("name", "").startswith("jail-persistent-")
    ]
    volume_source_names = [
        item["name"]
        for item in slurm_cluster["spec"]["volumeSources"]
        if item["name"].startswith("jail-persistent-")
    ]

    assert len(persistent_pv_names) == 2
    assert len(set(persistent_pv_names)) == 2
    assert len(persistent_pvc_names) == 2
    assert len(set(persistent_pvc_names)) == 2
    assert len(volume_source_names) == 2
    assert len(set(volume_source_names)) == 2


def test_active_passive_jail_rootfs_rejects_invalid_slot_path() -> None:
    result = _helm_template(
        "--set",
        "jailRootfs.slots.slot-a.localPath=/tmp/bad",
        check=False,
    )

    assert result.returncode != 0
    assert "jailRootfs.slots.slot-a.localPath must be /mnt/jail-store/rootfs/slot-a" in (
        result.stderr + result.stdout
    )


def test_active_passive_jail_rootfs_rejects_invalid_adoption_source() -> None:
    result = _helm_template(
        "--set",
        "jailRootfs.adoption.activeSource=legacy-rootf",
        "--set",
        "jailRootfs.adoption.legacyPvcName=source-jail-pvc",
        check=False,
    )

    assert result.returncode != 0
    assert "/jailRootfs/adoption/activeSource" in result.stderr + result.stdout


def test_legacy_adoption_rejects_reserved_slot_volume_source_name() -> None:
    result = _helm_template(
        "--set",
        "jailRootfs.adoption.activeSource=legacy-rootfs",
        "--set",
        "jailRootfs.adoption.legacyPvcName=source-jail-pvc",
        "--set",
        "jailRootfs.slots.slot-a.volumeSourceName=jail",
        check=False,
    )

    assert result.returncode != 0
    assert "reserved legacy source name" in result.stderr + result.stdout


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (
            (
                "jailRootfs.slots.slot-a.pvcName=shared-slot-pvc",
                "jailRootfs.slots.slot-b.pvcName=shared-slot-pvc",
            ),
            "slot pvcName values must be distinct",
        ),
        (
            (
                "jailRootfs.adoption.activeSource=legacy-rootfs",
                "jailRootfs.adoption.legacyPvcName=jail-rootfs-slot-b-pvc",
            ),
            "must differ from legacyPvcName",
        ),
        (
            ("jailRootfs.slots.slot-a.volumeSourceName=jail-persistent-home",),
            "generated volume source",
        ),
        (
            ("jailRootfs.slots.slot-a.pvcName=jail-persistent-home-pvc",),
            "generated PVC",
        ),
        (
            (
                "jailRootfs.adoption.activeSource=legacy-rootfs",
                "jailRootfs.adoption.legacyPvcName=legacy-pvc",
                "volume.jail.name=legacy",
                "jailRootfs.slots.slot-a.volumeSourceName=legacy",
            ),
            "chart-owned legacy jail PV name",
        ),
    ],
)
def test_active_passive_jail_rootfs_rejects_generated_name_collisions(
    settings: tuple[str, ...],
    expected: str,
) -> None:
    args = [item for setting in settings for item in ("--set", setting)]
    result = _helm_template(*args, check=False)

    assert result.returncode != 0
    assert expected in result.stderr + result.stdout


def test_active_passive_jail_rootfs_rejects_persistent_mount_overlap() -> None:
    result = _helm_template(
        "--set",
        "jailPersistentMounts[0].mountPath=/data",
        "--set",
        "jailPersistentMounts[0].localPath=/mnt/jail-store/rootfs/slot-a/data",
        check=False,
    )

    assert result.returncode != 0
    assert "must not overlap rootfs or cxcli system path" in result.stderr + result.stdout


def test_active_passive_jail_rootfs_rejects_duplicate_normalized_persistent_mounts() -> None:
    result = _helm_template(
        "--set",
        "jailPersistentMounts[0].mountPath=/data",
        "--set",
        "jailPersistentMounts[0].localPath=/mnt/jail-store/shared/data",
        "--set",
        "jailPersistentMounts[1].mountPath=/data/",
        "--set",
        "jailPersistentMounts[1].localPath=/mnt/jail-store/shared/data-shadow",
        check=False,
    )

    assert result.returncode != 0
    assert 'duplicate jailPersistentMounts mountPath "/data"' in result.stderr + result.stdout


def test_active_passive_jail_rootfs_rejects_non_normalized_persistent_mount_paths() -> None:
    result = _helm_template(
        "--set",
        "jailPersistentMounts[0].mountPath=/data//training",
        "--set",
        "jailPersistentMounts[0].localPath=/mnt/jail-store/shared/data",
        check=False,
    )

    assert result.returncode != 0
    assert "mountPath must be an absolute normalized non-root path" in (
        result.stderr + result.stdout
    )
