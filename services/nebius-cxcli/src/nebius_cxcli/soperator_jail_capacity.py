"""Active/passive Soperator jail rootfs capacity checks."""

from __future__ import annotations

import math
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml

from .soperator_populate_jail import active_passive_pod_scheduling_fields

GIB = 1024**3
KIB = 1024
DEFAULT_REQUIRED_ROOTFS_GIB = 64
REQUIRED_ROOTFS_HEADROOM_NUMERATOR = 5
REQUIRED_ROOTFS_HEADROOM_DENOMINATOR = 4
RESIZE_ROUNDING_GIB = 256

JAIL_SFS_RESIZE_POLICIES = frozenset({"fail", "prompt", "apply"})


@dataclass(frozen=True)
class JailCapacityPreflight:
    status: str
    active_used_bytes: int | None
    active_used_source: str
    passive_available_bytes: int
    required_bytes: int
    shortage_bytes: int
    degraded: bool
    reason: str

    @property
    def sufficient(self) -> bool:
        return self.status == "passed"

    @property
    def required_gib(self) -> int:
        return _ceil_gib(self.required_bytes)

    @property
    def passive_available_gib(self) -> int:
        return self.passive_available_bytes // GIB

    @property
    def shortage_gib(self) -> int:
        return _ceil_gib(self.shortage_bytes)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "reason": self.reason,
            "required_gib": self.required_gib,
            "passive_available_gib": self.passive_available_gib,
            "shortage_gib": self.shortage_gib,
            "active_used_source": self.active_used_source,
            "degraded": self.degraded,
        }
        if self.active_used_bytes is not None:
            payload["active_used_gib"] = _ceil_gib(self.active_used_bytes)
        return payload


def _ceil_gib(bytes_value: int) -> int:
    if bytes_value <= 0:
        return 0
    return int(math.ceil(bytes_value / GIB))


def round_up_gib(value: int, *, quantum: int = RESIZE_ROUNDING_GIB) -> int:
    if value <= 0:
        return quantum
    return int(math.ceil(value / quantum) * quantum)


def resolve_jail_sfs_resize_policy(policy: str | None, *, interactive: bool) -> str:
    normalized = str(policy or "").strip().lower()
    if not normalized:
        return "prompt" if interactive else "fail"
    if normalized not in JAIL_SFS_RESIZE_POLICIES:
        allowed = "|".join(sorted(JAIL_SFS_RESIZE_POLICIES))
        raise ValueError(f"--jail-sfs-resize-policy must be one of {allowed}; got {policy!r}.")
    return normalized


def required_passive_rootfs_bytes(active_used_bytes: int | None) -> tuple[int, str, bool]:
    minimum = DEFAULT_REQUIRED_ROOTFS_GIB * GIB
    if active_used_bytes is None or active_used_bytes <= 0:
        return minimum, "fallback-minimum", True
    with_headroom = math.ceil(
        active_used_bytes
        * REQUIRED_ROOTFS_HEADROOM_NUMERATOR
        / REQUIRED_ROOTFS_HEADROOM_DENOMINATOR
    )
    return max(minimum, with_headroom), "measured-active-slot", False


def evaluate_jail_capacity(
    *,
    passive_available_bytes: int,
    active_used_bytes: int | None,
) -> JailCapacityPreflight:
    required, source, degraded = required_passive_rootfs_bytes(active_used_bytes)
    shortage = max(0, required - max(0, passive_available_bytes))
    status = "passed" if shortage == 0 else "failed"
    reason = (
        "passive rootfs slot has enough free space"
        if shortage == 0
        else "passive rootfs slot does not have enough free space"
    )
    if degraded:
        reason += "; active slot usage could not be measured, so the minimum was used"
    return JailCapacityPreflight(
        status=status,
        active_used_bytes=active_used_bytes if active_used_bytes and active_used_bytes > 0 else None,
        active_used_source=source,
        passive_available_bytes=max(0, passive_available_bytes),
        required_bytes=required,
        shortage_bytes=shortage,
        degraded=degraded,
        reason=reason,
    )


def recommend_jail_sfs_size_gib(
    *,
    current_size_gib: int,
    shortage_bytes: int,
    explicit_size_gib: int | None = None,
) -> int:
    if current_size_gib <= 0:
        raise ValueError("current jail SFS size must be a positive GiB value.")
    shortage_gib = _ceil_gib(shortage_bytes)
    if explicit_size_gib is not None:
        if explicit_size_gib < current_size_gib:
            raise ValueError(
                "jail SFS resize cannot shrink the filesystem: "
                f"current={current_size_gib}GiB requested={explicit_size_gib}GiB."
            )
        if explicit_size_gib < current_size_gib + shortage_gib:
            raise ValueError(
                "requested jail SFS size is too small for the passive rootfs slot: "
                f"need at least {current_size_gib + shortage_gib}GiB."
            )
        return explicit_size_gib
    target = current_size_gib + math.ceil(shortage_gib * 1.10)
    return round_up_gib(target)


def parse_capacity_probe_output(output: str) -> JailCapacityPreflight:
    values: dict[str, str] = {}
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    passive_kib = _positive_int(values.get("passive_available_kib"))
    if passive_kib is None:
        raise RuntimeError("jail capacity probe did not report passive_available_kib.")
    active_kib = _positive_int(values.get("active_used_kib"))
    return evaluate_jail_capacity(
        passive_available_bytes=passive_kib * KIB,
        active_used_bytes=active_kib * KIB if active_kib is not None else None,
    )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _kubectl_args(
    args: Sequence[str],
    *,
    kube_context: str = "",
) -> tuple[str, ...]:
    if kube_context:
        return ("kubectl", "--context", kube_context, *args)
    return ("kubectl", *args)


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower())
    normalized = normalized.strip("-") or "soperator"
    return normalized[:40].strip("-") or "soperator"


def sh_quote(value: str) -> str:
    return shlex.quote(str(value))


def _active_mount_exclude_path(path: str, *, active_rootfs_path: str = "") -> str:
    raw_path = str(path or "").strip().rstrip("/")
    if not raw_path or raw_path == "/":
        return ""
    active_root = str(active_rootfs_path or "").strip().rstrip("/")
    if active_root and (raw_path == active_root or raw_path.startswith(f"{active_root}/")):
        suffix = raw_path.removeprefix(active_root).lstrip("/")
        return "/mnt/active" if not suffix else f"/mnt/active/{suffix}"
    if raw_path.startswith("/mnt/active/") or raw_path == "/mnt/active":
        return raw_path
    return raw_path


def _capacity_probe_job_manifest(
    *,
    namespace: str,
    target_ref: str,
    image: str,
    active_pvc: str,
    passive_pvc: str,
    active_rootfs_path: str = "",
    exclude_paths: Sequence[str] = (),
    scheduling: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    name = f"{_safe_name(target_ref)}-jail-capacity-probe"
    shared_pvc = active_pvc == passive_pvc
    active_mount_path = "/mnt/active"
    passive_mount_path = active_mount_path if shared_pvc else "/mnt/passive"
    exclude_mount_paths = tuple(
        _active_mount_exclude_path(path, active_rootfs_path=active_rootfs_path)
        for path in exclude_paths
    )
    exclude_mount_paths = tuple(dict.fromkeys(path for path in exclude_mount_paths if path))
    exclude_script = "\n".join(
        f"subtract_path {sh_quote(path)}" for path in exclude_mount_paths
    )
    script = r"""
    set -eu
    active_used_kib=""
    subtract_path() {
      path="$1"
      [ -e "$path" ] || return 0
      used="$(du -sk "$path" 2>/dev/null | awk '{print $1}' || true)"
      case "$used" in
        ''|*[!0-9]*) used=0 ;;
      esac
      exclude_used_kib=$((exclude_used_kib + used))
    }
    if active_total_line="$(du -sk __ACTIVE_MOUNT_PATH__ 2>/tmp/active-du.err | awk '{print $1}')" && [ -n "$active_total_line" ]; then
      exclude_used_kib=0
__EXCLUDE_SCRIPT__
      active_used_kib=$((active_total_line - exclude_used_kib))
      if [ "$active_used_kib" -lt 0 ]; then
        active_used_kib=0
      fi
    fi
    passive_available_kib="$(df -Pk __PASSIVE_MOUNT_PATH__ | awk 'NR==2 {print $4}')"
    printf 'active_used_kib=%s\n' "$active_used_kib"
    printf 'passive_available_kib=%s\n' "$passive_available_kib"
    """
    script = (
        script.replace("__ACTIVE_MOUNT_PATH__", sh_quote(active_mount_path))
        .replace("__PASSIVE_MOUNT_PATH__", sh_quote(passive_mount_path))
        .replace("__EXCLUDE_SCRIPT__", exclude_script)
    )
    volume_mounts = [
        {"name": "active-rootfs", "mountPath": active_mount_path, "readOnly": True},
    ]
    volumes = [
        {
            "name": "active-rootfs",
            "persistentVolumeClaim": {"claimName": active_pvc, "readOnly": True},
        },
    ]
    if not shared_pvc:
        volume_mounts.append({"name": "passive-rootfs", "mountPath": passive_mount_path})
        volumes.append(
            {
                "name": "passive-rootfs",
                "persistentVolumeClaim": {"claimName": passive_pvc},
            }
        )
    pod_spec = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "containers": [
            {
                "name": "capacity-probe",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "command": ["/bin/sh", "-ceu", script],
                "volumeMounts": volume_mounts,
            }
        ],
        "volumes": volumes,
    }
    pod_spec.update(active_passive_pod_scheduling_fields(scheduling))
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "namespace": namespace,
            "name": name,
            "labels": {
                "app.kubernetes.io/component": "jail-capacity-probe",
                "slurm.nebius.ai/jail-rootfs-refresh": "active-passive",
            },
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/component": "jail-capacity-probe",
                    }
                },
                "spec": pod_spec,
            },
        },
    }


def probe_active_passive_jail_capacity(
    command_runner: Callable[..., Any],
    *,
    namespace: str,
    target_ref: str,
    image: str,
    active_pvc: str,
    passive_pvc: str,
    active_rootfs_path: str = "",
    exclude_paths: Sequence[str] = (),
    scheduling: Mapping[str, Any] | None = None,
    kube_context: str = "",
    timeout_seconds: int = 300,
) -> JailCapacityPreflight:
    if not image:
        raise RuntimeError("Cannot run jail capacity preflight without a populate-jail image.")
    manifest = _capacity_probe_job_manifest(
        namespace=namespace,
        target_ref=target_ref,
        image=image,
        active_pvc=active_pvc,
        passive_pvc=passive_pvc,
        active_rootfs_path=active_rootfs_path,
        exclude_paths=exclude_paths,
        scheduling=scheduling,
    )
    metadata = manifest["metadata"]
    job_name = str(metadata["name"])
    command_runner(
        _kubectl_args(
            ("-n", namespace, "delete", "job", job_name, "--ignore-not-found", "--wait=false"),
            kube_context=kube_context,
        ),
        timeout_seconds=120,
    )
    command_runner(
        _kubectl_args(("apply", "-f", "-"), kube_context=kube_context),
        input_text=yaml.safe_dump({"apiVersion": "v1", "kind": "List", "items": [manifest]}, sort_keys=False),
        timeout_seconds=120,
    )
    command_runner(
        _kubectl_args(
            ("-n", namespace, "wait", f"job/{job_name}", "--for=condition=complete", "--timeout=5m"),
            kube_context=kube_context,
        ),
        timeout_seconds=timeout_seconds,
    )
    logs = command_runner(
        _kubectl_args(("-n", namespace, "logs", f"job/{job_name}"), kube_context=kube_context),
        timeout_seconds=120,
    )
    command_runner(
        _kubectl_args(
            ("-n", namespace, "delete", "job", job_name, "--ignore-not-found", "--wait=false"),
            kube_context=kube_context,
        ),
        timeout_seconds=120,
        check=False,
    )
    return parse_capacity_probe_output(str(getattr(logs, "stdout", "") or ""))


def capacity_preflight_check_payload(preflight: JailCapacityPreflight) -> Mapping[str, str]:
    status = "passed" if preflight.sufficient else "failed"
    detail = (
        f"required={preflight.required_gib}GiB, "
        f"available={preflight.passive_available_gib}GiB, "
        f"shortage={preflight.shortage_gib}GiB"
    )
    if preflight.degraded:
        detail += ", active slot measurement degraded"
    return {
        "name": "jail rootfs passive slot capacity",
        "status": status,
        "detail": detail,
    }
