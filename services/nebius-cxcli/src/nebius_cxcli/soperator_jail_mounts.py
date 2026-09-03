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
JAIL_EXTERNAL_HOME_LOCAL_PATH = "/mnt/jail/home"
JAIL_ROOTFS_SLOT_A = "slot-a"
JAIL_ROOTFS_SLOT_B = "slot-b"
JAIL_LEGACY_ACTIVE_SOURCE = "legacy-rootfs"
JAIL_DEFAULT_SHARED_MOUNT_PATHS = (
    "/data",
    "/scripts",
    "/models",
)
JAIL_MANDATORY_PERSISTENT_MOUNT_PATHS = (
    JAIL_PERSISTENT_HOME_MOUNT_PATH,
    *JAIL_DEFAULT_SHARED_MOUNT_PATHS,
)
JAIL_EXTERNAL_AUTO_PERSISTENT_MOUNT_PATHS = JAIL_MANDATORY_PERSISTENT_MOUNT_PATHS
JAIL_FORBIDDEN_OPTIONAL_MOUNT_ROOTS = (
    "/dev",
    "/proc",
    "/run",
    "/sys",
    "/tmp",
)
_SAFE_POSIX_PATH_RE = re.compile(r"^/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$")


def _mashed_kebab(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"([a-z])([A-Z])", r"\1-\2", text)
    text = re.sub(r"([A-Z])([A-Z][a-z])", r"\1-\2", text).lower()
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", text)).strip("-")


def legacy_jail_pvc_name(values: Mapping[str, Any]) -> str:
    jail_rootfs = _mapping(values.get("jailRootfs"))
    adoption = _mapping(jail_rootfs.get("adoption"))
    explicit = str(adoption.get("legacyPvcName") or "").strip()
    if explicit:
        return explicit
    jail_volume = _mapping(_mapping(values.get("volume")).get("jail"))
    volume_name = _mashed_kebab(jail_volume.get("name") or "jail") or "jail"
    return f"{volume_name}-pvc"


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
    if normalized != "/" and not _SAFE_POSIX_PATH_RE.fullmatch(normalized):
        raise ValueError(
            f"{field} must use shell-safe path components containing only "
            f"letters, digits, '.', '_', or '-'; got {text!r}."
        )
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
            local_path=f"{JAIL_LEGACY_ROOT_PATH}/{mount_path.strip('/')}",
        )
        for mount_path in JAIL_DEFAULT_SHARED_MOUNT_PATHS
    )


def managed_default_jail_persistent_mounts() -> tuple[JailPersistentMount, ...]:
    return tuple(
        JailPersistentMount(
            mount_path=mount_path,
            local_path=f"{JAIL_MANAGED_STORE_PATH}/shared/{mount_path.strip('/')}",
        )
        for mount_path in JAIL_DEFAULT_SHARED_MOUNT_PATHS
    )


def managed_legacy_default_jail_persistent_mounts() -> tuple[JailPersistentMount, ...]:
    """Bind first-adoption data paths directly below the retained legacy store."""

    return tuple(
        JailPersistentMount(
            mount_path=mount_path,
            local_path=f"{JAIL_MANAGED_STORE_PATH}/{mount_path.strip('/')}",
        )
        for mount_path in JAIL_DEFAULT_SHARED_MOUNT_PATHS
    )


def jail_persistent_mounts_from_paths(
    paths: Sequence[str], *, layout: str, legacy_active_source: bool = False
) -> tuple[JailPersistentMount, ...]:
    """Derive layout-owned backing paths for optional persistent data directories."""

    if layout not in {"managed", "external"}:
        raise ValueError("layout must be managed or external.")
    store_path = JAIL_LEGACY_ROOT_PATH
    if layout == "managed":
        store_path = (
            JAIL_MANAGED_STORE_PATH if legacy_active_source else f"{JAIL_MANAGED_STORE_PATH}/shared"
        )
    result: list[JailPersistentMount] = []
    for value in paths:
        mount_path = _normalize_path(value, field="persistent data path")
        if mount_path in {"/usr", "/opt", "/etc"}:
            raise ValueError(
                f"persistent data path {mount_path!r} is an image-owned system root; "
                "select a dedicated data subdirectory instead."
            )
        if any(_path_contains(root, mount_path) for root in JAIL_FORBIDDEN_OPTIONAL_MOUNT_ROOTS):
            raise ValueError(f"persistent data path {mount_path!r} is inside a runtime filesystem.")
        result.append(
            JailPersistentMount(
                mount_path=mount_path,
                local_path=f"{store_path.rstrip('/')}/{mount_path.strip('/')}",
            )
        )
    return tuple(result)


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
    if include_home and JAIL_PERSISTENT_HOME_MOUNT_PATH not in existing_paths:
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
    seen_names: set[str] = set()
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
        if mount_path in {"/usr", "/opt", "/etc"}:
            raise ValueError(
                f"jailPersistentMounts.mountPath {mount_path!r} is an image-owned system root."
            )
        if any(_path_contains(root, mount_path) for root in JAIL_FORBIDDEN_OPTIONAL_MOUNT_ROOTS):
            raise ValueError(
                f"jailPersistentMounts.mountPath {mount_path!r} is inside a runtime filesystem."
            )
        if mount_path in existing_paths:
            continue
        for existing_path in existing_paths:
            if _paths_overlap(mount_path, existing_path):
                raise ValueError(
                    "jailPersistentMounts.mountPath must not overlap an existing jail submount; "
                    f"got {mount_path!r} overlapping {existing_path!r}."
                )
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
        for existing_mount in result:
            if _paths_overlap(mount_path, existing_mount.mount_path):
                raise ValueError(
                    "jailPersistentMounts.mountPath values must not overlap; "
                    f"got {mount_path!r} overlapping {existing_mount.mount_path!r}."
                )
            if _paths_overlap(local_path, existing_mount.local_path):
                raise ValueError(
                    "jailPersistentMounts.localPath values must not overlap; "
                    f"got {local_path!r} overlapping {existing_mount.local_path!r}."
                )
        normalized_mount = JailPersistentMount(mount_path=mount_path, local_path=local_path)
        if normalized_mount.name in seen_names:
            raise ValueError(
                "jailPersistentMounts paths must generate distinct volume/PVC names; "
                f"{mount_path!r} collides at {normalized_mount.name!r}."
            )
        seen_names.add(normalized_mount.name)
        result.append(normalized_mount)
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

    entry.clear()
    entry.update(
        {
            "name": name,
            "persistentVolumeClaim": {"claimName": pvc_name, "readOnly": False},
            "createPVC": False,
            "size": "",
            "storageClassName": "",
        }
    )


def _existing_pvc_volume_source_claim_name(volume_sources: Sequence[Any], name: str) -> str:
    for item in volume_sources:
        if not isinstance(item, Mapping) or str(item.get("name") or "").strip() != name:
            continue
        claim_name = str(_mapping(item.get("persistentVolumeClaim")).get("claimName") or "").strip()
        if claim_name:
            return claim_name
    return ""


def _referenced_controller_spool_volume_sources(values: Mapping[str, Any]) -> tuple[str, ...]:
    slurm_nodes = _mapping(values.get("slurmNodes"))
    controller = _mapping(slurm_nodes.get("controller"))
    volumes = _mapping(controller.get("volumes"))
    spool = _mapping(volumes.get("spool"))
    names: list[str] = []
    explicit_name = str(spool.get("volumeSourceName") or "").strip()
    if explicit_name:
        names.append(explicit_name)
    volume = _mapping(values.get("volume"))
    controller_spool = _mapping(volume.get("controllerSpool"))
    default_name = str(controller_spool.get("name") or "controller-spool").strip()
    if default_name and default_name not in names:
        names.append(default_name)
    return tuple(names)


def sync_jail_volume_sources(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return values with SlurmCluster volumeSources aligned to jail rootfs state."""

    patched = copy.deepcopy(dict(to_plain_data(values)))
    jail_rootfs_active_source(patched)
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

    adoption = _mapping(jail_rootfs.get("adoption"))
    legacy_active = str(adoption.get("activeSource") or "").strip() == JAIL_LEGACY_ACTIVE_SOURCE
    legacy_pvc_name = legacy_jail_pvc_name(patched)
    if legacy_active and not legacy_pvc_name:
        raise ValueError("jailRootfs.adoption.legacyPvcName must not be empty.")

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
    # from jailRootfs/jailPersistentMounts. During first adoption, the legacy
    # `jail` alias and all login/worker consumers must remain on the discovered
    # legacy PVC until the persistent mount identities and passive-slot
    # population have both completed. After the switch, the active slot becomes
    # canonical.
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
        pvc_name=legacy_pvc_name if legacy_active else slot_pvcs[active_slot],
    )

    active_volume_source = (
        volume_key
        if legacy_active
        else str(
            _mapping(slots.get(active_slot)).get("volumeSourceName") or f"jail-rootfs-{active_slot}"
        ).strip()
    )
    active_pvc_name = legacy_pvc_name if legacy_active else slot_pvcs[active_slot]
    slurm_nodes = patched.get("slurmNodes")
    if isinstance(slurm_nodes, MutableMapping):
        # Controller and login expose configurable jail references in the
        # SlurmCluster API. REST and SConfigController consume the canonical
        # `jail` alias internally in Soperator; per-role REST volume values are
        # not part of the CRD and must not be emitted as misleading dead state.
        for role in ("controller", "login"):
            role_values = slurm_nodes.get(role)
            if not isinstance(role_values, MutableMapping):
                continue
            volumes = role_values.get("volumes")
            if not isinstance(volumes, MutableMapping):
                continue
            jail = volumes.get("jail")
            if isinstance(jail, MutableMapping):
                volumes["jail"] = {"volumeSourceName": active_volume_source}
        for role in ("rest", "exporter"):
            role_values = slurm_nodes.get(role)
            if not isinstance(role_values, MutableMapping):
                continue
            volumes = role_values.get("volumes")
            if not isinstance(volumes, MutableMapping):
                continue
            volumes.pop("jail", None)
            if not volumes:
                role_values.pop("volumes", None)
    nodesets = patched.get("nodesets")
    if isinstance(nodesets, Sequence) and not isinstance(nodesets, (str, bytes, bytearray)):
        for nodeset in nodesets:
            if not isinstance(nodeset, MutableMapping):
                continue
            slurmd = nodeset.get("slurmd")
            if not isinstance(slurmd, MutableMapping):
                continue
            volumes = slurmd.get("volumes")
            if not isinstance(volumes, MutableMapping):
                continue
            jail = volumes.get("jail")
            if not isinstance(jail, MutableMapping):
                continue
            volumes["jail"] = {"persistentVolumeClaim": {"claimName": active_pvc_name}}
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
    home_local_path = (
        f"{JAIL_MANAGED_STORE_PATH}/home"
        if managed and legacy_active_source
        else JAIL_MANAGED_HOME_LOCAL_PATH
        if managed
        else JAIL_EXTERNAL_HOME_LOCAL_PATH
    )
    if legacy_active_source is None:
        legacy_active_source = not managed

    patched = copy.deepcopy(dict(to_plain_data(values)))
    jail_rootfs_active_source(patched)
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
    adoption = _mutable_mapping(jail_rootfs, "adoption")
    if legacy_active_source:
        adoption["activeSource"] = JAIL_LEGACY_ACTIVE_SOURCE
        adoption["rollbackSource"] = JAIL_LEGACY_ACTIVE_SOURCE
        adoption["legacyPvcName"] = legacy_jail_pvc_name(patched)
    else:
        adoption["activeSource"] = "slot"
        adoption["rollbackSource"] = "slot"
        adoption.pop("legacyPvcName", None)

    default_mounts = (
        managed_legacy_default_jail_persistent_mounts()
        if managed and legacy_active_source
        else managed_default_jail_persistent_mounts()
        if managed
        else external_default_jail_persistent_mounts()
    )
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


def jail_rootfs_active_source(values: Mapping[str, Any]) -> str:
    """Resolve the canonical active-rootfs source from materialized chart values."""

    rootfs_value = values.get("jailRootfs")
    if rootfs_value is None and "jailRootfs" not in values:
        return JAIL_LEGACY_ACTIVE_SOURCE
    if not isinstance(rootfs_value, Mapping):
        raise ValueError("jailRootfs must be a mapping")
    rootfs = rootfs_value

    if "strategy" in rootfs:
        strategy_value = rootfs.get("strategy")
        if not isinstance(strategy_value, str) or strategy_value != "activePassive":
            raise ValueError("jailRootfs.strategy must be activePassive when present")
        strategy = "activePassive"
    else:
        strategy = ""

    adoption_value = rootfs.get("adoption")
    if adoption_value is None and "adoption" not in rootfs:
        return "slot" if strategy == "activePassive" else JAIL_LEGACY_ACTIVE_SOURCE
    if not isinstance(adoption_value, Mapping):
        raise ValueError("jailRootfs.adoption must be a mapping")
    adoption = adoption_value

    if "activeSource" not in adoption:
        return "slot" if strategy == "activePassive" else JAIL_LEGACY_ACTIVE_SOURCE
    active_source_value = adoption.get("activeSource")
    if not isinstance(active_source_value, str) or not active_source_value:
        raise ValueError("jailRootfs.adoption.activeSource must be a non-empty string")
    active_source = active_source_value
    if active_source not in {"slot", JAIL_LEGACY_ACTIVE_SOURCE}:
        raise ValueError("jailRootfs.adoption.activeSource must be slot or legacy-rootfs")
    return active_source


def jail_rootfs_uses_legacy_active_source(values: Mapping[str, Any]) -> bool:
    return jail_rootfs_active_source(values) == JAIL_LEGACY_ACTIVE_SOURCE
