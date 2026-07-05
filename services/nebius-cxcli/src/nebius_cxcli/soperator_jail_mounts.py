"""Persistent jail mount helpers for active/passive Soperator rootfs refresh."""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .runtime_config import to_plain_data

JAIL_PERSISTENT_MOUNTS_VALUES_KEY = "jailPersistentMounts"
JAIL_PERSISTENT_HOME_MOUNT_PATH = "/home"
JAIL_LEGACY_ROOT_PATH = "/mnt/jail"
JAIL_MANAGED_STORE_PATH = "/mnt/jail-store"
JAIL_MANAGED_ROOTFS_PATH = "/mnt/jail-store/rootfs"
JAIL_MANAGED_HOME_LOCAL_PATH = "/mnt/jail-store/shared/home"
JAIL_EXTERNAL_ROOTFS_PATH = "/mnt/jail/.cxcli/rootfs"
JAIL_EXTERNAL_SYSTEM_PATH = "/mnt/jail/.cxcli"
JAIL_EXTERNAL_HOME_LOCAL_PATH = "/mnt/jail/home"
JAIL_ROOTFS_SLOT_A = "slot-a"
JAIL_ROOTFS_SLOT_B = "slot-b"
JAIL_LEGACY_ACTIVE_SOURCE = "legacy-rootfs"


@dataclass(frozen=True)
class JailPersistentMount:
    mount_path: str
    local_path: str

    @property
    def name(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.mount_path.strip("/").lower()).strip("-")
        slug = slug or "root"
        base = f"jail-persistent-{slug}"
        if len(base) <= 52:
            return base
        digest = hashlib.sha1(self.mount_path.encode("utf-8")).hexdigest()[:8]
        return f"{base[:43].rstrip('-')}-{digest}"

    @property
    def pvc_name(self) -> str:
        return f"{self.name}-pvc"

    def as_values(self) -> dict[str, str]:
        return {
            "mountPath": self.mount_path,
            "localPath": self.local_path,
        }

    def as_payload(self) -> dict[str, str]:
        return {
            "name": self.name,
            "mount_path": self.mount_path,
            "local_path": self.local_path,
            "pvc_name": self.pvc_name,
        }


@dataclass(frozen=True)
class JailPersistentMountStatus:
    status: str
    reason: str
    mounts: tuple[JailPersistentMount, ...] = ()

    @property
    def verified(self) -> bool:
        return self.status in {"verified", "planned"}

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "mounts": [mount.as_payload() for mount in self.mounts],
        }


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
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_path(value: Any, *, field: str, allow_root: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required.")
    if not text.startswith("/"):
        raise ValueError(f"{field} must be an absolute path; got {text!r}.")
    if "//" in text:
        raise ValueError(f"{field} must be normalized and must not contain '//'; got {text!r}.")
    path = PurePosixPath(text)
    if ".." in path.parts:
        raise ValueError(f"{field} must not contain '..'; got {text!r}.")
    normalized = path.as_posix()
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    if normalized == "/" and not allow_root:
        raise ValueError(f"{field} must not be '/'.")
    return normalized


def _path_contains(parent: str, child: str) -> bool:
    parent = _normalize_path(parent, field="parent", allow_root=True)
    child = _normalize_path(child, field="child", allow_root=True)
    if parent == "/":
        return True
    return child == parent or child.startswith(f"{parent}/")


def _paths_overlap(first: str, second: str) -> bool:
    return _path_contains(first, second) or _path_contains(second, first)


def _existing_submount_paths(values: Mapping[str, Any]) -> set[str]:
    paths: set[str] = set()
    slurm_nodes = _mapping(values.get("slurmNodes"))
    login_volumes = _mapping(_mapping(slurm_nodes.get("login")).get("volumes"))
    for item in _sequence_of_mappings(login_volumes.get("jailSubMounts")):
        mount_path = str(item.get("mountPath") or "").strip()
        if mount_path:
            paths.add(_normalize_path(mount_path, field="jailSubMount.mountPath"))
    for nodeset in _sequence_of_mappings(values.get("nodesets")):
        volumes = _mapping(_mapping(nodeset.get("slurmd")).get("volumes"))
        for item in _sequence_of_mappings(volumes.get("jailSubMounts")):
            mount_path = str(item.get("mountPath") or "").strip()
            if mount_path:
                paths.add(_normalize_path(mount_path, field="jailSubMount.mountPath"))
    external_nfs = _mapping(values.get("externalNfs"))
    if _truthy(external_nfs.get("enabled")):
        mount_path = str(external_nfs.get("mountPath") or JAIL_PERSISTENT_HOME_MOUNT_PATH).strip()
        if mount_path:
            paths.add(_normalize_path(mount_path, field="externalNfs.mountPath"))
    return paths


def parse_jail_persistent_mount_spec(value: str) -> JailPersistentMount:
    raw = str(value or "").strip()
    if "=" not in raw:
        raise ValueError(
            "--jail-persistent-mount must use <mountPath>=<localPath>, "
            f"for example /data=/mnt/jail/data; got {value!r}."
        )
    mount_path, local_path = raw.split("=", 1)
    return JailPersistentMount(
        mount_path=_normalize_path(mount_path, field="jailPersistentMount.mountPath"),
        local_path=_normalize_path(local_path, field="jailPersistentMount.localPath"),
    )


def parse_jail_persistent_mount_specs(values: Sequence[str] | None) -> tuple[JailPersistentMount, ...]:
    if not values:
        return ()
    return tuple(parse_jail_persistent_mount_spec(value) for value in values)


def normalize_jail_persistent_mounts(
    mounts: Sequence[JailPersistentMount | Mapping[str, Any]],
    *,
    values: Mapping[str, Any] | None = None,
    include_home: bool = True,
    home_local_path: str = JAIL_EXTERNAL_HOME_LOCAL_PATH,
    store_path: str = JAIL_LEGACY_ROOT_PATH,
    rootfs_path: str = JAIL_EXTERNAL_ROOTFS_PATH,
    system_path: str = JAIL_EXTERNAL_SYSTEM_PATH,
) -> tuple[JailPersistentMount, ...]:
    existing_paths = _existing_submount_paths(values or {})
    normalized: list[JailPersistentMount] = []
    explicit_home = False
    for item in mounts:
        raw_mount_path = item.mount_path if isinstance(item, JailPersistentMount) else item.get("mountPath")
        try:
            explicit_home = (
                _normalize_path(raw_mount_path, field="jailPersistentMount.mountPath")
                == JAIL_PERSISTENT_HOME_MOUNT_PATH
            )
        except ValueError:
            explicit_home = False
        if explicit_home:
            break
    if include_home and not explicit_home and JAIL_PERSISTENT_HOME_MOUNT_PATH not in existing_paths:
        normalized.append(
            JailPersistentMount(
                mount_path=JAIL_PERSISTENT_HOME_MOUNT_PATH,
                local_path=_normalize_path(home_local_path, field="jailPersistentMount.localPath"),
            )
        )
    for item in mounts:
        if isinstance(item, JailPersistentMount):
            mount = item
        else:
            mount = JailPersistentMount(
                mount_path=_normalize_path(
                    item.get("mountPath"),
                    field="jailPersistentMount.mountPath",
                ),
                local_path=_normalize_path(
                    item.get("localPath"),
                    field="jailPersistentMount.localPath",
                ),
            )
        normalized.append(mount)

    seen: set[str] = set()
    result: list[JailPersistentMount] = []
    store_path = _normalize_path(store_path, field="jailRootfs.store.mountPath")
    system_paths = (
        _normalize_path(system_path, field="jailRootfs.store.systemPath"),
        _normalize_path(rootfs_path, field="jailRootfs.store.rootfsPath"),
        f"{rootfs_path.rstrip('/')}/{JAIL_ROOTFS_SLOT_A}",
        f"{rootfs_path.rstrip('/')}/{JAIL_ROOTFS_SLOT_B}",
    )
    for mount in normalized:
        mount_path = _normalize_path(mount.mount_path, field="jailPersistentMount.mountPath")
        local_path = _normalize_path(mount.local_path, field="jailPersistentMount.localPath")
        if mount_path in seen:
            raise ValueError(f"duplicate jailPersistentMounts mountPath {mount_path!r}.")
        seen.add(mount_path)
        if not _path_contains(store_path, local_path):
            raise ValueError(
                "jailPersistentMounts.localPath must be inside the physical jail store "
                f"{store_path!r}; got {local_path!r}."
            )
        for blocked_path in system_paths:
            if _paths_overlap(local_path, blocked_path):
                raise ValueError(
                    "jailPersistentMounts.localPath must not overlap active/passive "
                    f"rootfs or cxcli system paths; got {local_path!r} overlapping {blocked_path!r}."
                )
        result.append(JailPersistentMount(mount_path=mount_path, local_path=local_path))
    return tuple(result)


def _slot_values(rootfs_path: str, slot: str) -> dict[str, str]:
    return {
        "volumeSourceName": f"jail-rootfs-{slot}",
        "pvcName": f"jail-rootfs-{slot}-pvc",
        "localPath": f"{rootfs_path.rstrip('/')}/{slot}",
    }


def apply_jail_persistent_mount_values(
    values: Mapping[str, Any],
    *,
    target_ref: str,
    persistent_mounts: Sequence[JailPersistentMount | Mapping[str, Any]] = (),
    layout: str = "external",
) -> dict[str, Any]:
    """Return Soperator chart values patched for single-SFS active/passive rootfs slots."""

    del target_ref
    if layout not in {"managed", "external"}:
        raise ValueError("layout must be managed or external.")
    managed = layout == "managed"
    store_path = JAIL_MANAGED_STORE_PATH if managed else JAIL_LEGACY_ROOT_PATH
    rootfs_path = JAIL_MANAGED_ROOTFS_PATH if managed else JAIL_EXTERNAL_ROOTFS_PATH
    system_path = f"{store_path}/.cxcli" if managed else JAIL_EXTERNAL_SYSTEM_PATH
    home_local_path = JAIL_MANAGED_HOME_LOCAL_PATH if managed else JAIL_EXTERNAL_HOME_LOCAL_PATH

    patched = copy.deepcopy(dict(to_plain_data(values)))
    jail_rootfs = _mutable_mapping(patched, "jailRootfs")
    jail_rootfs["strategy"] = "activePassive"
    jail_rootfs.setdefault("activeSlot", JAIL_ROOTFS_SLOT_A)
    jail_rootfs.setdefault("passiveSlot", JAIL_ROOTFS_SLOT_B)
    jail_rootfs.pop("home", None)
    store = _mutable_mapping(jail_rootfs, "store")
    store["mountPath"] = store_path
    store["rootfsPath"] = rootfs_path
    store.setdefault("volumeKey", "jail")
    slots = _mutable_mapping(jail_rootfs, "slots")
    for slot in (JAIL_ROOTFS_SLOT_A, JAIL_ROOTFS_SLOT_B):
        slot_values = _mutable_mapping(slots, slot)
        slot_values.update(_slot_values(rootfs_path, slot))
    if not managed:
        adoption = _mutable_mapping(jail_rootfs, "adoption")
        adoption.setdefault("activeSource", JAIL_LEGACY_ACTIVE_SOURCE)
        adoption.setdefault("rollbackSource", JAIL_LEGACY_ACTIVE_SOURCE)

    mounts = normalize_jail_persistent_mounts(
        persistent_mounts,
        values=patched,
        include_home=True,
        home_local_path=home_local_path,
        store_path=store_path,
        rootfs_path=rootfs_path,
        system_path=system_path,
    )
    patched[JAIL_PERSISTENT_MOUNTS_VALUES_KEY] = [mount.as_values() for mount in mounts]
    patched.pop("jail_home", None)
    return patched


def jail_persistent_mount_status(values: Mapping[str, Any]) -> JailPersistentMountStatus:
    store = _mapping(_mapping(values.get("jailRootfs")).get("store"))
    store_path = str(store.get("mountPath") or JAIL_LEGACY_ROOT_PATH)
    rootfs_path = str(store.get("rootfsPath") or JAIL_EXTERNAL_ROOTFS_PATH)
    system_path = f"{store_path.rstrip('/')}/.cxcli"
    configured = normalize_jail_persistent_mounts(
        _sequence_of_mappings(values.get(JAIL_PERSISTENT_MOUNTS_VALUES_KEY)),
        values=values,
        include_home=False,
        store_path=store_path,
        rootfs_path=rootfs_path,
        system_path=system_path,
    )
    existing_paths = _existing_submount_paths(values)
    has_home = any(mount.mount_path == JAIL_PERSISTENT_HOME_MOUNT_PATH for mount in configured)
    if has_home:
        return JailPersistentMountStatus(
            status="planned",
            reason="/home is configured as a persistent jail mount",
            mounts=configured,
        )
    if JAIL_PERSISTENT_HOME_MOUNT_PATH in existing_paths:
        return JailPersistentMountStatus(
            status="verified",
            reason="/home is already provided by an existing customer-owned jail submount",
            mounts=configured,
        )
    return JailPersistentMountStatus(
        status="blocked",
        reason="/home is not configured as a persistent jail mount",
        mounts=configured,
    )


def jail_rootfs_uses_legacy_active_source(values: Mapping[str, Any]) -> bool:
    adoption = _mapping(_mapping(values.get("jailRootfs")).get("adoption"))
    return str(adoption.get("activeSource") or "").strip() == JAIL_LEGACY_ACTIVE_SOURCE


def jail_persistent_mount_exclude_paths(values: Mapping[str, Any]) -> tuple[str, ...]:
    paths = [JAIL_EXTERNAL_SYSTEM_PATH]
    for mount in _sequence_of_mappings(values.get(JAIL_PERSISTENT_MOUNTS_VALUES_KEY)):
        local_path = str(mount.get("localPath") or "").strip()
        if local_path:
            paths.append(_normalize_path(local_path, field="jailPersistentMount.localPath"))
    return tuple(dict.fromkeys(paths))
