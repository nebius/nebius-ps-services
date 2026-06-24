"""Shared Soperator GPU driver jail contract helpers."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

SOPERATOR_GPU_DRIVER_JAIL_VALUE_KEY = "gpuDriverJail"
SOPERATOR_GPU_DRIVER_JAIL_MOUNT_NAME = "nvidia-driver-root"
SOPERATOR_GPU_DRIVER_JAIL_MOUNT_PATH = "/run/nvidia/driver"
SOPERATOR_GPU_DRIVER_JAIL_HOST_PATH = "/"
SOPERATOR_GPU_DRIVER_JAIL_INIT_CONTAINER_NAME = "cxcli-gpu-driver-jail"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mutable_mapping(value: Any) -> MutableMapping[str, Any] | None:
    return value if isinstance(value, MutableMapping) else None


def _bool_false(value: Any) -> bool:
    if isinstance(value, bool):
        return value is False
    if value is None:
        return False
    return str(value).strip().lower() in {"0", "false", "no", "off"}


def _positive_int(value: Any) -> int:
    if value in (None, "") or isinstance(value, bool):
        return 0
    try:
        return max(int(str(value).strip()), 0)
    except ValueError:
        return 0


def soperator_nodeset_is_gpu_worker(nodeset: Mapping[str, Any]) -> bool:
    """Return whether a chart-native NodeSet needs the GPU driver jail contract."""

    gpu = _mapping(nodeset.get("gpu"))
    if bool(gpu.get("enabled", False)):
        return True
    resources = _mapping(_mapping(nodeset.get("slurmd")).get("resources"))
    return any(
        _positive_int(resources.get(key)) > 0
        for key in ("gpu", "nvidia.com/gpu")
    )


def _canonical_gpu_driver_jail_mount(mount: Mapping[str, Any]) -> bool:
    if str(mount.get("name", "") or "").strip() != SOPERATOR_GPU_DRIVER_JAIL_MOUNT_NAME:
        return False
    if str(mount.get("mountPath", "") or "").strip() != SOPERATOR_GPU_DRIVER_JAIL_MOUNT_PATH:
        return False
    volume_source = _mapping(mount.get("volumeSource"))
    host_path = _mapping(volume_source.get("hostPath"))
    if str(host_path.get("path", "") or "").strip() != SOPERATOR_GPU_DRIVER_JAIL_HOST_PATH:
        return False
    read_only = mount.get("readOnly", False)
    return _bool_false(read_only) or read_only in (
        None,
        False,
        "false",
        "False",
        "0",
    )


def normalize_soperator_gpu_driver_jail_mounts(
    nodeset: MutableMapping[str, Any],
    *,
    context: str,
) -> None:
    """Remove canonical raw driver mounts and reject conflicting ones.

    The local chart injects this mount itself for GPU NodeSets. cxcli still
    normalizes adopted values so old/raw mounts do not compete with the
    chart-owned mount while unrelated custom mounts stay intact.
    """

    if not soperator_nodeset_is_gpu_worker(nodeset):
        return
    slurmd = _mutable_mapping(nodeset.get("slurmd"))
    volumes = _mutable_mapping(_mapping(slurmd).get("volumes"))
    if slurmd is None or volumes is None:
        return
    mounts = volumes.get("customVolumeMounts")
    if not isinstance(mounts, Sequence) or isinstance(mounts, (str, bytes, bytearray)):
        return

    kept: list[Any] = []
    changed = False
    for raw_mount in mounts:
        mount = _mapping(raw_mount)
        name = str(mount.get("name", "") or "").strip()
        mount_path = str(mount.get("mountPath", "") or "").strip()
        owns_name_or_path = (
            name == SOPERATOR_GPU_DRIVER_JAIL_MOUNT_NAME
            or mount_path == SOPERATOR_GPU_DRIVER_JAIL_MOUNT_PATH
        )
        if not owns_name_or_path:
            kept.append(raw_mount)
            continue
        if not _canonical_gpu_driver_jail_mount(mount):
            raise ValueError(
                f"{context} has a conflicting customVolumeMount for the Soperator GPU "
                f"driver jail. The chart owns {SOPERATOR_GPU_DRIVER_JAIL_MOUNT_NAME} at "
                f"{SOPERATOR_GPU_DRIVER_JAIL_MOUNT_PATH} from hostPath "
                f"{SOPERATOR_GPU_DRIVER_JAIL_HOST_PATH}; remove or rename the custom mount."
            )
        changed = True

    if not changed:
        return
    if kept:
        volumes["customVolumeMounts"] = kept
    else:
        volumes.pop("customVolumeMounts", None)


def ensure_soperator_gpu_driver_jail_values(
    values: MutableMapping[str, Any],
    *,
    context: str,
) -> None:
    """Enable and validate the chart-owned GPU driver jail contract for cxcli."""

    nodesets = values.get("nodesets")
    if not isinstance(nodesets, Sequence) or isinstance(nodesets, (str, bytes, bytearray)):
        return
    gpu_nodesets = [
        item for item in nodesets if isinstance(item, MutableMapping) and soperator_nodeset_is_gpu_worker(item)
    ]
    if not gpu_nodesets:
        return

    raw_config = values.get(SOPERATOR_GPU_DRIVER_JAIL_VALUE_KEY)
    config = raw_config if isinstance(raw_config, MutableMapping) else {}
    if _bool_false(config.get("enabled")):
        raise ValueError(
            f"{context} has GPU worker NodeSets but {SOPERATOR_GPU_DRIVER_JAIL_VALUE_KEY}.enabled=false. "
            "Nebius-image GPU workers require the chart-owned driver jail mount and init guard."
        )
    config["enabled"] = True
    values[SOPERATOR_GPU_DRIVER_JAIL_VALUE_KEY] = config
    for nodeset in gpu_nodesets:
        name = str(nodeset.get("name", "") or "worker").strip()
        normalize_soperator_gpu_driver_jail_mounts(
            nodeset,
            context=f"{context} NodeSet {name}",
        )
