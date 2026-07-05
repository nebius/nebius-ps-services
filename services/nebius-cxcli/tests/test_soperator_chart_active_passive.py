from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CHART = REPO_ROOT / "helm-charts" / "soperator"


def _render(*args: str) -> list[dict[str, Any]]:
    if shutil.which("helm") is None:
        pytest.skip("helm is not installed")
    result = subprocess.run(
        ["helm", "template", "test", str(CHART), "-n", "soperator", *args],
        text=True,
        capture_output=True,
        check=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]


def _by_kind_name(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name:
            return doc
    raise AssertionError(f"{kind}/{name} was not rendered")


def test_active_passive_jail_rootfs_default_storage_contract() -> None:
    docs = _render()
    slurm_cluster = _by_kind_name(docs, "SlurmCluster", "soperator")

    assert _by_kind_name(docs, "PersistentVolume", "jail-rootfs-slot-a-pv")["spec"][
        "local"
    ]["path"] == "/mnt/jail-store/rootfs/slot-a"
    assert _by_kind_name(docs, "PersistentVolume", "jail-rootfs-slot-b-pv")["spec"][
        "local"
    ]["path"] == "/mnt/jail-store/rootfs/slot-b"
    assert _by_kind_name(docs, "PersistentVolume", "jail-persistent-home-pv")["spec"]["local"][
        "path"
    ] == "/mnt/jail-store/shared/home"
    assert _by_kind_name(docs, "PersistentVolume", "jail-rootfs-slot-a-pv")["spec"][
        "capacity"
    ]["storage"] == "2Ti"
    assert _by_kind_name(docs, "PersistentVolume", "jail-rootfs-slot-b-pv")["spec"][
        "capacity"
    ]["storage"] == "2Ti"
    assert _by_kind_name(docs, "PersistentVolume", "jail-persistent-home-pv")["spec"]["capacity"][
        "storage"
    ] == "2Ti"
    assert _by_kind_name(docs, "PersistentVolumeClaim", "jail-rootfs-slot-a-pvc")
    assert _by_kind_name(docs, "PersistentVolumeClaim", "jail-rootfs-slot-b-pvc")
    assert _by_kind_name(docs, "PersistentVolumeClaim", "jail-persistent-home-pvc")
    assert _by_kind_name(docs, "PersistentVolumeClaim", "jail-rootfs-slot-a-pvc")[
        "spec"
    ]["resources"]["requests"]["storage"] == "2Ti"

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
    assert volume_sources["jail-persistent-home"]["persistentVolumeClaim"]["claimName"] == (
        "jail-persistent-home-pvc"
    )
    assert slurm_cluster["spec"]["slurmNodes"]["login"]["size"] == 2
    assert slurm_cluster["spec"]["slurmNodes"]["login"]["volumes"]["jail"] == {
        "volumeSourceName": "jail-rootfs-slot-a"
    }
    assert {
        "name": "jail-persistent-home",
        "mountPath": "/home",
        "volumeSourceName": "jail-persistent-home",
    } in slurm_cluster["spec"]["slurmNodes"]["login"]["volumes"]["jailSubMounts"]


def test_external_single_sfs_layout_uses_legacy_jail_store_paths() -> None:
    docs = _render(
        "--set",
        "jailRootfs.store.mountPath=/mnt/jail",
        "--set",
        "jailRootfs.store.rootfsPath=/mnt/jail/.cxcli/rootfs",
        "--set",
        "jailRootfs.adoption.activeSource=legacy-rootfs",
        "--set",
        "jailPersistentMounts[0].mountPath=/home",
        "--set",
        "jailPersistentMounts[0].localPath=/mnt/jail/home",
    )

    assert _by_kind_name(docs, "PersistentVolume", "jail-pv")["spec"]["local"][
        "path"
    ] == "/mnt/jail"
    assert _by_kind_name(docs, "PersistentVolume", "jail-rootfs-slot-b-pv")["spec"][
        "local"
    ]["path"] == "/mnt/jail/.cxcli/rootfs/slot-b"
    assert _by_kind_name(docs, "PersistentVolume", "jail-persistent-home-pv")["spec"][
        "local"
    ]["path"] == "/mnt/jail/home"


def test_external_single_sfs_legacy_jail_pvc_stays_rendered_for_rollback() -> None:
    docs = _render(
        "--set",
        "jailRootfs.store.mountPath=/mnt/jail",
        "--set",
        "jailRootfs.store.rootfsPath=/mnt/jail/.cxcli/rootfs",
        "--set",
        "jailRootfs.adoption.activeSource=slot",
        "--set",
        "jailRootfs.adoption.rollbackSource=legacy-rootfs",
        "--set",
        "jailRootfs.activeSlot=slot-b",
        "--set",
        "jailRootfs.passiveSlot=slot-a",
        "--set",
        "jailPersistentMounts[0].mountPath=/home",
        "--set",
        "jailPersistentMounts[0].localPath=/mnt/jail/home",
    )

    assert _by_kind_name(docs, "PersistentVolume", "jail-pv")["spec"]["local"][
        "path"
    ] == "/mnt/jail"
    assert _by_kind_name(docs, "PersistentVolumeClaim", "jail-pvc")


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
    if shutil.which("helm") is None:
        pytest.skip("helm is not installed")
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(CHART),
            "-n",
            "soperator",
            "--set",
            "jailRootfs.slots.slot-a.localPath=/tmp/bad",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "jailRootfs.slots.slot-a.localPath must be /mnt/jail-store/rootfs/slot-a" in (
        result.stderr + result.stdout
    )


def test_active_passive_jail_rootfs_rejects_persistent_mount_overlap() -> None:
    if shutil.which("helm") is None:
        pytest.skip("helm is not installed")
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(CHART),
            "-n",
            "soperator",
            "--set",
            "jailPersistentMounts[0].mountPath=/data",
            "--set",
            "jailPersistentMounts[0].localPath=/mnt/jail-store/rootfs/slot-a/data",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "must not overlap rootfs or cxcli system path" in result.stderr + result.stdout


def test_active_passive_jail_rootfs_rejects_duplicate_normalized_persistent_mounts() -> None:
    if shutil.which("helm") is None:
        pytest.skip("helm is not installed")
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(CHART),
            "-n",
            "soperator",
            "--set",
            "jailPersistentMounts[0].mountPath=/data",
            "--set",
            "jailPersistentMounts[0].localPath=/mnt/jail-store/shared/data",
            "--set",
            "jailPersistentMounts[1].mountPath=/data/",
            "--set",
            "jailPersistentMounts[1].localPath=/mnt/jail-store/shared/data-shadow",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert 'duplicate jailPersistentMounts mountPath "/data"' in result.stderr + result.stdout


def test_active_passive_jail_rootfs_rejects_non_normalized_persistent_mount_paths() -> None:
    if shutil.which("helm") is None:
        pytest.skip("helm is not installed")
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(CHART),
            "-n",
            "soperator",
            "--set",
            "jailPersistentMounts[0].mountPath=/data//training",
            "--set",
            "jailPersistentMounts[0].localPath=/mnt/jail-store/shared/data",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "mountPath must be an absolute normalized non-root path" in (
        result.stderr + result.stdout
    )
