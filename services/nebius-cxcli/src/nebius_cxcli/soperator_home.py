"""Soperator /home preservation helpers."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

from .runtime_config import to_plain_data

HOME_SFS_KEY = "home"
HOME_SFS_MIGRATION_VALUES_KEY = "homeSfsMigration"
HOME_SFS_VOLUME_SOURCE_NAME = "home"
HOME_SFS_SUBMOUNT_NAME = "home"
HOME_SFS_MOUNT_PATH = "/home"
HOME_SFS_MOUNT_TAG = "home"
HOME_SFS_SOURCE_PVC = "jail-pvc"
HOME_SFS_SOURCE_PATH = "/home"
HOME_SFS_TARGET_PVC = "jail-submount-home-pvc"
HOME_SFS_TARGET_PATH = "/"
HOME_SFS_DEFAULT_BLOCK_SIZE_KIB = 4
HOME_SFS_DEFAULT_TYPE = "NETWORK_SSD"
HOME_SFS_DEFAULT_SIZE_MULTIPLIER = 1.3
HOME_SFS_MIN_SIZE_GIB = 1


@dataclass(frozen=True)
class SoperatorHomePreservationStatus:
    status: str
    reason: str
    source: str = ""

    @property
    def external(self) -> bool:
        return self.status == "verified"

    @property
    def needs_migration(self) -> bool:
        return self.status == "needs_home_sfs_migration"

    def as_payload(self) -> dict[str, str]:
        payload = {"status": self.status, "reason": self.reason}
        if self.source:
            payload["source"] = self.source
        return payload


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mutable_mapping(parent: MutableMapping[str, Any], key: str) -> MutableMapping[str, Any]:
    value = parent.get(key)
    if isinstance(value, MutableMapping):
        return value
    replacement: dict[str, Any] = {}
    parent[key] = replacement
    return replacement


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _mount_path(value: Any) -> str:
    text = str(value or "").strip()
    return text.rstrip("/") if text != "/" else text


def _home_submounts(values: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    found: list[Mapping[str, Any]] = []
    slurm_nodes = _mapping(values.get("slurmNodes"))
    login_volumes = _mapping(_mapping(slurm_nodes.get("login")).get("volumes"))
    found.extend(_sequence_of_mappings(login_volumes.get("jailSubMounts")))
    for nodeset in _sequence_of_mappings(values.get("nodesets")):
        slurmd_volumes = _mapping(_mapping(nodeset.get("slurmd")).get("volumes"))
        found.extend(_sequence_of_mappings(slurmd_volumes.get("jailSubMounts")))
    return tuple(
        item for item in found if _mount_path(item.get("mountPath")) == HOME_SFS_MOUNT_PATH
    )


def _home_volume_source_names(values: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in _sequence_of_mappings(values.get("volumeSources")):
        name = str(item.get("name", "") or "").strip()
        if name:
            names.add(name)
    return names


def soperator_home_preservation_status(
    values: Mapping[str, Any],
) -> SoperatorHomePreservationStatus:
    """Classify whether /home is already outside the jail rootfs from chart values."""

    external_nfs = _mapping(values.get("externalNfs"))
    if _truthy(external_nfs.get("enabled")) and (
        _mount_path(external_nfs.get("mountPath") or HOME_SFS_MOUNT_PATH) == HOME_SFS_MOUNT_PATH
    ):
        server = str(external_nfs.get("server", "") or "").strip()
        path = str(external_nfs.get("path", "") or "").strip()
        return SoperatorHomePreservationStatus(
            status="verified",
            reason="/home is mounted through values.externalNfs",
            source=f"externalNfs:{server}:{path}" if server or path else "externalNfs",
        )

    volume_source_names = _home_volume_source_names(values)
    for submount in _home_submounts(values):
        source = str(submount.get("volumeSourceName") or submount.get("name") or "").strip()
        if source and source != "jail" and (not volume_source_names or source in volume_source_names):
            return SoperatorHomePreservationStatus(
                status="verified",
                reason="/home is configured as a jail submount from a separate volume source",
                source=f"volumeSource:{source}",
            )

    if soperator_home_sfs_migration_enabled(values):
        return SoperatorHomePreservationStatus(
            status="planned",
            reason="/home SFS migration is planned but not yet verified",
            source=HOME_SFS_TARGET_PVC,
        )

    return SoperatorHomePreservationStatus(
        status="needs_home_sfs_migration",
        reason="/home is not proven to be an external jail submount",
        source="jail-rootfs",
    )


def soperator_home_sfs_migration_enabled(values: Mapping[str, Any]) -> bool:
    return _truthy(_mapping(values.get(HOME_SFS_MIGRATION_VALUES_KEY)).get("enabled"))


def normalize_home_sfs_size_multiplier(value: float | int | str | None) -> float:
    if value is None:
        return HOME_SFS_DEFAULT_SIZE_MULTIPLIER
    try:
        multiplier = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("--home-sfs-size-multiplier must be a positive number.") from exc
    if multiplier < 1.0:
        raise ValueError("--home-sfs-size-multiplier must be at least 1.0.")
    return multiplier


def compute_home_sfs_size_gib(
    *,
    usage_bytes: int,
    multiplier: float = HOME_SFS_DEFAULT_SIZE_MULTIPLIER,
    explicit_size_gib: int | None = None,
) -> int:
    if usage_bytes < 0:
        raise ValueError("/home usage bytes must not be negative.")
    usage_gib = max(HOME_SFS_MIN_SIZE_GIB, math.ceil(usage_bytes / (1024**3)))
    computed = max(HOME_SFS_MIN_SIZE_GIB, math.ceil((usage_bytes * multiplier) / (1024**3)))
    if explicit_size_gib is not None:
        if explicit_size_gib < usage_gib:
            raise ValueError(
                "--home-sfs-size-gib must not be smaller than measured /home usage "
                f"({usage_gib} GiB)."
            )
        return explicit_size_gib
    return max(usage_gib, computed)


def _upsert_named_mapping(items: Any, item: Mapping[str, Any]) -> list[dict[str, Any]]:
    name = str(item.get("name", "") or "").strip()
    existing: list[dict[str, Any]] = []
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
        for current in items:
            if isinstance(current, Mapping):
                existing.append(dict(to_plain_data(current)))
    for index, current in enumerate(existing):
        if str(current.get("name", "") or "").strip() == name:
            merged = dict(current)
            merged.update(dict(to_plain_data(item)))
            existing[index] = merged
            return existing
    existing.append(dict(to_plain_data(item)))
    return existing


def _ensure_home_jail_submounts(volumes: MutableMapping[str, Any]) -> None:
    submount = {
        "name": HOME_SFS_SUBMOUNT_NAME,
        "mountPath": HOME_SFS_MOUNT_PATH,
        "volumeSourceName": HOME_SFS_VOLUME_SOURCE_NAME,
        "readOnly": False,
    }
    volumes["jailSubMounts"] = _upsert_named_mapping(volumes.get("jailSubMounts"), submount)


def apply_home_sfs_migration_values(
    values: Mapping[str, Any],
    *,
    target_ref: str,
    size_gib: int,
) -> dict[str, Any]:
    """Return chart values patched to mount a dedicated SFS-backed /home."""

    patched = copy.deepcopy(dict(to_plain_data(values)))
    sfs = _mutable_mapping(patched, "sfs")
    filesystems = _mutable_mapping(sfs, "filesystems")
    home_fs = _mutable_mapping(filesystems, HOME_SFS_KEY)
    home_fs.setdefault("name", f"{target_ref}-{HOME_SFS_KEY}" if target_ref else HOME_SFS_KEY)
    home_fs["size_gib"] = int(size_gib)
    home_fs.setdefault("block_size_kib", HOME_SFS_DEFAULT_BLOCK_SIZE_KIB)
    home_fs.setdefault("mount_tag", HOME_SFS_MOUNT_TAG)
    home_fs.setdefault("forbid_deletion", True)
    home_fs.setdefault("type", HOME_SFS_DEFAULT_TYPE)

    volume = _mutable_mapping(patched, "volume")
    volume["jailSubMounts"] = _upsert_named_mapping(
        volume.get("jailSubMounts"),
        {
            "name": HOME_SFS_SUBMOUNT_NAME,
            "size": f"{int(size_gib)}Gi",
            "filestoreDeviceName": HOME_SFS_MOUNT_TAG,
        },
    )

    patched["volumeSources"] = _upsert_named_mapping(
        patched.get("volumeSources"),
        {
            "name": HOME_SFS_VOLUME_SOURCE_NAME,
            "createPVC": False,
            "storageClassName": "",
            "size": "",
            "persistentVolumeClaim": {
                "claimName": HOME_SFS_TARGET_PVC,
                "readOnly": False,
            },
        },
    )

    slurm_nodes = _mutable_mapping(patched, "slurmNodes")
    login = _mutable_mapping(slurm_nodes, "login")
    login_volumes = _mutable_mapping(login, "volumes")
    _ensure_home_jail_submounts(login_volumes)

    nodesets = patched.get("nodesets")
    if isinstance(nodesets, list):
        for nodeset in nodesets:
            if not isinstance(nodeset, MutableMapping):
                continue
            slurmd = _mutable_mapping(nodeset, "slurmd")
            slurmd_volumes = _mutable_mapping(slurmd, "volumes")
            _ensure_home_jail_submounts(slurmd_volumes)

    migration = _mutable_mapping(patched, HOME_SFS_MIGRATION_VALUES_KEY)
    migration.update(
        {
            "enabled": True,
            "sourcePvc": HOME_SFS_SOURCE_PVC,
            "sourcePath": HOME_SFS_SOURCE_PATH,
            "targetPvc": HOME_SFS_TARGET_PVC,
            "targetPath": HOME_SFS_TARGET_PATH,
            "sizeGib": int(size_gib),
            "status": str(migration.get("status", "planned") or "planned"),
        }
    )
    return patched
