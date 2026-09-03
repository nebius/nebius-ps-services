"""Thin declarative adapter between cxcli storage and upstream Soperator."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .soperator_jail_mounts import JAIL_MANDATORY_PERSISTENT_MOUNT_PATHS
from .soperator_release import SoperatorReleaseSnapshot

SOPERATOR_ADAPTER_LABEL = "soperator.nebius.ai/managed-by"
SOPERATOR_ADAPTER_LABEL_VALUE = "nebius-cxcli-adapter"
SOPERATOR_VALUES_CONFIGMAP = "terraform-fluxcd-values"
SOPERATOR_VALUES_NAMESPACE = "flux-system"
SOPERATOR_ADAPTER_NAMESPACE = "soperator"
SOPERATOR_ADAPTER_STATE_CONFIGMAP = "nebius-cxcli-soperator-adapter"
SOPERATOR_LIFECYCLE_LABEL = "soperator.nebius.ai/lifecycle"
SOPERATOR_LIFECYCLE_PROTECTED = "protected"
SOPERATOR_LIFECYCLE_RECREATABLE = "recreatable"
SOPERATOR_LIFECYCLE_SHARED = "shared-adopted"
_DNS_TOKEN_RE = re.compile(r"[^a-z0-9-]+")
_DEVICE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_NFS_SERVER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
_IMMUTABLE_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_PROTECTED_UPGRADE_GENERATED_VOLUME_SOURCE_NAMES = frozenset({"controller-spool", "jail"})
SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS = frozenset(
    {"sha256:20cf96ba24157f4c2cd4248906613b041680cf7d0275add50c2f1a92e72be073"}
)
SOPERATOR_VM_STACK_CLEANUP_HOOK_DISABLED_PACKAGES = frozenset(
    {
        (
            "victoria-metrics-k8s-stack",
            "0.39.4",
            "https://victoriametrics.github.io/helm-charts",
            "sha256:01e38a9632441d5c6c11f7c047fb99dc41084b4cc32e96c15ede612f85e02eb9",
            None,
        )
    }
)
_SOPERATOR_MONITORING_DASHBOARD_FILES = (
    "cluster_health.json",
    "gpu_cluster_stats.json",
    "jobs_overview.json",
    "nfs_server_client.json",
    "slurm_controller.json",
    "workers_detailed_stats.json",
    "workers_overview.json",
)
_CONFIGMAP_SAFE_DATA_BYTES = 900 * 1024


@dataclass(frozen=True)
class SoperatorJailImageAuthority:
    """One effective digest-pinned populate-jail image and its authority source."""

    image: str
    source: str
    upstream_image: str


@dataclass(frozen=True)
class SoperatorPersistentMountBinding:
    """One normalized protected path and its adapter-owned storage identities."""

    name: str
    mount_path: str
    pv_name: str
    pvc_name: str


def soperator_adapter_state_from_documents(
    documents: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the exact digest-bound adapter state from its rendered ConfigMap."""

    candidates = [
        document
        for document in documents
        if str(document.get("kind") or "") == "ConfigMap"
        and str(_mapping(document.get("metadata")).get("name") or "")
        == SOPERATOR_ADAPTER_STATE_CONFIGMAP
        and str(_mapping(document.get("metadata")).get("namespace") or "")
        == SOPERATOR_ADAPTER_NAMESPACE
    ]
    if len(candidates) != 1:
        raise ValueError("rendered Soperator adapter state ConfigMap is missing or ambiguous")
    document = candidates[0]
    raw_state = _mapping(document.get("data")).get("state.json")
    if not isinstance(raw_state, str):
        raise ValueError("rendered Soperator adapter state has no state.json payload")
    try:
        candidate = json.loads(raw_state)
    except json.JSONDecodeError as exc:
        raise ValueError("rendered Soperator adapter state is invalid JSON") from exc
    if not isinstance(candidate, dict):
        raise ValueError("rendered Soperator adapter state must be a mapping")
    state = copy.deepcopy(candidate)
    schema = str(state.get("schema") or "").strip()
    active_slot = str(state.get("activeSlot") or "").strip()
    passive_slot = str(state.get("passiveSlot") or "").strip()
    active_pvc = str(state.get("activePvc") or "").strip()
    slots = state.get("slots")
    mounts = state.get("persistentMounts")
    if (
        not schema.startswith("nebius-cxcli.soperator-adapter-state.")
        or {active_slot, passive_slot} != {"slot-a", "slot-b"}
        or not active_pvc
        or not isinstance(slots, Mapping)
        or not isinstance(mounts, list)
    ):
        raise ValueError("rendered Soperator adapter state is incomplete")
    slot = _mapping(slots.get(active_slot))
    if str(slot.get("pvc_name") or "").strip() != active_pvc:
        raise ValueError("rendered Soperator adapter active PVC does not match its active slot")
    expected_digest = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    annotations = _mapping(_mapping(document.get("metadata")).get("annotations"))
    if str(annotations.get("soperator.nebius.ai/state-sha256") or "") != expected_digest:
        raise ValueError("rendered Soperator adapter state digest does not match its payload")
    return state


def soperator_persistent_mount_bindings_from_adapter_state(
    state: Mapping[str, Any],
) -> tuple[SoperatorPersistentMountBinding, ...]:
    """Return protected mount identities from the compiled adapter contract."""

    raw_mounts = state.get("persistentMounts")
    if not isinstance(raw_mounts, list):
        raise ValueError("rendered Soperator adapter persistent mounts are missing")
    bindings: list[SoperatorPersistentMountBinding] = []
    seen_paths: set[str] = set()
    for index, item in enumerate(raw_mounts):
        if not isinstance(item, Mapping):
            raise ValueError(f"adapter persistentMounts[{index}] must be a mapping")
        name = str(item.get("name") or "").strip()
        mount_path = str(item.get("mount_path") or "").strip()
        pv_name = str(item.get("pv_name") or "").strip()
        pvc_name = str(item.get("pvc_name") or "").strip()
        if not name or not mount_path.startswith("/") or not pv_name or not pvc_name:
            raise ValueError(f"adapter persistentMounts[{index}] is incomplete")
        if mount_path in seen_paths:
            raise ValueError(f"duplicate adapter persistent mount path {mount_path}")
        seen_paths.add(mount_path)
        bindings.append(
            SoperatorPersistentMountBinding(
                name=name,
                mount_path=mount_path,
                pv_name=pv_name,
                pvc_name=pvc_name,
            )
        )
    return tuple(bindings)


_PARENT_ONLY_KEYS = {
    "certManager",
    "clusterType",
    "controllerManager",
    "customContainer",
    "externalNfs",
    "fullnameOverride",
    "gpuDriverJail",
    "hostNetwork",
    "jailPersistentMounts",
    "jailRootfs",
    "kruise",
    "mariadb-operator",
    "nameOverride",
    "nodesets",
    "observability",
    "partitionProfile",
    "priorityClasses",
    "rebooter",
    "serviceMonitor",
    "sfs",
    "soperator-activechecks",
    "soperator-backup-config",
    "soperator-checks",
    "soperator-dcgm-exporter",
    "soperator-notifier",
    "storage",
    "storageClass",
    "topologyProfile",
    "uninstallCleanup",
    "volume",
}

_UNSUPPORTED_DOWNSTREAM_KEYS = {
    "qosConfiguration",
    "schedulingConfig",
    "storageClass",
    "uninstallCleanup",
}

_PARTITION_POLICY_FIELDS = {
    "default": "Default",
    "hidden": "Hidden",
    "state": "State",
    "maxTime": "MaxTime",
    "defaultTime": "DefaultTime",
    "priorityTier": "PriorityTier",
    "preemptMode": "PreemptMode",
    "defMemPerNode": "DefMemPerNode",
    "defMemPerCPU": "DefMemPerCPU",
    "defMemPerGPU": "DefMemPerGPU",
    "defCpuPerGPU": "DefCpuPerGPU",
    "overSubscribe": "OverSubscribe",
    "allowAccounts": "AllowAccounts",
    "allowQos": "AllowQos",
    "denyAccounts": "DenyAccounts",
    "denyQos": "DenyQos",
}

_MOUNT_SCRIPT = """#!/bin/sh
set -eu
set -f
host_path="/host${MOUNT_PATH}"
receipt_dir_name=".nebius-cxcli/mount-receipts"

boot_id() {
  tr -d '\r\n' </proc/sys/kernel/random/boot_id
}

for_each_path() {
  callback="$1"
  "$callback" "$host_path"
  old_ifs="$IFS"
  IFS=';'
  for relative in $CREATE_DIRS; do
    [ -n "$relative" ] && "$callback" "$host_path/$relative"
  done
  for relative in $VERIFY_DIRS; do
    [ -n "$relative" ] && "$callback" "$host_path/$relative"
  done
  IFS="$old_ifs"
}

ensure_real_directory() {
  requested="$1"
  allow_create="$2"
  case "$requested" in
    "$host_path"|"$host_path"/*) ;;
    *) return 1 ;;
  esac
  test ! -L "$host_path"
  test -d "$host_path"
  relative="${requested#"$host_path"}"
  relative="${relative#/}"
  current="$host_path"
  old_ifs="$IFS"
  IFS='/'
  for component in $relative; do
    case "$component" in ''|.|..) IFS="$old_ifs"; return 1 ;; esac
    current="$current/$component"
    if [ -L "$current" ]; then
      IFS="$old_ifs"
      return 1
    fi
    if [ -e "$current" ]; then
      test -d "$current" || { IFS="$old_ifs"; return 1; }
    elif [ "$allow_create" = "1" ]; then
      mkdir "$current"
    else
      IFS="$old_ifs"
      return 1
    fi
  done
  IFS="$old_ifs"
}

prepare_paths() {
  old_ifs="$IFS"
  IFS=';'
  for relative in $CREATE_DIRS; do
    [ -z "$relative" ] || ensure_real_directory "$host_path/$relative" 1
  done
  for relative in $VERIFY_DIRS; do
    [ -z "$relative" ] || ensure_real_directory "$host_path/$relative" 0
  done
  IFS="$old_ifs"
}

verify_mount() {
  if [ "$MOUNT_TYPE" = "filestore" ]; then
    awk -v source="$DEVICE_TAG" -v target="$host_path" \
      '$1 == source && $2 == target && $3 == "virtiofs" { found = 1 } END { exit !found }' \
      /proc/mounts
  fi
}

write_receipt() {
  proof_path="$1"
  ensure_real_directory "$proof_path" 0
  receipt_dir="$proof_path/$receipt_dir_name"
  ensure_real_directory "$receipt_dir" 1
  chmod 0755 "$proof_path/.nebius-cxcli" "$receipt_dir"
  probe="$proof_path/.nebius-cxcli/.write-probe.$$"
  : >"$probe"
  rm -f "$probe"
  receipt="$receipt_dir/$NODE_NAME"
  temporary="$receipt.tmp.$$"
  # Receipts contain only mount identity and boot freshness evidence. Keep the
  # writer privileged, but make the completed receipt readable by non-root
  # workload init gates; the temporary file is atomically renamed below.
  umask 022
  {
    printf 'schema=nebius-cxcli.soperator-mount-receipt.v1\n'
    printf 'boot-id=%s\n' "$(boot_id)"
    printf 'node=%s\n' "$NODE_NAME"
    printf 'mount-id=%s\n' "$MOUNT_ID"
    printf 'filesystem-id=%s\n' "$FILESYSTEM_ID"
    printf 'device-tag=%s\n' "$DEVICE_TAG"
  } >"$temporary"
  mv -f "$temporary" "$receipt"
}

verify_receipt() {
  proof_path="$1"
  receipt="$proof_path/$receipt_dir_name/$NODE_NAME"
  test -f "$receipt"
  test -w "$proof_path"
  grep -Fx 'schema=nebius-cxcli.soperator-mount-receipt.v1' "$receipt" >/dev/null
  grep -Fx "boot-id=$(boot_id)" "$receipt" >/dev/null
  grep -Fx "node=$NODE_NAME" "$receipt" >/dev/null
  grep -Fx "mount-id=$MOUNT_ID" "$receipt" >/dev/null
  grep -Fx "filesystem-id=$FILESYSTEM_ID" "$receipt" >/dev/null
  grep -Fx "device-tag=$DEVICE_TAG" "$receipt" >/dev/null
}

ready() {
  verify_mount
  for_each_path verify_receipt
}

if [ "${1:-run}" = "verify" ]; then
  ready
  exit 0
fi

test ! -L "$host_path"
mkdir -p "$host_path"
if [ "$MOUNT_TYPE" = "filestore" ] && ! verify_mount; then
  mount -t virtiofs "$DEVICE_TAG" "$host_path"
fi
verify_mount
prepare_paths
for_each_path write_receipt
while ready; do sleep 15; done
exit 1
"""

_MOUNT_GATE_SCRIPT = """set -eu
receipt=/proof/.nebius-cxcli/mount-receipts/$NODE_NAME
boot_id="$(tr -d '\r\n' </proc/sys/kernel/random/boot_id)"
test -f "$receipt"
test -r "$receipt"
grep -Fx 'schema=nebius-cxcli.soperator-mount-receipt.v1' "$receipt" >/dev/null
grep -Fx "boot-id=$boot_id" "$receipt" >/dev/null
grep -Fx "node=$NODE_NAME" "$receipt" >/dev/null
grep -Fx "mount-id=$MOUNT_ID" "$receipt" >/dev/null
[ -z "$FILESYSTEM_ID" ] || grep -Fx "filesystem-id=$FILESYSTEM_ID" "$receipt" >/dev/null
"""

_REST_JWT_CONFIG_GATE_SCRIPT = """config=/proof/etc/slurm/slurm.conf
until test -r "$config" \
  && grep -Eq '^AuthAltTypes=.*auth/jwt' "$config" \
  && grep -Eq '^AuthAltParameters=.*jwt_key=' "$config"; do
  echo 'Waiting for Slurm REST JWT configuration in the protected jail'
  sleep 2
done
"""


def soperator_rest_jwt_config_gate_patch_is_exact(
    current_script: object,
    replacement_script: object,
) -> bool:
    """Return whether replacement adds only the adapter-owned REST JWT gate."""

    return (
        isinstance(current_script, str)
        and isinstance(replacement_script, str)
        and _REST_JWT_CONFIG_GATE_SCRIPT not in current_script
        and replacement_script == current_script + _REST_JWT_CONFIG_GATE_SCRIPT
    )


def _mapping(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _soperator_monitoring_dashboards_requested(values: Mapping[str, Any]) -> bool:
    dcgm = _mapping(values.get("soperator-dcgm-exporter"))
    observability = _mapping(values.get("observability"))
    return dcgm.get("enabled") is True or observability.get("enabled") is True


def soperator_monitoring_dashboards_require_post_flux(
    values: Mapping[str, Any], *, release: SoperatorReleaseSnapshot
) -> bool:
    """Return whether the frozen release needs the digest-bound dashboard adapter."""

    if not _soperator_monitoring_dashboards_requested(values):
        return False
    chart = release.charts.get("monitoringDashboards")
    return bool(
        chart is not None and chart.digest in SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS
    )


def soperator_vm_stack_cleanup_exception(
    release: SoperatorReleaseSnapshot,
) -> dict[str, Any] | None:
    """Return the closed raw-child exception for one exact frozen package."""

    chart = release.third_party_charts.get("victoriaMetricsStack")
    if chart is None:
        return None
    identity = (
        chart.chart,
        chart.version,
        chart.repository,
        chart.package_sha256,
        chart.oci_digest,
    )
    if identity not in SOPERATOR_VM_STACK_CLEANUP_HOOK_DISABLED_PACKAGES:
        return None
    matching_nodes = tuple(
        node for node in release.release_graph if node.release_name == "soperator-fluxcd-vm-stack"
    )
    if len(matching_nodes) != 1:
        return None
    node = matching_nodes[0]
    if node.owner != "third-party" or node.chart_key != "victoriaMetricsStack":
        return None
    return {
        "id": "victoria-metrics-cleanup-hook-disabled",
        "chart": chart.chart,
        "version": chart.version,
        "repository": chart.repository,
        "packageSha256": chart.package_sha256,
        "reason": "frozen-package-uninstall-hook-image-unavailable",
    }


def _verified_soperator_dashboard_directory(
    source_root: Path,
    *,
    chart_source_path: str,
) -> Path:
    source_path = PurePosixPath(str(chart_source_path or "").strip())
    if (
        not chart_source_path
        or source_path.is_absolute()
        or str(source_path) != chart_source_path
        or any(part in {"", ".", ".."} for part in source_path.parts)
    ):
        raise ValueError("the frozen monitoring-dashboard chart source path is unsafe")
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("the frozen Soperator source root must be a real directory")
    resolved_root = source_root.resolve(strict=True)
    current = source_root
    for part in (*source_path.parts, "dashboards"):
        current = current / part
        if current.is_symlink() or not current.is_dir():
            raise ValueError("the frozen monitoring-dashboard source must contain real directories")
    try:
        current.resolve(strict=True).relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("the frozen monitoring-dashboard source escapes its source root") from exc
    return current


def render_soperator_monitoring_dashboard_documents(
    values: Mapping[str, Any],
    *,
    release: SoperatorReleaseSnapshot,
    source_root: Path,
) -> list[dict[str, Any]]:
    """Render the known-broken official dashboard chart as owned ConfigMaps."""

    if not soperator_monitoring_dashboards_require_post_flux(values, release=release):
        return []
    chart = release.charts.get("monitoringDashboards")
    if chart is None:  # Defensive: the predicate above requires this chart.
        raise ValueError("the frozen monitoring-dashboard chart snapshot is missing")
    dashboard_dir = _verified_soperator_dashboard_directory(
        source_root,
        chart_source_path=chart.source_path,
    )
    entries = sorted(dashboard_dir.iterdir(), key=lambda item: item.name)
    actual_names = tuple(item.name for item in entries)
    if actual_names != _SOPERATOR_MONITORING_DASHBOARD_FILES:
        raise ValueError(
            "the frozen monitoring-dashboard source does not match the digest-bound file set"
        )
    documents: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    chart_label = f"{chart.name}-{chart.version}".replace("+", "_")[:63].rstrip("-")
    for path in entries:
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise ValueError("every frozen monitoring dashboard must be a regular JSON file")
        payload = path.read_bytes()
        if len(payload) > _CONFIGMAP_SAFE_DATA_BYTES:
            raise ValueError(
                f"frozen monitoring dashboard {path.name} exceeds the safe ConfigMap size"
            )
        try:
            rendered_json = payload.decode("utf-8")
            parsed = json.loads(rendered_json)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"frozen monitoring dashboard {path.name} is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise ValueError(f"frozen monitoring dashboard {path.name} must be a JSON object")
        dashboard_name = path.stem.replace("_", "-")
        configmap_name = _dns_token(
            f"soperator-{dashboard_name}",
            label=f"monitoring dashboard {path.name}",
        )
        if configmap_name in seen_names:
            raise ValueError("frozen monitoring dashboards produce duplicate ConfigMap names")
        seen_names.add(configmap_name)
        payload_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        documents.append(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": configmap_name,
                    "namespace": "monitoring-system",
                    "labels": {
                        **_lifecycle_labels(
                            _labels(release),
                            SOPERATOR_LIFECYCLE_RECREATABLE,
                        ),
                        "helm.sh/chart": chart_label,
                        "app.kubernetes.io/name": chart.name,
                        "app.kubernetes.io/instance": ("soperator-fluxcd-monitoring-dashboards"),
                        "app.kubernetes.io/managed-by": "nebius-cxcli",
                        "grafana_dashboard": "1",
                    },
                    "annotations": {
                        "soperator.nebius.ai/upstream-chart-digest": chart.digest,
                        "soperator.nebius.ai/upstream-source-path": (
                            f"{chart.source_path}/dashboards/{path.name}"
                        ),
                        "soperator.nebius.ai/dashboard-sha256": payload_digest,
                    },
                },
                "data": {f"{dashboard_name}.json": rendered_json},
            }
        )
    return documents


def _dns_token(value: str, *, label: str) -> str:
    token = _DNS_TOKEN_RE.sub("-", str(value).strip().lower()).strip("-")
    if not token or len(token) > 50:
        raise ValueError(f"{label} must produce a non-empty DNS token of at most 50 chars")
    return token


def _absolute_path(value: Any, *, label: str) -> str:
    token = str(value or "").strip()
    path = PurePosixPath(token)
    if (
        not token.startswith("/")
        or token != str(path)
        or any(part in {".", ".."} for part in path.parts)
        or any(character in token for character in ("\x00", "\r", "\n", ";", "*", "?", "[", "]"))
    ):
        raise ValueError(f"{label} must be a normalized absolute path")
    return str(path)


def _device_tag(value: Any, *, label: str) -> str:
    token = str(value or "").strip()
    if not _DEVICE_TAG_RE.fullmatch(token):
        raise ValueError(f"{label} must be a valid virtiofs device tag")
    return token


def _positive_integer_resource(value: Any, *, label: str) -> bool:
    if value is None or value == "":
        return False
    token = str(value).strip()
    if isinstance(value, bool) or not token.isdigit():
        raise ValueError(f"{label} must be a non-negative integer")
    return int(token) > 0


def _below_path(value: Any, *, root: str, label: str) -> str:
    path = _absolute_path(value, label=label)
    prefix = root.rstrip("/") + "/"
    if not path.startswith(prefix):
        raise ValueError(f"{label} must be below jailRootfs.store.mountPath")
    return path


def _service_storage_contract(
    values: Mapping[str, Any],
    *,
    key: str,
    default_name: str,
    default_path: str,
    enabled: bool,
) -> dict[str, Any]:
    volume = _mapping(_mapping(values.get("volume")).get(key))
    storage = _mapping(_mapping(values.get("storage")).get(key))
    name = _dns_token(str(volume.get("name") or default_name), label=f"volume.{key}.name")
    existing_pvc_name = str(volume.get("existingPvcName") or "").strip()
    existing_pv_name = str(volume.get("existingPvName") or "").strip()
    if bool(existing_pvc_name) != bool(existing_pv_name):
        raise ValueError(f"volume.{key}.existingPvcName and existingPvName must be set together")
    if existing_pvc_name:
        existing_pvc_name = _dns_token(
            existing_pvc_name,
            label=f"volume.{key}.existingPvcName",
        )
        existing_pv_name = _dns_token(
            existing_pv_name,
            label=f"volume.{key}.existingPvName",
        )
    volume_type = str(volume.get("type") or "filestore").strip()
    if volume_type not in {"filestore", "local"}:
        raise ValueError(f"volume.{key}.type must be filestore or local")
    affinity = storage.get("matchExpressions")
    if enabled and (not isinstance(affinity, list) or not affinity):
        raise ValueError(f"storage.{key}.matchExpressions must be a non-empty list")
    tolerations = storage.get("tolerations")
    mount_path = _below_path(
        volume.get("localPath") or default_path,
        root="/mnt",
        label=f"volume.{key}.localPath",
    )
    device_tag = _device_tag(
        volume.get("filestoreDeviceName") or name,
        label=f"volume.{key}.filestoreDeviceName",
    )
    filesystem_id = _filesystem_id(values, key.replace("Spool", "-spool"))
    raw_access_modes = volume.get("existingAccessModes")
    access_modes = (
        [str(item).strip() for item in raw_access_modes if str(item).strip()]
        if isinstance(raw_access_modes, list)
        else []
    )
    return {
        "enabled": enabled,
        "adopt_existing": bool(existing_pvc_name),
        "name": name,
        "pv_name": existing_pv_name or f"{name}-pv",
        "pvc_name": existing_pvc_name or f"{name}-pvc",
        "size": str(volume.get("size") or "128Gi").strip(),
        "storage_class_name": str(
            volume.get("existingStorageClassName")
            if "existingStorageClassName" in volume
            else "slurm-local-pv"
        ),
        "access_modes": access_modes or ["ReadWriteOnce"],
        "type": volume_type,
        "device_tag": device_tag,
        # The PV points below the mount root. That subdirectory does not exist
        # on the node root filesystem, so local-volume attachment cannot race a
        # boot-time virtiofs mount.
        "mount_path": mount_path,
        "local_path": f"{mount_path}/data",
        "filesystem_id": filesystem_id or f"{volume_type}:{device_tag}",
        "affinity": affinity if isinstance(affinity, list) else [],
        "tolerations": tolerations if isinstance(tolerations, list) else [],
    }


def _jail_contract(values: Mapping[str, Any]) -> dict[str, Any]:
    rootfs = _mapping(values.get("jailRootfs"))
    if str(rootfs.get("strategy") or "activePassive") != "activePassive":
        raise ValueError("jailRootfs.strategy must be activePassive")
    active_slot = str(rootfs.get("activeSlot") or "slot-a").strip()
    passive_slot = str(rootfs.get("passiveSlot") or "slot-b").strip()
    if {active_slot, passive_slot} != {"slot-a", "slot-b"}:
        raise ValueError("active and passive Jail slots must be slot-a and slot-b")
    store = _mapping(rootfs.get("store"))
    mount_path = _below_path(
        store.get("mountPath") or "/mnt/jail-store",
        root="/mnt",
        label="jailRootfs.store.mountPath",
    )
    rootfs_path = _below_path(
        store.get("rootfsPath") or f"{mount_path}/rootfs",
        root=mount_path,
        label="jailRootfs.store.rootfsPath",
    )
    slots_payload = _mapping(rootfs.get("slots"))
    slots: dict[str, dict[str, str]] = {}
    for slot in ("slot-a", "slot-b"):
        item = _mapping(slots_payload.get(slot))
        volume_name = _dns_token(
            str(item.get("volumeSourceName") or f"jail-rootfs-{slot}"),
            label=f"jailRootfs.slots.{slot}.volumeSourceName",
        )
        slots[slot] = {
            "volume_name": volume_name,
            "pv_name": _dns_token(
                str(item.get("pvName") or f"{volume_name}-pv"),
                label=f"jailRootfs.slots.{slot}.pvName",
            ),
            "pvc_name": _dns_token(
                str(item.get("pvcName") or f"{volume_name}-pvc"),
                label=f"jailRootfs.slots.{slot}.pvcName",
            ),
            "local_path": _below_path(
                item.get("localPath") or f"{rootfs_path}/{slot}",
                root=mount_path,
                label=f"jailRootfs.slots.{slot}.localPath",
            ),
        }
    adoption = _mapping(rootfs.get("adoption"))
    active_source = str(adoption.get("activeSource") or "slot").strip()
    if active_source not in {"slot", "legacy-rootfs"}:
        raise ValueError("jailRootfs.adoption.activeSource is invalid")
    legacy_pvc = _dns_token(
        str(adoption.get("legacyPvcName") or "jail-pvc"),
        label="jailRootfs.adoption.legacyPvcName",
    )
    jail_volume = _mapping(_mapping(values.get("volume")).get("jail"))
    jail_device_tag = _device_tag(
        jail_volume.get("filestoreDeviceName") or "jail",
        label="volume.jail.filestoreDeviceName",
    )
    filesystem_id = _filesystem_id(values, "jail")
    return {
        "active_slot": active_slot,
        "passive_slot": passive_slot,
        "active_source": active_source,
        "active_pvc": legacy_pvc
        if active_source == "legacy-rootfs"
        else slots[active_slot]["pvc_name"],
        "mount_path": mount_path,
        "rootfs_path": rootfs_path,
        "filesystem_id": filesystem_id
        or f"{str(jail_volume.get('type') or 'filestore').strip()}:{jail_device_tag}",
        "slots": slots,
    }


def resolve_soperator_jail_image_authority(
    values: Mapping[str, Any], *, release: SoperatorReleaseSnapshot
) -> SoperatorJailImageAuthority:
    """Resolve the only supported target rootfs-image precedence path."""

    images = _mapping(values.get("images"))
    conflicting = sorted(
        key
        for key in ("populateJail", "populateJailRepository", "populateJailTag")
        if key in images
    )
    if conflicting:
        raise ValueError(
            "The target Jail image is owned by the frozen official release; direct images."
            + ", images.".join(conflicting)
            + " is not supported"
        )
    rootfs = _mapping(values.get("jailRootfs"))
    if "targetImage" in rootfs:
        raise ValueError(
            "jailRootfs.targetImage is not supported; the frozen official release owns "
            "the target Jail image"
        )
    upstream = str(release.populate_jail_image or "").strip()
    if not _IMMUTABLE_IMAGE_RE.fullmatch(upstream):
        raise ValueError("the frozen upstream populate-jail image is not digest-addressed")
    return SoperatorJailImageAuthority(
        image=upstream,
        source="upstream-default",
        upstream_image=upstream,
    )


def rendered_soperator_jail_image_authority(
    values: Mapping[str, Any], *, release: SoperatorReleaseSnapshot
) -> SoperatorJailImageAuthority:
    """Recover and validate image authority from compiled upstream umbrella values."""

    slurm = _mapping(values.get("slurmCluster"))
    overrides = _mapping(slurm.get("overrideValues"))
    images = _mapping(overrides.get("images"))
    image = str(images.get("populateJail") or "").strip()
    if not _IMMUTABLE_IMAGE_RE.fullmatch(image):
        raise ValueError(
            "rendered upstream Soperator values lack the digest-addressed populate-jail image"
        )
    upstream = str(release.populate_jail_image or "").strip()
    if image != upstream:
        raise ValueError(
            "rendered upstream Soperator values changed the frozen official populate-jail image"
        )
    return SoperatorJailImageAuthority(
        image=image,
        source="upstream-default",
        upstream_image=upstream,
    )


def _persistent_mounts(
    values: Mapping[str, Any], *, contract: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if contract["active_source"] == "legacy-rootfs":
        return []
    raw = values.get("jailPersistentMounts")
    rows = raw if isinstance(raw, list) else []
    mounts: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, item in enumerate(rows):
        if not isinstance(item, Mapping):
            raise ValueError(f"jailPersistentMounts[{index}] must be a mapping")
        mount_path = _absolute_path(
            item.get("mountPath"), label=f"jailPersistentMounts[{index}].mountPath"
        )
        if mount_path in seen_paths:
            raise ValueError(f"duplicate persistent mount path {mount_path}")
        seen_paths.add(mount_path)
        suffix = _dns_token(mount_path.strip("/").replace("/", "-"), label="mountPath")
        volume_name = _dns_token(
            str(item.get("name") or f"jail-persistent-{suffix}"),
            label=f"jailPersistentMounts[{index}].name",
        )
        mounts.append(
            {
                "name": volume_name,
                "mount_path": mount_path,
                "local_path": _below_path(
                    item.get("localPath"),
                    root=str(contract["mount_path"]),
                    label=f"jailPersistentMounts[{index}].localPath",
                ),
                "pv_name": _dns_token(
                    str(item.get("pvName") or f"{volume_name}-pv"),
                    label=f"jailPersistentMounts[{index}].pvName",
                ),
                "pvc_name": _dns_token(
                    str(item.get("pvcName") or f"{volume_name}-pvc"),
                    label=f"jailPersistentMounts[{index}].pvcName",
                ),
                "create_dir": mount_path in JAIL_MANDATORY_PERSISTENT_MOUNT_PATHS,
            }
        )
    external_nfs = _mapping(values.get("externalNfs"))
    if external_nfs.get("enabled") is True:
        mount_path = _absolute_path(
            external_nfs.get("mountPath") or "/home",
            label="externalNfs.mountPath",
        )
        mounts = [item for item in mounts if item["mount_path"] != mount_path]
        mounts.append(
            {
                "name": "external-nfs-home",
                "mount_path": mount_path,
                "local_path": "",
                "pv_name": "external-nfs-home-pv",
                "pvc_name": "external-nfs-home-pvc",
                "create_dir": False,
            }
        )
    return mounts


def soperator_persistent_mount_bindings(
    values: Mapping[str, Any],
) -> tuple[SoperatorPersistentMountBinding, ...]:
    """Return the exact persistent submount identities compiled for consumers."""

    contract = _jail_contract(values)
    contract["controller_spool"] = _service_storage_contract(
        values,
        key="controllerSpool",
        default_name="controller-spool",
        default_path="/mnt/controller-spool",
        enabled=True,
    )
    accounting_values = _mapping(_mapping(values.get("volume")).get("accounting"))
    contract["accounting"] = _service_storage_contract(
        values,
        key="accounting",
        default_name="accounting",
        default_path="/mnt/accounting",
        enabled=accounting_values.get("enabled") is True,
    )
    mounts = _persistent_mounts(values, contract=contract)
    _validate_adapter_identities(contract, mounts)
    return tuple(
        SoperatorPersistentMountBinding(
            name=str(item["name"]),
            mount_path=str(item["mount_path"]),
            pv_name=str(item["pv_name"]),
            pvc_name=str(item["pvc_name"]),
        )
        for item in mounts
    )


def _pvc_volume_source(name: str, pvc_name: str) -> dict[str, Any]:
    return {
        "name": name,
        "createPVC": False,
        "storageClassName": "",
        "size": "",
        "persistentVolumeClaim": {"claimName": pvc_name, "readOnly": False},
    }


def prepare_soperator_upgrade_adapter_handoff(
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove current-chart aliases that the direct-upstream adapter regenerates."""

    prepared = copy.deepcopy(dict(values))
    volume_sources = prepared.get("volumeSources")
    if not isinstance(volume_sources, list):
        return prepared
    prepared["volumeSources"] = [
        source
        for source in volume_sources
        if not (
            isinstance(source, Mapping)
            and str(source.get("name") or "").strip()
            in _PROTECTED_UPGRADE_GENERATED_VOLUME_SOURCE_NAMES
        )
    ]
    return prepared


def mount_gate_init_container(
    *,
    name: str,
    volume_name: str,
    mount_id: str,
    filesystem_id: str,
    image: str,
    readiness_script: str = "",
) -> dict[str, Any]:
    return {
        "name": _dns_token(f"mount-gate-{name}", label="mount gate name"),
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "command": ["/bin/sh", "-ec", _MOUNT_GATE_SCRIPT + readiness_script],
        "env": [
            {
                "name": "NODE_NAME",
                "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}},
            },
            {"name": "MOUNT_ID", "value": mount_id},
            {"name": "FILESYSTEM_ID", "value": filesystem_id},
        ],
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "runAsUser": 65534,
        },
        "volumeMounts": [
            {"name": volume_name, "mountPath": "/proof", "readOnly": True},
        ],
    }


def _append_mount_gate(
    role: dict[str, Any],
    *,
    name: str,
    volume_name: str,
    mount_id: str,
    filesystem_id: str,
    image: str,
    readiness_script: str = "",
) -> None:
    init_containers = role.setdefault("customInitContainers", [])
    if not isinstance(init_containers, list):
        raise ValueError("customInitContainers must be a list")
    gate = mount_gate_init_container(
        name=name,
        volume_name=volume_name,
        mount_id=mount_id,
        filesystem_id=filesystem_id,
        image=image,
        readiness_script=readiness_script,
    )
    if any(
        isinstance(item, Mapping) and str(item.get("name") or "") == gate["name"]
        for item in init_containers
    ):
        raise ValueError(f"customInitContainers collides with adapter-owned {gate['name']}")
    init_containers.append(gate)


def _partition_policy_value(value: Any) -> str:
    if isinstance(value, bool):
        return "YES" if value else "NO"
    rendered = ",".join(str(item) for item in value) if isinstance(value, list) else str(value)
    if not rendered or any(character.isspace() or character == "=" for character in rendered):
        raise ValueError("partition policy values must be non-empty single tokens")
    return rendered


def _validate_adapter_identities(contract: Mapping[str, Any], mounts: list[dict[str, str]]) -> None:
    sources = [
        "controller-spool",
        "jail",
        *(str(contract["slots"][slot]["volume_name"]) for slot in ("slot-a", "slot-b")),
        *(item["name"] for item in mounts),
    ]
    pvs = [
        str(contract["controller_spool"]["pv_name"]),
        *(str(contract["slots"][slot]["pv_name"]) for slot in ("slot-a", "slot-b")),
        *(item["pv_name"] for item in mounts),
    ]
    pvcs = [
        str(contract["controller_spool"]["pvc_name"]),
        *(str(contract["slots"][slot]["pvc_name"]) for slot in ("slot-a", "slot-b")),
        *(item["pvc_name"] for item in mounts),
    ]
    if contract["accounting"]["enabled"]:
        pvs.append(str(contract["accounting"]["pv_name"]))
        pvcs.append(str(contract["accounting"]["pvc_name"]))
    for label, names in (("volume source", sources), ("PV", pvs), ("PVC", pvcs)):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"adapter-owned {label} names collide: {', '.join(duplicates)}")


def _compile_partition_configuration(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return copy.deepcopy(value)
    result = copy.deepcopy(dict(value))
    partitions = result.get("partitions")
    if not isinstance(partitions, list):
        return result
    for index, partition in enumerate(partitions):
        if not isinstance(partition, dict):
            continue
        policy = partition.pop("policy", None)
        if policy is None:
            continue
        if not isinstance(policy, Mapping):
            raise ValueError(f"partitionConfiguration.partitions[{index}].policy must be a mapping")
        raw_config = str(partition.get("config") or "").strip()
        rendered: list[str] = []
        for field, slurm_key in _PARTITION_POLICY_FIELDS.items():
            field_value = policy.get(field)
            if field_value is None:
                continue
            if re.search(rf"(?:^|\s){re.escape(slurm_key)}=", raw_config, re.IGNORECASE):
                raise ValueError(
                    "partition policy and raw config both set "
                    f"{slurm_key} in partition {partition.get('name') or index}"
                )
            rendered.append(f"{slurm_key}={_partition_policy_value(field_value)}")
        partition["config"] = " ".join((*rendered, raw_config)).strip()
    return result


def _compile_slurm_values(
    values: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    mounts: list[dict[str, str]],
    gate_image: str,
) -> dict[str, Any]:
    unsupported = sorted(key for key in _UNSUPPORTED_DOWNSTREAM_KEYS if key in values)
    if unsupported:
        raise ValueError(
            "Unsupported downstream-only values for upstream Soperator 4.1.7: "
            + ", ".join(unsupported)
        )
    result = {
        key: copy.deepcopy(value) for key, value in values.items() if key not in _PARENT_ONLY_KEYS
    }
    if "partitionConfiguration" in result:
        result["partitionConfiguration"] = _compile_partition_configuration(
            result["partitionConfiguration"]
        )
    existing_sources = result.get("volumeSources")
    adapter_source_names = {
        "controller-spool",
        "jail",
        *(str(contract["slots"][slot]["volume_name"]) for slot in ("slot-a", "slot-b")),
        *(mount["name"] for mount in mounts),
    }
    if isinstance(existing_sources, list):
        collisions = sorted(
            {
                str(item.get("name") or "")
                for item in existing_sources
                if isinstance(item, Mapping) and str(item.get("name") or "") in adapter_source_names
            }
        )
        if collisions:
            raise ValueError(
                "volumeSources collide with adapter-owned names: " + ", ".join(collisions)
            )
    sources = (
        [copy.deepcopy(item) for item in existing_sources if isinstance(item, Mapping)]
        if isinstance(existing_sources, list)
        else []
    )
    controller_spool = contract["controller_spool"]
    sources.append(_pvc_volume_source("controller-spool", str(controller_spool["pvc_name"])))
    for slot in ("slot-a", "slot-b"):
        item = contract["slots"][slot]
        sources.append(_pvc_volume_source(item["volume_name"], item["pvc_name"]))
    sources.append(_pvc_volume_source("jail", str(contract["active_pvc"])))
    for mount in mounts:
        sources.append(_pvc_volume_source(mount["name"], mount["pvc_name"]))
    result["volumeSources"] = sources
    slurm_nodes = result.setdefault("slurmNodes", {})
    if isinstance(slurm_nodes, dict):
        rest_enabled = _mapping(slurm_nodes.get("rest")).get("enabled") is True
        for default_jail_role in ("controller", "login", "exporter"):
            role = slurm_nodes.setdefault(default_jail_role, {})
            if isinstance(role, dict):
                volumes = role.setdefault("volumes", {})
                if isinstance(volumes, dict):
                    volumes.setdefault("jail", {})
        controller = slurm_nodes.get("controller")
        if isinstance(controller, dict):
            controller_volumes = controller.setdefault("volumes", {})
            if isinstance(controller_volumes, dict):
                controller_volumes["spool"] = {"volumeSourceName": "controller-spool"}
            _append_mount_gate(
                controller,
                name="controller-spool",
                volume_name="controller-spool",
                mount_id=str(controller_spool["name"]),
                filesystem_id=str(controller_spool["filesystem_id"]),
                image=gate_image,
            )
        accounting = slurm_nodes.get("accounting")
        accounting_contract = contract["accounting"]
        if isinstance(accounting, dict) and accounting_contract["enabled"]:
            mariadb = accounting.setdefault("mariadbOperator", {})
            if isinstance(mariadb, dict):
                if accounting_contract["adopt_existing"]:
                    mariadb["protectedSecret"] = True
                mariadb["storage"] = {
                    "volumeClaimTemplate": {
                        "accessModes": accounting_contract["access_modes"],
                        "resources": {"requests": {"storage": accounting_contract["size"]}},
                        "storageClassName": accounting_contract["storage_class_name"],
                    },
                }
        for role_name, role in slurm_nodes.items():
            if not isinstance(role, dict):
                continue
            volumes = role.get("volumes")
            if not isinstance(volumes, dict):
                continue
            if "jail" in volumes:
                volumes["jail"] = {"volumeSourceName": "jail"}
                _append_mount_gate(
                    role,
                    name=f"{role_name}-jail",
                    volume_name="jail",
                    mount_id="jail",
                    filesystem_id=str(contract["filesystem_id"]),
                    image=gate_image,
                    readiness_script=(
                        _REST_JWT_CONFIG_GATE_SCRIPT
                        if role_name == "controller" and rest_enabled
                        else ""
                    ),
                )
            if mounts and role_name == "login":
                volumes["jailSubMounts"] = [
                    {
                        "name": mount["name"],
                        "mountPath": mount["mount_path"],
                        "volumeSourceName": mount["name"],
                    }
                    for mount in mounts
                ]
                for mount in mounts:
                    if mount["local_path"]:
                        _append_mount_gate(
                            role,
                            name=f"{role_name}-{mount['name']}",
                            volume_name=mount["name"],
                            mount_id="jail",
                            filesystem_id=str(contract["filesystem_id"]),
                            image=gate_image,
                        )
    return result


def _compile_nodesets_values(
    values: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    mounts: list[dict[str, str]],
    gate_image: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(values.get("priorityClasses"), list):
        result["priorityClasses"] = copy.deepcopy(values["priorityClasses"])
    raw_nodesets = values.get("nodesets")
    nodesets = copy.deepcopy(raw_nodesets) if isinstance(raw_nodesets, list) else []
    gpu_driver_jail = _mapping(values.get("gpuDriverJail"))
    gpu_driver_jail_enabled = gpu_driver_jail.get("enabled") is True
    for item in nodesets:
        if not isinstance(item, dict):
            continue
        slurmd = item.get("slurmd")
        volumes = slurmd.get("volumes") if isinstance(slurmd, dict) else None
        if not isinstance(volumes, dict):
            continue
        volumes["jail"] = {
            "persistentVolumeClaim": {
                "claimName": str(contract["active_pvc"]),
                "readOnly": False,
            }
        }
        _append_mount_gate(
            item,
            name=f"{item.get('name') or 'worker'}-jail",
            volume_name="jail",
            mount_id="jail",
            filesystem_id=str(contract["filesystem_id"]),
            image=gate_image,
        )
        if mounts:
            volumes["jailSubMounts"] = [
                {
                    "name": mount["name"],
                    "mountPath": mount["mount_path"],
                    "readOnly": False,
                    "volumeSource": {"persistentVolumeClaim": {"claimName": mount["pvc_name"]}},
                }
                for mount in mounts
            ]
            for mount in mounts:
                if mount["local_path"]:
                    _append_mount_gate(
                        item,
                        name=f"{item.get('name') or 'worker'}-{mount['name']}",
                        volume_name=mount["name"],
                        mount_id="jail",
                        filesystem_id=str(contract["filesystem_id"]),
                        image=gate_image,
                    )
        gpu = _mapping(item.get("gpu"))
        resources = _mapping(slurmd.get("resources")) if isinstance(slurmd, Mapping) else {}
        is_gpu = gpu.get("enabled") is True or any(
            _positive_integer_resource(
                resources.get(key),
                label=f"nodesets[{item.get('name') or '?'}].slurmd.resources.{key}",
            )
            for key in ("gpu", "nvidia.com/gpu")
        )
        if gpu_driver_jail_enabled and is_gpu:
            custom_mounts = volumes.setdefault("customVolumeMounts", [])
            if not isinstance(custom_mounts, list):
                raise ValueError(
                    f"nodesets[{item.get('name') or '?'}].slurmd.volumes.customVolumeMounts "
                    "must be a list"
                )
            reserved_names = {"nvidia-driver-root", "gpu-health-sysfs"}
            reserved_paths = {"/run/nvidia/driver", "/mnt/jail/sys-host"}
            if any(
                isinstance(mount, Mapping)
                and (
                    str(mount.get("name") or "") in reserved_names
                    or str(mount.get("mountPath") or "") in reserved_paths
                )
                for mount in custom_mounts
            ):
                raise ValueError(
                    f"nodesets[{item.get('name') or '?'}] collides with adapter-owned GPU mounts"
                )
            custom_mounts.extend(
                [
                    {
                        "name": "nvidia-driver-root",
                        "mountPath": "/run/nvidia/driver",
                        "readOnly": True,
                        "volumeSource": {"hostPath": {"path": "/", "type": ""}},
                    },
                    {
                        "name": "gpu-health-sysfs",
                        "mountPath": "/mnt/jail/sys-host",
                        "readOnly": True,
                        "volumeSource": {"hostPath": {"path": "/sys", "type": "Directory"}},
                    },
                ]
            )
    result["nodesets"] = nodesets
    return result


def compile_upstream_soperator_values(
    values: Mapping[str, Any], *, release: SoperatorReleaseSnapshot
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return upstream umbrella values and the normalized adapter contract."""

    pinned = release
    contract = _jail_contract(values)
    jail_image = resolve_soperator_jail_image_authority(values, release=pinned)
    contract["target_image"] = jail_image.image
    contract["target_image_source"] = jail_image.source
    contract["upstream_target_image"] = jail_image.upstream_image
    contract["controller_spool"] = _service_storage_contract(
        values,
        key="controllerSpool",
        default_name="controller-spool",
        default_path="/mnt/controller-spool",
        enabled=True,
    )
    accounting_values = _mapping(_mapping(values.get("volume")).get("accounting"))
    contract["accounting"] = _service_storage_contract(
        values,
        key="accounting",
        default_name="accounting",
        default_path="/mnt/accounting",
        enabled=accounting_values.get("enabled") is True,
    )
    mounts = _persistent_mounts(values, contract=contract)
    _validate_adapter_identities(contract, mounts)
    slurm_values = _compile_slurm_values(
        values,
        contract=contract,
        mounts=mounts,
        gate_image=pinned.mount_image,
    )
    slurm_images = slurm_values.setdefault("images", {})
    if not isinstance(slurm_images, dict):
        raise ValueError("images must be a mapping")
    slurm_images["populateJail"] = jail_image.image
    umbrella: dict[str, Any] = {
        "ns": {
            "enabled": True,
            "version": pinned.third_party_charts["namespaceRaw"].version,
        },
        "certManager": {
            "enabled": True,
            "version": pinned.third_party_charts["certManager"].version,
        },
        "mariadbOperator": {
            "enabled": True,
            "version": pinned.third_party_charts["mariadbOperator"].version,
        },
        "securityProfilesOperator": {
            "enabled": True,
            "version": pinned.third_party_charts["securityProfilesOperator"].version,
        },
        "slurmCluster": {
            "enabled": True,
            "version": pinned.release,
            "overrideValues": slurm_values,
            "slurmClusterStorage": {"enabled": False},
        },
        "nodesets": {
            "enabled": bool(values.get("nodesets")),
            "version": pinned.release,
            "overrideValues": _compile_nodesets_values(
                values,
                contract=contract,
                mounts=mounts,
                gate_image=pinned.mount_image,
            ),
        },
        "nfsServer": {"enabled": False},
        "backup": {"enabled": False, "config": {"enabled": False}},
        "observability": {"enabled": False},
        "storageClasses": {"enabled": False},
    }
    checks = _mapping(values.get("soperator-checks"))
    activechecks = _mapping(values.get("soperator-activechecks"))
    notifier = _mapping(values.get("soperator-notifier"))
    backup = _mapping(values.get("soperator-backup-config"))
    dcgm = _mapping(values.get("soperator-dcgm-exporter"))
    cert_manager = _mapping(values.get("certManager"))
    monitoring_dashboards_requested = _soperator_monitoring_dashboards_requested(values)
    if cert_manager:
        cert_manager.pop("version", None)
        cert_manager_enabled = cert_manager.pop("enabled", True) is True
        umbrella["certManager"] = {
            "enabled": cert_manager_enabled,
            "version": pinned.third_party_charts["certManager"].version,
            "overrideValues": cert_manager or None,
        }
    operator_values: dict[str, Any] = {}
    for key in ("controllerManager", "serviceMonitor"):
        if key in values:
            operator_values[key] = copy.deepcopy(values[key])
    nodeconfigurator_values: dict[str, Any] = {}
    for key in ("customContainer", "hostNetwork", "rebooter"):
        if key in values:
            nodeconfigurator_values[key] = copy.deepcopy(values[key])
    soperator: dict[str, Any] = {
        "enabled": True,
        "version": pinned.release,
        "overrideValues": operator_values or None,
        "kruise": {
            "enabled": True,
            "version": pinned.third_party_charts["kruise"].version,
        },
        "soperatorChecks": {"enabled": True},
        "nodeConfigurator": {
            "enabled": True,
            "version": pinned.release,
            "overrideValues": nodeconfigurator_values or None,
        },
        "monitoringDashboards": {"enabled": False},
    }
    if "soperator-checks" in values:
        soperator["soperatorChecks"] = {
            "enabled": checks.pop("enabled", False) is True,
            "overrideValues": checks or None,
        }
    umbrella["soperator"] = soperator
    umbrella["soperatorActiveChecks"] = {
        "enabled": activechecks.pop("enabled", True) is True,
        "version": pinned.release,
        "overrideValues": activechecks or None,
    }
    umbrella["customConfigmaps"] = {"enabled": True, "version": pinned.release}
    if "soperator-notifier" in values:
        umbrella["notifier"] = {
            "enabled": notifier.pop("enabled", False) is True,
            "version": pinned.release,
            "overrideValues": notifier or None,
        }
    if "soperator-backup-config" in values:
        umbrella["backup"] = {
            "enabled": backup.get("enabled") is True,
            "config": {
                "enabled": backup.pop("enabled", False) is True,
                "version": pinned.release,
                "overrideValues": backup or None,
            },
        }
    observability = _mapping(values.get("observability"))
    chart_exceptions: list[dict[str, Any]] = []
    if monitoring_dashboards_requested:
        vm_stack_exception = soperator_vm_stack_cleanup_exception(pinned)
        vm_stack_values = observability.get("vmStack")
        vm_stack_enabled = not (
            isinstance(vm_stack_values, Mapping) and vm_stack_values.get("enabled") is False
        )
        if vm_stack_exception is not None and vm_stack_enabled:
            chart_exceptions.append(vm_stack_exception)
        umbrella["observability"] = {
            **observability,
            "enabled": observability.pop("enabled", True) is True,
            "dcgmExporter": {
                "enabled": dcgm.pop("enabled", False) is True,
                "version": pinned.release,
                "overrideValues": dcgm or None,
            },
        }
        if not soperator_monitoring_dashboards_require_post_flux(values, release=pinned):
            soperator["monitoringDashboards"] = {
                "enabled": True,
                "version": pinned.release,
            }
    mariadb = _mapping(values.get("mariadb-operator"))
    if mariadb:
        install_operator = mariadb.pop("installOperator", None)
        umbrella["mariadbOperator"] = {
            "enabled": install_operator is not False,
            "version": pinned.third_party_charts["mariadbOperator"].version,
            "overrideValues": mariadb or None,
        }
    kruise = _mapping(values.get("kruise"))
    if kruise:
        soperator["kruise"]["overrideValues"] = kruise
    if operator_values:
        controller_manager = _mapping(operator_values.get("controllerManager"))
        manager = _mapping(controller_manager.get("manager"))
        manager_env = _mapping(manager.get("env"))
        observability_values = _mapping(umbrella.get("observability"))
        prometheus_operator = _mapping(observability_values.get("prometheusOperator"))
        manager_env.update(
            {
                "isApparmorCrdInstalled": (
                    _mapping(umbrella.get("securityProfilesOperator")).get("enabled") is True
                ),
                "isMariadbCrdInstalled": (
                    _mapping(umbrella.get("mariadbOperator")).get("enabled") is True
                ),
                "isPrometheusCrdInstalled": (
                    observability_values.get("enabled") is True
                    and prometheus_operator.get("enabled") is not False
                ),
            }
        )
        manager["env"] = manager_env
        controller_manager["manager"] = manager
        operator_values["controllerManager"] = controller_manager
        soperator["overrideValues"] = operator_values
    contract = {
        **contract,
        "persistent_mounts": mounts,
        "chart_exceptions": chart_exceptions,
    }
    return umbrella, contract


def _labels(release: SoperatorReleaseSnapshot) -> dict[str, str]:
    return {
        SOPERATOR_ADAPTER_LABEL: SOPERATOR_ADAPTER_LABEL_VALUE,
        "app.kubernetes.io/part-of": "soperator",
        "app.kubernetes.io/version": release.release,
    }


def _lifecycle_labels(labels: Mapping[str, str], lifecycle: str) -> dict[str, str]:
    return {**dict(labels), SOPERATOR_LIFECYCLE_LABEL: lifecycle}


def _local_pv_pvc(
    *,
    name: str,
    pvc_name: str,
    path: str,
    size: str,
    affinity: list[Any],
    labels: Mapping[str, str],
) -> list[dict[str, Any]]:
    protected_labels = _lifecycle_labels(labels, SOPERATOR_LIFECYCLE_PROTECTED)
    pv = {
        "apiVersion": "v1",
        "kind": "PersistentVolume",
        "metadata": {"name": name, "labels": protected_labels},
        "spec": {
            "storageClassName": "slurm-local-pv",
            "volumeMode": "Filesystem",
            "mountOptions": ["rw", "relatime", "exec", "dev"],
            "capacity": {"storage": size},
            "accessModes": ["ReadWriteMany"],
            "persistentVolumeReclaimPolicy": "Retain",
            "local": {"path": path},
            "claimRef": {"namespace": SOPERATOR_ADAPTER_NAMESPACE, "name": pvc_name},
            "nodeAffinity": {"required": {"nodeSelectorTerms": [{"matchExpressions": affinity}]}},
        },
    }
    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": pvc_name,
            "namespace": SOPERATOR_ADAPTER_NAMESPACE,
            "labels": protected_labels,
            "annotations": {"k8up.io/backup": "true"},
        },
        "spec": {
            "storageClassName": "slurm-local-pv",
            "resources": {"requests": {"storage": size}},
            "accessModes": ["ReadWriteMany"],
            "volumeName": name,
        },
    }
    return [pv, pvc]


def _local_pv_only(
    *, name: str, path: str, size: str, affinity: list[Any], labels: Mapping[str, str]
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolume",
        "metadata": {
            "name": name,
            "labels": _lifecycle_labels(labels, SOPERATOR_LIFECYCLE_PROTECTED),
        },
        "spec": {
            "storageClassName": "slurm-local-pv",
            "volumeMode": "Filesystem",
            "mountOptions": ["rw", "relatime"],
            "capacity": {"storage": size},
            "accessModes": ["ReadWriteOnce"],
            "persistentVolumeReclaimPolicy": "Retain",
            "local": {"path": path},
            "nodeAffinity": {"required": {"nodeSelectorTerms": [{"matchExpressions": affinity}]}},
        },
    }


def _mount_daemonset(
    *,
    name: str,
    volume_type: str,
    device_tag: str,
    mount_path: str,
    mount_id: str,
    filesystem_id: str,
    create_dirs: list[str],
    verify_dirs: list[str],
    affinity: list[Any],
    tolerations: list[Any],
    image: str,
    labels: Mapping[str, str],
) -> dict[str, Any]:
    if not filesystem_id:
        raise ValueError(f"{mount_id} requires an exact filesystem_id")
    recreatable_labels = _lifecycle_labels(labels, SOPERATOR_LIFECYCLE_RECREATABLE)
    app_labels = {**recreatable_labels, "app.kubernetes.io/name": name}
    return {
        "apiVersion": "apps/v1",
        "kind": "DaemonSet",
        "metadata": {
            "name": name,
            "namespace": SOPERATOR_ADAPTER_NAMESPACE,
            "labels": recreatable_labels,
        },
        "spec": {
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "template": {
                "metadata": {
                    "labels": app_labels,
                    "annotations": {
                        "soperator.nebius.ai/mount-script-sha256": hashlib.sha256(
                            _MOUNT_SCRIPT.encode()
                        ).hexdigest()
                    },
                },
                "spec": {
                    "serviceAccountName": "nebius-cxcli-soperator-mount",
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": "mount-storage",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["/bin/sh", "/mount/mount.sh"],
                            "env": [
                                {"name": "MOUNT_TYPE", "value": volume_type},
                                {"name": "DEVICE_TAG", "value": device_tag},
                                {"name": "MOUNT_PATH", "value": mount_path},
                                {"name": "MOUNT_ID", "value": mount_id},
                                {"name": "FILESYSTEM_ID", "value": filesystem_id},
                                {
                                    "name": "NODE_NAME",
                                    "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}},
                                },
                                {"name": "CREATE_DIRS", "value": ";".join(create_dirs)},
                                {"name": "VERIFY_DIRS", "value": ";".join(verify_dirs)},
                            ],
                            "securityContext": {
                                "privileged": True,
                                "readOnlyRootFilesystem": True,
                            },
                            "volumeMounts": [
                                {
                                    "name": "host-mnt",
                                    "mountPath": "/host/mnt",
                                    "mountPropagation": "Bidirectional",
                                },
                                {
                                    "name": "mount-script",
                                    "mountPath": "/mount",
                                    "readOnly": True,
                                },
                            ],
                            "startupProbe": {
                                "exec": {"command": ["/bin/sh", "/mount/mount.sh", "verify"]},
                                "failureThreshold": 30,
                                "periodSeconds": 2,
                                "timeoutSeconds": 1,
                            },
                            "readinessProbe": {
                                "exec": {"command": ["/bin/sh", "/mount/mount.sh", "verify"]},
                                "failureThreshold": 1,
                                "periodSeconds": 5,
                                "timeoutSeconds": 2,
                            },
                            "livenessProbe": {
                                "exec": {"command": ["/bin/sh", "/mount/mount.sh", "verify"]},
                                "failureThreshold": 2,
                                "periodSeconds": 10,
                                "timeoutSeconds": 2,
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "host-mnt",
                            "hostPath": {"path": "/mnt", "type": "Directory"},
                        },
                        {
                            "name": "mount-script",
                            "configMap": {
                                "name": "nebius-cxcli-soperator-mount",
                                "defaultMode": 320,
                            },
                        },
                    ],
                    "affinity": {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [{"matchExpressions": affinity}]
                            }
                        }
                    },
                    "tolerations": tolerations,
                },
            },
        },
    }


def _external_nfs_docs(
    values: Mapping[str, Any], *, mount: Mapping[str, str], labels: Mapping[str, str], size: str
) -> list[dict[str, Any]]:
    external_nfs = _mapping(values.get("externalNfs"))
    server = str(external_nfs.get("server") or "").strip()
    export_path = _absolute_path(external_nfs.get("path"), label="externalNfs.path")
    if not _NFS_SERVER_RE.fullmatch(server):
        raise ValueError("externalNfs.server must be a valid DNS name or IP address when enabled")
    shared_labels = _lifecycle_labels(labels, SOPERATOR_LIFECYCLE_SHARED)
    pv = {
        "apiVersion": "v1",
        "kind": "PersistentVolume",
        "metadata": {"name": mount["pv_name"], "labels": shared_labels},
        "spec": {
            "storageClassName": "",
            "capacity": {"storage": size},
            "accessModes": ["ReadWriteMany"],
            "persistentVolumeReclaimPolicy": "Retain",
            "mountOptions": ["rw", "hard"],
            "nfs": {"server": server, "path": export_path, "readOnly": False},
            "claimRef": {
                "namespace": SOPERATOR_ADAPTER_NAMESPACE,
                "name": mount["pvc_name"],
            },
        },
    }
    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": mount["pvc_name"],
            "namespace": SOPERATOR_ADAPTER_NAMESPACE,
            "labels": shared_labels,
        },
        "spec": {
            "storageClassName": "",
            "resources": {"requests": {"storage": size}},
            "accessModes": ["ReadWriteMany"],
            "volumeName": mount["pv_name"],
        },
    }
    return [pv, pvc]


def _filesystem_id(values: Mapping[str, Any], key: str) -> str:
    filesystems = _mapping(_mapping(values.get("sfs")).get("filesystems"))
    filesystem = _mapping(filesystems.get(key))
    existing = _mapping(filesystem.get("existing_filesystem"))
    return str(
        filesystem.get("filesystem_id") or filesystem.get("id") or existing.get("id") or ""
    ).strip()


def render_soperator_adapter_documents(
    values: Mapping[str, Any], *, release: SoperatorReleaseSnapshot
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pinned = release
    _, contract = compile_upstream_soperator_values(values, release=pinned)
    labels = _labels(pinned)
    volume = _mapping(_mapping(values.get("volume")).get("jail"))
    volume_type = str(volume.get("type") or "filestore").strip()
    if volume_type not in {"filestore", "local"}:
        raise ValueError("the Soperator adapter supports only filestore or local Jail volumes")
    size = str(volume.get("size") or "2Ti").strip()
    device = _device_tag(
        volume.get("filestoreDeviceName") or "jail",
        label="volume.jail.filestoreDeviceName",
    )
    storage = _mapping(_mapping(values.get("storage")).get("jail"))
    affinity = storage.get("matchExpressions")
    if not isinstance(affinity, list) or not affinity:
        raise ValueError("storage.jail.matchExpressions must be a non-empty list")
    tolerations = storage.get("tolerations")
    if not isinstance(tolerations, list):
        tolerations = []
    controller_spool = contract["controller_spool"]
    accounting = contract["accounting"]
    docs: list[dict[str, Any]] = [
        {
            "apiVersion": "storage.k8s.io/v1",
            "kind": "StorageClass",
            "metadata": {
                "name": "slurm-local-pv",
                "labels": _lifecycle_labels(labels, SOPERATOR_LIFECYCLE_RECREATABLE),
            },
            "provisioner": "kubernetes.io/no-provisioner",
            "volumeBindingMode": "WaitForFirstConsumer",
        }
    ]
    for slot in ("slot-a", "slot-b"):
        item = contract["slots"][slot]
        docs.extend(
            _local_pv_pvc(
                name=item["pv_name"],
                pvc_name=item["pvc_name"],
                path=item["local_path"],
                size=size,
                affinity=affinity,
                labels=labels,
            )
        )
    if not controller_spool["adopt_existing"]:
        docs.extend(
            _local_pv_pvc(
                name=str(controller_spool["pv_name"]),
                pvc_name=str(controller_spool["pvc_name"]),
                path=str(controller_spool["local_path"]),
                size=str(controller_spool["size"]),
                affinity=controller_spool["affinity"],
                labels=labels,
            )
        )
    if accounting["enabled"] and not accounting["adopt_existing"]:
        docs.append(
            _local_pv_only(
                name=str(accounting["pv_name"]),
                path=str(accounting["local_path"]),
                size=str(accounting["size"]),
                affinity=accounting["affinity"],
                labels=labels,
            )
        )
    external_nfs = _mapping(values.get("externalNfs"))
    for mount in contract["persistent_mounts"]:
        if external_nfs.get("enabled") is True and mount["name"] == "external-nfs-home":
            docs.extend(_external_nfs_docs(values, mount=mount, labels=labels, size=size))
        else:
            docs.extend(
                _local_pv_pvc(
                    name=mount["pv_name"],
                    pvc_name=mount["pvc_name"],
                    path=mount["local_path"],
                    size=size,
                    affinity=affinity,
                    labels=labels,
                )
            )
    state = {
        "schema": pinned.adapter_state_schema,
        "release": pinned.release,
        "filesystemId": _filesystem_id(values, "jail"),
        "deviceTag": device,
        "mountPath": contract["mount_path"],
        "rootfsPath": contract["rootfs_path"],
        "activeSource": contract["active_source"],
        "activeSlot": contract["active_slot"],
        "passiveSlot": contract["passive_slot"],
        "targetImage": contract["target_image"],
        "targetImageSource": contract["target_image_source"],
        "upstreamTargetImage": contract["upstream_target_image"],
        "activePvc": contract["active_pvc"],
        "slots": contract["slots"],
        "persistentMounts": contract["persistent_mounts"],
        "chartExceptions": contract["chart_exceptions"],
        "controllerSpool": {
            **controller_spool,
            "filesystem_id": _filesystem_id(values, "controller-spool"),
        },
        "accounting": {
            **accounting,
            "filesystem_id": _filesystem_id(values, "accounting"),
        },
    }
    state_json = json.dumps(state, sort_keys=True, separators=(",", ":"))
    state_digest = hashlib.sha256(state_json.encode()).hexdigest()
    docs.extend(
        [
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": SOPERATOR_ADAPTER_STATE_CONFIGMAP,
                    "namespace": SOPERATOR_ADAPTER_NAMESPACE,
                    "labels": _lifecycle_labels(labels, SOPERATOR_LIFECYCLE_PROTECTED),
                    "annotations": {"soperator.nebius.ai/state-sha256": state_digest},
                },
                "data": {"state.json": json.dumps(state, indent=2, sort_keys=True)},
            },
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "nebius-cxcli-soperator-mount",
                    "namespace": SOPERATOR_ADAPTER_NAMESPACE,
                    "labels": _lifecycle_labels(labels, SOPERATOR_LIFECYCLE_RECREATABLE),
                },
                "data": {"mount.sh": _MOUNT_SCRIPT},
            },
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {
                    "name": "nebius-cxcli-soperator-mount",
                    "namespace": SOPERATOR_ADAPTER_NAMESPACE,
                    "labels": _lifecycle_labels(labels, SOPERATOR_LIFECYCLE_RECREATABLE),
                },
                "automountServiceAccountToken": False,
            },
        ]
    )
    slot_dirs = [
        str(PurePosixPath(path).relative_to(PurePosixPath(contract["mount_path"])))
        for path in (
            contract["slots"]["slot-a"]["local_path"],
            contract["slots"]["slot-b"]["local_path"],
        )
    ]
    create_dirs = [
        *slot_dirs,
        *[
            str(
                PurePosixPath(item["local_path"]).relative_to(PurePosixPath(contract["mount_path"]))
            )
            for item in contract["persistent_mounts"]
            if item["local_path"] and item["create_dir"]
        ],
    ]
    verify_dirs = [
        str(PurePosixPath(item["local_path"]).relative_to(PurePosixPath(contract["mount_path"])))
        for item in contract["persistent_mounts"]
        if item["local_path"] and not item["create_dir"]
    ]
    docs.append(
        _mount_daemonset(
            name="nebius-cxcli-soperator-jail-mount",
            volume_type=volume_type,
            device_tag=device,
            mount_path=str(contract["mount_path"]),
            mount_id="jail",
            filesystem_id=str(contract["filesystem_id"]),
            create_dirs=create_dirs,
            verify_dirs=verify_dirs,
            affinity=affinity,
            tolerations=tolerations,
            image=pinned.mount_image,
            labels=labels,
        )
    )
    docs.append(
        _mount_daemonset(
            name="nebius-cxcli-soperator-controller-spool-mount",
            volume_type=str(controller_spool["type"]),
            device_tag=str(controller_spool["device_tag"]),
            mount_path=str(controller_spool["mount_path"]),
            mount_id=str(controller_spool["name"]),
            filesystem_id=str(controller_spool["filesystem_id"]),
            create_dirs=["data"],
            verify_dirs=[],
            affinity=controller_spool["affinity"],
            tolerations=controller_spool["tolerations"],
            image=pinned.mount_image,
            labels=labels,
        )
    )
    if accounting["enabled"] and not accounting["adopt_existing"]:
        docs.append(
            _mount_daemonset(
                name="nebius-cxcli-soperator-accounting-mount",
                volume_type=str(accounting["type"]),
                device_tag=str(accounting["device_tag"]),
                mount_path=str(accounting["mount_path"]),
                mount_id=str(accounting["name"]),
                filesystem_id=str(accounting["filesystem_id"]),
                create_dirs=["data"],
                verify_dirs=[],
                affinity=accounting["affinity"],
                tolerations=accounting["tolerations"],
                image=pinned.mount_image,
                labels=labels,
            )
        )
    return docs, state


__all__ = [
    "SOPERATOR_ADAPTER_LABEL",
    "SOPERATOR_ADAPTER_LABEL_VALUE",
    "SOPERATOR_ADAPTER_NAMESPACE",
    "SOPERATOR_ADAPTER_STATE_CONFIGMAP",
    "SOPERATOR_LIFECYCLE_LABEL",
    "SOPERATOR_LIFECYCLE_PROTECTED",
    "SOPERATOR_LIFECYCLE_RECREATABLE",
    "SOPERATOR_LIFECYCLE_SHARED",
    "SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS",
    "SOPERATOR_VALUES_CONFIGMAP",
    "SOPERATOR_VALUES_NAMESPACE",
    "SoperatorJailImageAuthority",
    "SoperatorPersistentMountBinding",
    "compile_upstream_soperator_values",
    "mount_gate_init_container",
    "prepare_soperator_upgrade_adapter_handoff",
    "soperator_adapter_state_from_documents",
    "rendered_soperator_jail_image_authority",
    "render_soperator_adapter_documents",
    "render_soperator_monitoring_dashboard_documents",
    "resolve_soperator_jail_image_authority",
    "soperator_monitoring_dashboards_require_post_flux",
    "soperator_persistent_mount_bindings",
    "soperator_persistent_mount_bindings_from_adapter_state",
    "soperator_vm_stack_cleanup_exception",
]
