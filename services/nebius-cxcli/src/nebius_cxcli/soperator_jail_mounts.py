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
JAIL_MANAGED_SYSTEM_PATH = "/mnt/jail-store/.cxcli"
JAIL_MANAGED_HOME_LOCAL_PATH = "/mnt/jail-store/shared/home"
JAIL_EXTERNAL_ROOTFS_PATH = "/mnt/jail/.cxcli/rootfs"
JAIL_EXTERNAL_SYSTEM_PATH = "/mnt/jail/.cxcli"
JAIL_EXTERNAL_HOME_LOCAL_PATH = "/mnt/jail/shared/home"
JAIL_ROOTFS_SLOT_A = "slot-a"
JAIL_ROOTFS_SLOT_B = "slot-b"
JAIL_LEGACY_ACTIVE_SOURCE = "legacy-rootfs"
JAIL_EXTERNAL_DEFAULT_SHARED_MOUNT_PATHS = (
    "/data",
    "/scripts",
    "/models",
)
JAIL_EXTERNAL_AUTO_PERSISTENT_MOUNT_PATHS = (
    JAIL_PERSISTENT_HOME_MOUNT_PATH,
    *JAIL_EXTERNAL_DEFAULT_SHARED_MOUNT_PATHS,
)
JAIL_MANAGED_DEFAULT_SHARED_MOUNT_PATHS = JAIL_EXTERNAL_DEFAULT_SHARED_MOUNT_PATHS
JAIL_MANAGED_AUTO_PERSISTENT_MOUNT_PATHS = JAIL_EXTERNAL_AUTO_PERSISTENT_MOUNT_PATHS


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


def jail_existing_submount_paths(values: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(_existing_submount_paths(values)))


def parse_jail_persistent_mount_spec(value: str) -> JailPersistentMount:
    raw = str(value or "").strip()
    if "=" not in raw:
        raise ValueError(
            "--jail-persistent-mount must use <mountPath>=<localPath>, "
            f"for example /data=/mnt/jail/shared/data; got {value!r}."
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


def jail_persistent_mount_decisions(
    *,
    original_values: Mapping[str, Any],
    patched_values: Mapping[str, Any],
    explicit_mounts: Sequence[JailPersistentMount | Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    existing_paths = set(jail_existing_submount_paths(original_values))
    store = _mapping(_mapping(patched_values.get("jailRootfs")).get("store"))
    store_path = _normalize_path(
        store.get("mountPath") or JAIL_LEGACY_ROOT_PATH,
        field="jailRootfs.store.mountPath",
    )
    explicit_by_path = {
        _normalize_path(mount.mount_path, field="jailPersistentMount.mountPath"): mount
        for mount in (_coerce_jail_persistent_mount(item) for item in explicit_mounts)
    }
    configured_by_path: dict[str, Mapping[str, Any]] = {}
    for item in _sequence_of_mappings(patched_values.get(JAIL_PERSISTENT_MOUNTS_VALUES_KEY)):
        mount_path = str(item.get("mountPath") or "").strip()
        if not mount_path:
            continue
        configured_by_path[_normalize_path(mount_path, field="jailPersistentMount.mountPath")] = item

    ordered_paths: list[str] = []
    for path in (
        *JAIL_EXTERNAL_AUTO_PERSISTENT_MOUNT_PATHS,
        *sorted(existing_paths),
        *explicit_by_path,
    ):
        normalized = _normalize_path(path, field="jailPersistentMount.mountPath")
        if normalized not in ordered_paths:
            ordered_paths.append(normalized)

    decisions: list[dict[str, Any]] = []
    for mount_path in ordered_paths:
        configured = configured_by_path.get(mount_path)
        explicit = explicit_by_path.get(mount_path)
        existing_submount = mount_path in existing_paths and configured is None
        if existing_submount:
            decisions.append(
                {
                    "mount_path": mount_path,
                    "status": "existing-submount",
                    "source_status": "existing-submount",
                    "origin": "existing-submount",
                    "copy_required": False,
                }
            )
            continue
        if configured is None:
            continue
        local_path = _normalize_path(
            configured.get("localPath"),
            field="jailPersistentMount.localPath",
        )
        status = "explicit" if explicit is not None else "pending-probe"
        source_status = "explicit" if explicit is not None else "pending-probe"
        decisions.append(
            {
                "mount_path": mount_path,
                "local_path": local_path,
                "status": status,
                "source_status": source_status,
                "origin": "explicit" if explicit is not None else "auto",
                "source_path": f"{store_path}{mount_path}",
                "target_local_path": local_path,
                "copy_required": True,
            }
        )
    return tuple(decisions)


def _coerce_jail_persistent_mount(
    item: JailPersistentMount | Mapping[str, Any],
) -> JailPersistentMount:
    if isinstance(item, JailPersistentMount):
        return item
    return JailPersistentMount(
        mount_path=_normalize_path(
            item.get("mountPath"),
            field="jailPersistentMount.mountPath",
        ),
        local_path=_normalize_path(
            item.get("localPath"),
            field="jailPersistentMount.localPath",
        ),
    )


def external_default_jail_persistent_mounts() -> tuple[JailPersistentMount, ...]:
    return tuple(
        JailPersistentMount(
            mount_path=mount_path,
            local_path=f"{JAIL_LEGACY_ROOT_PATH}/shared/{mount_path.strip('/')}",
        )
        for mount_path in JAIL_EXTERNAL_DEFAULT_SHARED_MOUNT_PATHS
    )


def managed_default_jail_persistent_mounts() -> tuple[JailPersistentMount, ...]:
    return tuple(
        JailPersistentMount(
            mount_path=mount_path,
            local_path=f"{JAIL_MANAGED_STORE_PATH}/shared/{mount_path.strip('/')}",
        )
        for mount_path in JAIL_MANAGED_DEFAULT_SHARED_MOUNT_PATHS
    )


def normalize_jail_persistent_mounts(
    mounts: Sequence[JailPersistentMount | Mapping[str, Any]],
    *,
    values: Mapping[str, Any] | None = None,
    include_home: bool = True,
    default_mounts: Sequence[JailPersistentMount | Mapping[str, Any]] = (),
    home_local_path: str = JAIL_EXTERNAL_HOME_LOCAL_PATH,
    store_path: str = JAIL_LEGACY_ROOT_PATH,
    rootfs_path: str = JAIL_EXTERNAL_ROOTFS_PATH,
    system_path: str = JAIL_EXTERNAL_SYSTEM_PATH,
) -> tuple[JailPersistentMount, ...]:
    existing_paths = _existing_submount_paths(values or {})
    normalized: list[JailPersistentMount] = []
    explicit_mounts = [_coerce_jail_persistent_mount(item) for item in mounts]
    explicit_seen: set[str] = set()
    for mount in explicit_mounts:
        mount_path = _normalize_path(mount.mount_path, field="jailPersistentMount.mountPath")
        if mount_path in explicit_seen:
            raise ValueError(f"duplicate jailPersistentMounts mountPath {mount_path!r}.")
        explicit_seen.add(mount_path)
    explicit_by_path = {
        _normalize_path(mount.mount_path, field="jailPersistentMount.mountPath"): mount
        for mount in explicit_mounts
    }
    if (
        include_home
        and JAIL_PERSISTENT_HOME_MOUNT_PATH not in existing_paths
    ):
        normalized.append(
            explicit_by_path.pop(
                JAIL_PERSISTENT_HOME_MOUNT_PATH,
                JailPersistentMount(
                    mount_path=JAIL_PERSISTENT_HOME_MOUNT_PATH,
                    local_path=_normalize_path(
                        home_local_path,
                        field="jailPersistentMount.localPath",
                    ),
                ),
            )
        )
    for item in default_mounts:
        mount = _coerce_jail_persistent_mount(item)
        mount_path = _normalize_path(mount.mount_path, field="jailPersistentMount.mountPath")
        if mount_path in existing_paths:
            continue
        normalized.append(explicit_by_path.pop(mount_path, mount))
    emitted_paths = {
        _normalize_path(mount.mount_path, field="jailPersistentMount.mountPath")
        for mount in normalized
    }
    for mount in explicit_mounts:
        mount_path = _normalize_path(mount.mount_path, field="jailPersistentMount.mountPath")
        if mount_path in emitted_paths:
            continue
        normalized.append(mount)
        emitted_paths.add(mount_path)

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


def _upsert_pvc_volume_source(
    volume_sources: list[Any],
    *,
    name: str,
    pvc_name: str,
) -> None:
    entry: MutableMapping[str, Any] | None = None
    duplicate_indexes: list[int] = []
    for index, item in enumerate(volume_sources):
        if not isinstance(item, MutableMapping) or str(item.get("name") or "").strip() != name:
            continue
        if entry is None:
            entry = item
        else:
            duplicate_indexes.append(index)
    for index in reversed(duplicate_indexes):
        del volume_sources[index]
    if entry is None:
        entry = {}
        volume_sources.append(entry)

    entry["name"] = name
    entry["persistentVolumeClaim"] = {"claimName": pvc_name, "readOnly": False}
    entry["createPVC"] = False
    entry["size"] = ""
    entry["storageClassName"] = ""
    for source_key in ("configMap", "emptyDir", "hostPath", "nfs", "secret"):
        entry.pop(source_key, None)


def _existing_pvc_volume_source_claim_name(volume_sources: Sequence[Any], name: str) -> str:
    for item in volume_sources:
        if not isinstance(item, Mapping) or str(item.get("name") or "").strip() != name:
            continue
        claim_name = str(
            _mapping(item.get("persistentVolumeClaim")).get("claimName") or ""
        ).strip()
        if claim_name:
            return claim_name
    return ""


def _referenced_controller_spool_volume_sources(values: Mapping[str, Any]) -> tuple[str, ...]:
    slurm_nodes = _mapping(values.get("slurmNodes"))
    controller = _mapping(slurm_nodes.get("controller"))
    volumes = _mapping(controller.get("volumes"))
    spool = _mapping(volumes.get("spool"))
    name = str(spool.get("volumeSourceName") or "").strip()
    return (name,) if name else ()


def sync_jail_volume_sources(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return values with SlurmCluster volumeSources aligned to jail rootfs state."""

    patched = copy.deepcopy(dict(to_plain_data(values)))
    jail_rootfs = _mutable_mapping(patched, "jailRootfs")
    jail_rootfs["strategy"] = str(jail_rootfs.get("strategy") or "activePassive").strip()
    active_slot = str(jail_rootfs.get("activeSlot") or JAIL_ROOTFS_SLOT_A).strip()
    if active_slot not in {JAIL_ROOTFS_SLOT_A, JAIL_ROOTFS_SLOT_B}:
        raise ValueError(f"jailRootfs.activeSlot must be slot-a or slot-b; got {active_slot!r}.")
    passive_slot = str(
        jail_rootfs.get("passiveSlot")
        or (JAIL_ROOTFS_SLOT_B if active_slot == JAIL_ROOTFS_SLOT_A else JAIL_ROOTFS_SLOT_A)
    ).strip()
    if passive_slot not in {JAIL_ROOTFS_SLOT_A, JAIL_ROOTFS_SLOT_B}:
        raise ValueError(f"jailRootfs.passiveSlot must be slot-a or slot-b; got {passive_slot!r}.")
    jail_rootfs["activeSlot"] = active_slot
    jail_rootfs["passiveSlot"] = passive_slot

    store = _mutable_mapping(jail_rootfs, "store")
    store_path = str(store.get("mountPath") or JAIL_LEGACY_ROOT_PATH).strip()
    rootfs_path = str(store.get("rootfsPath") or JAIL_EXTERNAL_ROOTFS_PATH).strip()
    system_path = str(store.get("systemPath") or f"{store_path.rstrip('/')}/.cxcli").strip()
    volume_key = str(store.get("volumeKey") or "jail").strip() or "jail"
    store["mountPath"] = store_path
    store["rootfsPath"] = rootfs_path
    store["volumeKey"] = volume_key

    volume_sources = patched.get("volumeSources")
    if not isinstance(volume_sources, list):
        volume_sources = []
        patched["volumeSources"] = volume_sources

    slots = _mutable_mapping(jail_rootfs, "slots")
    slot_pvcs: dict[str, str] = {}
    chart_rendered_source_names: set[str] = set()
    for slot in (JAIL_ROOTFS_SLOT_A, JAIL_ROOTFS_SLOT_B):
        defaults = _slot_values(rootfs_path, slot)
        slot_values = _mutable_mapping(slots, slot)
        volume_source_name = str(
            slot_values.get("volumeSourceName") or defaults["volumeSourceName"]
        ).strip()
        pvc_name = str(slot_values.get("pvcName") or defaults["pvcName"]).strip()
        local_path = str(slot_values.get("localPath") or defaults["localPath"]).strip()
        slot_values["volumeSourceName"] = volume_source_name
        slot_values["pvcName"] = pvc_name
        slot_values["localPath"] = local_path
        slot_pvcs[slot] = pvc_name
        chart_rendered_source_names.add(volume_source_name)

    for mount in normalize_jail_persistent_mounts(
        _sequence_of_mappings(patched.get(JAIL_PERSISTENT_MOUNTS_VALUES_KEY)),
        values=patched,
        include_home=False,
        store_path=store_path,
        rootfs_path=rootfs_path,
        system_path=system_path,
    ):
        chart_rendered_source_names.add(mount.name)

    # The chart renders active/passive slot and persistent-mount volumeSources
    # from jailRootfs/jailPersistentMounts. Values only need the legacy `jail`
    # alias consumed by populate-jail before the active slot switch.
    volume_sources[:] = [
        item
        for item in volume_sources
        if not (
            isinstance(item, Mapping)
            and str(item.get("name") or "").strip() in chart_rendered_source_names
            and str(item.get("name") or "").strip() != volume_key
        )
    ]
    _upsert_pvc_volume_source(
        volume_sources,
        name=volume_key,
        pvc_name=slot_pvcs[active_slot],
    )
    for source_name in _referenced_controller_spool_volume_sources(patched):
        _upsert_pvc_volume_source(
            volume_sources,
            name=source_name,
            pvc_name=(
                _existing_pvc_volume_source_claim_name(volume_sources, source_name)
                or f"{source_name}-pvc"
            ),
        )
    return patched


def apply_jail_persistent_mount_values(
    values: Mapping[str, Any],
    *,
    target_ref: str,
    persistent_mounts: Sequence[JailPersistentMount | Mapping[str, Any]] = (),
    layout: str = "external",
    include_default_shared_mounts: bool | None = None,
    legacy_active_source: bool | None = None,
) -> dict[str, Any]:
    """Return Soperator chart values patched for single-SFS active/passive rootfs slots."""

    del target_ref
    if layout not in {"managed", "external"}:
        raise ValueError("layout must be managed or external.")
    managed = layout == "managed"
    store_path = JAIL_MANAGED_STORE_PATH if managed else JAIL_LEGACY_ROOT_PATH
    rootfs_path = JAIL_MANAGED_ROOTFS_PATH if managed else JAIL_EXTERNAL_ROOTFS_PATH
    system_path = JAIL_MANAGED_SYSTEM_PATH if managed else JAIL_EXTERNAL_SYSTEM_PATH
    home_local_path = JAIL_MANAGED_HOME_LOCAL_PATH if managed else JAIL_EXTERNAL_HOME_LOCAL_PATH
    if include_default_shared_mounts is None:
        include_default_shared_mounts = not managed
    if legacy_active_source is None:
        legacy_active_source = not managed

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
    if managed:
        volume = _mutable_mapping(patched, "volume")
        jail_volume = _mutable_mapping(volume, "jail")
        jail_volume["localPath"] = store_path
    slots = _mutable_mapping(jail_rootfs, "slots")
    for slot in (JAIL_ROOTFS_SLOT_A, JAIL_ROOTFS_SLOT_B):
        slot_values = _mutable_mapping(slots, slot)
        slot_values.update(_slot_values(rootfs_path, slot))
    if legacy_active_source:
        adoption = _mutable_mapping(jail_rootfs, "adoption")
        adoption.setdefault("activeSource", JAIL_LEGACY_ACTIVE_SOURCE)
        adoption.setdefault("rollbackSource", JAIL_LEGACY_ACTIVE_SOURCE)
    elif "adoption" in jail_rootfs:
        adoption = jail_rootfs.get("adoption")
        if isinstance(adoption, MutableMapping) and not adoption:
            jail_rootfs.pop("adoption", None)

    default_mounts = (
        managed_default_jail_persistent_mounts()
        if managed
        else external_default_jail_persistent_mounts()
    ) if include_default_shared_mounts else ()
    configured_mounts = _sequence_of_mappings(patched.get(JAIL_PERSISTENT_MOUNTS_VALUES_KEY))
    mounts = normalize_jail_persistent_mounts(
        (*configured_mounts, *persistent_mounts),
        values=patched,
        include_home=True,
        default_mounts=default_mounts,
        home_local_path=home_local_path,
        store_path=store_path,
        rootfs_path=rootfs_path,
        system_path=system_path,
    )
    patched[JAIL_PERSISTENT_MOUNTS_VALUES_KEY] = [mount.as_values() for mount in mounts]
    patched.pop("jail_home", None)
    return sync_jail_volume_sources(patched)


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
    store = _mapping(_mapping(values.get("jailRootfs")).get("store"))
    store_path = str(store.get("mountPath") or JAIL_LEGACY_ROOT_PATH).strip()
    system_path = str(store.get("systemPath") or f"{store_path.rstrip('/')}/.cxcli").strip()
    paths = [system_path]
    for mount in _sequence_of_mappings(values.get(JAIL_PERSISTENT_MOUNTS_VALUES_KEY)):
        local_path = str(mount.get("localPath") or "").strip()
        if local_path:
            paths.append(_normalize_path(local_path, field="jailPersistentMount.localPath"))
    return tuple(dict.fromkeys(paths))
