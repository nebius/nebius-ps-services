"""Zero-copy directory protection and retained physical jail generations."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shlex
from collections.abc import Mapping, Sequence
from typing import Any

from .soperator_jail_mounts import (
    JAIL_MANDATORY_PERSISTENT_MOUNT_PATHS,
    _normalize_path,
    apply_jail_persistent_mount_values,
    jail_persistent_mounts_from_paths,
    jail_rootfs_active_source,
    normalize_jail_storage_intent,
)


def _contains(parent: str, child: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def _overlaps(left: str, right: str) -> bool:
    return _contains(left, right) or _contains(right, left)


def retained_rootfs_generations(values: Mapping[str, Any]) -> list[dict[str, str]]:
    """Validate retained ownership without accepting arbitrary filesystem exceptions."""
    rootfs = values.get("jailRootfs") or {}
    rows = rootfs.get("retainedGenerations", [])
    if not isinstance(rows, list):
        raise ValueError("jailRootfs.retainedGenerations must be a list")
    root = _normalize_path(
        (rootfs.get("store") or {}).get("rootfsPath", "/mnt/jail/.cxcli/rootfs"),
        field="jailRootfs.store.rootfsPath",
    )
    result: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "localPath",
            "volumeSourceName",
            "pvName",
            "pvcName",
        }:
            raise ValueError("retained rootfs generation identity is incomplete")
        item = {key: str(value) for key, value in row.items()}
        path = _normalize_path(item["localPath"], field="retained rootfs localPath")
        if path.rsplit("/", 1)[0] != root or path.rsplit("/", 1)[1].startswith("."):
            raise ValueError("retained rootfs generations must be direct children of rootfsPath")
        for key in ("pvName", "pvcName", "volumeSourceName"):
            if len(item[key]) > 63 or not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", item[key]
            ):
                raise ValueError(f"retained rootfs {key} is invalid")
        item["localPath"] = path
        for previous in result:
            if any(previous[key] == item[key] for key in item):
                raise ValueError("retained rootfs generation identities overlap")
        result.append(item)
    return sorted(result, key=lambda row: row["localPath"])


def slot_generation(values: Mapping[str, Any], slot: str) -> dict[str, str]:
    rootfs = values["jailRootfs"]
    row = rootfs["slots"][slot]
    source = str(row["volumeSourceName"])
    return {
        "localPath": str(row["localPath"]),
        "volumeSourceName": source,
        "pvcName": str(row["pvcName"]),
        "pvName": str(row.get("pvName") or f"{source}-pv"),
    }


def validate_rootfs_generations(values: Mapping[str, Any]) -> None:
    root = _normalize_path(values["jailRootfs"]["store"]["rootfsPath"], field="rootfsPath")
    store = _normalize_path(values["jailRootfs"]["store"]["mountPath"], field="store mountPath")
    if root == store or not _contains(store, root):
        raise ValueError("rootfsPath must be inside the physical jail store")
    slots = [slot_generation(values, slot) for slot in ("slot-a", "slot-b")]
    retained = retained_rootfs_generations(values)
    for index, item in enumerate(slots):
        path = _normalize_path(item["localPath"], field="rootfs generation localPath")
        if path.rsplit("/", 1)[0] != root or path.rsplit("/", 1)[1].startswith("."):
            raise ValueError("rootfs generations must be direct children of rootfsPath")
        for other in slots[:index]:
            if any(item[key] == other[key] for key in item):
                raise ValueError("rootfs physical generation identities overlap")
        for other in retained:
            if item != other and any(item[key] == other[key] for key in item):
                raise ValueError("rootfs physical generation identities overlap")


def assert_protection_extension(source: Mapping[str, Any], desired: Mapping[str, Any]) -> None:
    """An upgrade may add protection but cannot release accepted customer storage."""
    from .soperator_adapter import soperator_persistent_mount_volume_names

    old = normalize_jail_storage_intent(source)
    new = normalize_jail_storage_intent(desired)
    mounts = {row["mountPath"]: row for row in new.get("jailPersistentMounts", [])}
    for row in old.get("jailPersistentMounts", []):
        current = mounts.get(row["mountPath"], {})
        if current.get("localPath") != row["localPath"] or soperator_persistent_mount_volume_names(
            current
        ) != soperator_persistent_mount_volume_names(row):
            raise ValueError("upgrade cannot remove or redirect an existing protected folder")
    if any(row not in retained_rootfs_generations(new) for row in retained_rootfs_generations(old)):
        raise ValueError("upgrade cannot release a retained rootfs generation")


def protect_jail_directories(
    values: Mapping[str, Any], *, paths: Sequence[str], layout: str, target_ref: str
) -> dict[str, Any]:
    """Add mount bindings to existing directories, never copy or move their contents."""
    existing_rootfs = values.get("jailRootfs") or {}
    existing_store = (existing_rootfs.get("store") or {}).get("mountPath")
    if existing_store:
        if existing_store not in {"/mnt/jail-store", "/mnt/jail"}:
            raise ValueError("unsupported physical jail store; cannot redirect protected backing")
        layout = "managed" if existing_store == "/mnt/jail-store" else "external"
    old_adoption = existing_rootfs.get("adoption") or {}
    if old_adoption.get("rollbackSource") == "legacy-rootfs" and not old_adoption.get(
        "legacyPvcName"
    ):
        raise ValueError("legacy rollback authority requires jailRootfs.adoption.legacyPvcName")
    legacy = jail_rootfs_active_source(values) != "slot"
    patched = apply_jail_persistent_mount_values(
        values, target_ref=target_ref, layout=layout, legacy_active_source=legacy
    )
    # Preserve an admitted legacy recovery source after the first slot switch.
    if not legacy and old_adoption.get("rollbackSource") == "legacy-rootfs":
        if not old_adoption.get("legacyPvcName"):
            raise ValueError("legacy rollback authority requires jailRootfs.adoption.legacyPvcName")
        patched["jailRootfs"]["adoption"].update(old_adoption)
    existing = {row["mountPath"]: row for row in patched["jailPersistentMounts"]}
    selected = jail_persistent_mounts_from_paths(paths, layout=layout, legacy_active_source=legacy)
    normalized = [row.mount_path for row in selected]
    if len(normalized) != len(set(normalized)):
        raise ValueError("additional persistent data paths must be unique after normalization")
    rootfs = patched["jailRootfs"]
    retained = retained_rootfs_generations(patched)
    generation = slot_generation(patched, rootfs["activeSlot"])
    backing_root = rootfs["store"]["mountPath"] if legacy else generation["localPath"]
    for path in normalized:
        if path in existing or path in JAIL_MANDATORY_PERSISTENT_MOUNT_PATHS:
            continue
        if not legacy and generation not in retained:
            retained.append(generation)
        existing[path] = {"mountPath": path, "localPath": backing_root + path}
    if retained:
        rootfs["retainedGenerations"] = sorted(retained, key=lambda row: row["localPath"])
    patched["jailPersistentMounts"] = list(existing.values())
    return normalize_jail_storage_intent(patched)


def prepare_disposable_passive_generation(values: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Select new physical backing before a retained logical slot is reused."""
    patched = normalize_jail_storage_intent(values)
    rootfs = patched["jailRootfs"]
    slot = rootfs["passiveSlot"]
    old = slot_generation(patched, slot)
    retained = retained_rootfs_generations(patched)
    if not any(_overlaps(row["localPath"], old["localPath"]) for row in retained):
        assert_disposable_generation(patched, old)
        return patched, False
    if old not in retained:
        raise ValueError("passive rootfs aliases retained storage with a different identity")
    token = hashlib.sha256(
        json.dumps({"previous": old, "retained": retained}, sort_keys=True).encode()
    ).hexdigest()[:16]
    name = f"jail-rootfs-{slot}-{token}"
    rootfs["slots"][slot] = {
        "localPath": f"{rootfs['store']['rootfsPath']}/{slot}-{token}",
        "volumeSourceName": name,
        "pvcName": f"{name}-pvc",
        "pvName": f"{name}-pv",
    }
    assert_disposable_generation(patched, rootfs["slots"][slot])
    return patched, True


def assert_disposable_generation(values: Mapping[str, Any], generation: Mapping[str, str]) -> None:
    target = _normalize_path(generation["localPath"], field="target rootfs localPath")
    for retained in retained_rootfs_generations(values):
        if _overlaps(target, retained["localPath"]) or any(
            generation[key] == retained[key] for key in ("pvName", "pvcName")
        ):
            raise ValueError("target rootfs generation is retained customer storage")
    for mount in values.get("jailPersistentMounts", []):
        if _overlaps(target, mount["localPath"]):
            raise ValueError("target rootfs generation overlaps persistent customer storage")


def freeze_jail_protection(values: Mapping[str, Any]) -> str:
    """The campaign owns a canonical, immutable projection of storage intent."""
    normalized = normalize_jail_storage_intent(values)
    return json.dumps(
        {
            "jailRootfs": normalized["jailRootfs"],
            "jailPersistentMounts": normalized.get("jailPersistentMounts", []),
            "jailVolumeLocalPath": normalized.get("volume", {}).get("jail", {}).get("localPath"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def apply_frozen_jail_protection(values: Mapping[str, Any], frozen: str) -> dict[str, Any]:
    payload = json.loads(frozen)
    if not isinstance(payload, dict) or set(payload) != {
        "jailRootfs",
        "jailPersistentMounts",
        "jailVolumeLocalPath",
    }:
        raise ValueError("frozen jail protection plan is invalid")
    patched = copy.deepcopy(dict(values))
    local_path = payload.pop("jailVolumeLocalPath")
    patched.update(payload)
    if local_path is not None:
        patched.setdefault("volume", {}).setdefault("jail", {})["localPath"] = local_path
    if freeze_jail_protection(patched) != frozen:
        raise ValueError("frozen jail protection plan is not canonical")
    return patched


def directory_probe_script(paths: Sequence[str]) -> str:
    """Read only directory metadata; never enumerate or read customer files."""
    lines = ["set -eu", "root_device=$(stat -c %d /mnt/jail)"]
    for path in paths:
        _normalize_path(path, field="protected directory")
        parts = ("/mnt/jail" + path).split("/")
        for index in range(2, len(parts) + 1):
            ancestor = shlex.quote("/".join(parts[:index]))
            lines.append(f"test -d {ancestor} && test ! -L {ancestor}")
        quoted = shlex.quote("/mnt/jail" + path)
        lines.extend(
            (
                f'test "$(stat -c %d {quoted})" = "$root_device"',
                f"stat -c %i {quoted}",
            )
        )
    return "\n".join(lines)


def observed_directory_bindings(
    values: Mapping[str, Any], *, pod: Mapping[str, Any], read_pvc: Any, read_pv: Any
) -> tuple[str, list[dict[str, str]]]:
    """Prove the proposed backing is the directory currently visible in the jail."""
    optional = [
        row
        for row in values.get("jailPersistentMounts", [])
        if row["mountPath"] not in JAIL_MANDATORY_PERSISTENT_MOUNT_PATHS
    ]
    containers = [
        row
        for row in pod["spec"]["containers"]
        if any(m.get("mountPath") == "/mnt/jail" for m in row.get("volumeMounts", []))
    ]
    sshd = [row for row in containers if row["name"] == "sshd"]
    containers = sshd or containers
    if len(containers) != 1:
        raise ValueError("protected directory observation requires one exact login jail container")
    container = containers[0]
    volumes = {row["name"]: row for row in pod["spec"]["volumes"]}
    result = []
    for row in optional:
        path = "/mnt/jail" + row["mountPath"]
        mounts = [m for m in container["volumeMounts"] if _contains(m["mountPath"], path)]
        mount = max(mounts, key=lambda m: len(m["mountPath"]))
        volume = volumes[mount["name"]]
        claim = volume.get("persistentVolumeClaim", {}).get("claimName")
        if not claim or mount.get("subPathExpr"):
            raise ValueError("protected directory is not backed by an exact jail PVC")
        pvc = read_pvc(claim)
        pv = read_pv(pvc.get("spec", {}).get("volumeName", ""))
        root = pv.get("spec", {}).get("local", {}).get("path")
        if not root:
            raise ValueError("additional protected folders must be on the physical jail filesystem")
        physical = root.rstrip("/")
        if mount.get("subPath"):
            physical += "/" + mount["subPath"]
        physical += path[len(mount["mountPath"]) :]
        physical = _normalize_path(physical, field="observed protected directory backing")
        if physical != row["localPath"]:
            raise ValueError(
                f"protected directory {row['mountPath']} differs from its live backing"
            )
        if not pvc.get("metadata", {}).get("uid") or not pv.get("metadata", {}).get("uid"):
            raise ValueError("protected directory storage identity is incomplete")
        result.append({"mountPath": row["mountPath"], "localPath": physical})
    return container["name"], result


def rootfs_storage_authority(state: Mapping[str, Any], target_slot: str) -> dict[str, Any]:
    """Canonical rendered physical identities carried by admission and replay."""
    target = state["slots"][target_slot]
    for slot, other in state["slots"].items():
        if slot != target_slot and (
            _overlaps(target["local_path"], other["local_path"])
            or any(target[key] == other[key] for key in ("pv_name", "pvc_name"))
        ):
            raise ValueError("target rootfs generation aliases another logical slot")
    retained = state.get("retainedGenerations", [])
    mounts = state.get("persistentMounts", [])
    for row in [*retained, *mounts]:
        if row.get("local_path") and _overlaps(target["local_path"], row["local_path"]):
            raise ValueError("target rootfs generation overlaps retained customer storage")
    for row in retained:
        if any(target[key] == row[key] for key in ("pv_name", "pvc_name")):
            raise ValueError("target rootfs volume identity belongs to retained storage")
    return {
        "filesystemId": state["filesystemId"],
        "deviceTag": state["deviceTag"],
        "mountPath": state["mountPath"],
        "targetGeneration": dict(target),
        "persistentMounts": sorted(
            (dict(row) for row in mounts), key=lambda row: row["mount_path"]
        ),
        "retainedGenerations": sorted(
            (dict(row) for row in retained), key=lambda row: row["local_path"]
        ),
    }


def validate_storage_authority(authority: Mapping[str, Any], *, target_pvc: str) -> dict[str, Any]:
    if not isinstance(authority, Mapping) or set(authority) != {
        "filesystemId",
        "deviceTag",
        "mountPath",
        "targetGeneration",
        "persistentMounts",
        "retainedGenerations",
    }:
        raise ValueError("rootfs storage authority is incomplete")
    target = authority["targetGeneration"]
    if not isinstance(target, Mapping) or set(target) != {
        "volume_name",
        "pv_name",
        "pvc_name",
        "local_path",
    }:
        raise ValueError("target rootfs generation identity is incomplete")
    if target["pvc_name"] != target_pvc or not all(
        isinstance(v, str) and v for v in target.values()
    ):
        raise ValueError("target rootfs generation identity changed")
    _normalize_path(target["local_path"], field="target rootfs path")
    normalized = rootfs_storage_authority(
        {
            **authority,
            "slots": {"target": target},
        },
        "target",
    )
    if normalized != authority:
        raise ValueError("rootfs storage authority is not canonical")
    return copy.deepcopy(normalized)


def verify_target_volume(
    authority: Mapping[str, Any], *, pvc: Mapping[str, Any], pv: Mapping[str, Any]
) -> str:
    target = authority["targetGeneration"]
    validate_storage_authority(authority, target_pvc=target["pvc_name"])
    pvc_meta, pvc_spec = pvc.get("metadata", {}), pvc.get("spec", {})
    pv_meta, pv_spec = pv.get("metadata", {}), pv.get("spec", {})
    claim = pv_spec.get("claimRef", {})
    if (
        pvc_meta.get("name") != target["pvc_name"]
        or not pvc_meta.get("uid")
        or pvc_spec.get("volumeName") != target["pv_name"]
        or pv_meta.get("name") != target["pv_name"]
        or not pv_meta.get("uid")
        or pv_spec.get("local", {}).get("path") != target["local_path"]
        or pv_spec.get("persistentVolumeReclaimPolicy") != "Retain"
        or claim.get("name") != target["pvc_name"]
        or claim.get("namespace") != pvc_meta.get("namespace")
        or claim.get("uid", pvc_meta["uid"]) != pvc_meta["uid"]
    ):
        raise ValueError("target rootfs PV/PVC differs from its admitted physical backing")
    return str(pv_meta["uid"])
