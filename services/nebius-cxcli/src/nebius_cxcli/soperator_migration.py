"""Soperator migration execution checkpoints and guarded preflight."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import yaml

from .component_instances import normalize_component_token
from .deploy_validation_report import (
    DEPLOY_REPORT_FILENAME,
    build_deploy_validation_report,
    clear_deploy_validation_artifacts,
    status_label,
    validation_section_lines,
)
from .duration_utils import parse_go_duration_seconds
from .helm_readiness import list_helm_releases, verify_helm_chart_ready
from .inventory_ops import write_inventory
from .mk8s_gpu import (
    mk8s_gpu_flux_release_post_render_patches,
    mk8s_gpu_validation_specs,
    run_mk8s_gpu_validations,
)
from .mk8s_upgrade import (
    DISRUPTION_POLICY_ALLOW_UNAVAILABLE,
    minor_version_hops,
    parse_k8s_version,
    resolve_drain_timeout,
    terraform_node_group_strategy_for_policy,
    validate_node_layer_value,
    validate_os_image_value,
)
from .paths import resolve_project_paths
from .quota_checks import (
    QuotaCoverageGap,
    QuotaReport,
    QuotaRequirement,
    assess_live_quota_requirements,
    estimate_mk8s_quota_requirements,
    format_quota_report_lines,
)
from .runtime_config import to_plain_data
from .soperator_onboarding import (
    ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
    ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
    ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
    ONBOARDING_ACTION_UPGRADE_SOPERATOR,
    ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_GPU_STACK_PRESET,
    ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION,
    ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_OS,
    SOPERATOR_MIGRATION_PROFILE_DATA_FILE,
    analyze_soperator_onboarding_snapshot,
    normalize_soperator_release_version,
    soperator_migration_profile_group,
    soperator_onboarding_target,
)
from .soperator_validation import (
    SOPERATOR_CLUSTER_VALIDATION_KIND,
    SoperatorValidationCommandResult,
    run_soperator_cluster_validations,
    soperator_cluster_validation_specs,
)

SOPERATOR_MIGRATION_EXECUTION_SCHEMA = "nebius-cxcli-soperator-migration-execution/v1"
SOPERATOR_MIGRATION_CHECKPOINT_DIR = ".nebius-cxcli/soperator-migrations"
MIGRATE_REPORT_FILENAME = "migrate-report.md"
_MUTATING_PHASE_IDS = frozenset(
    {
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "create-aligned-sfs",
        "online-bulk-data-sync",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
        "retire-old-resources",
    }
)
_ORDERED_EXECUTE_PHASE_IDS = (
    "discovery-and-plan",
    "customer-approval",
    "external-node-template-upgrade",
    "target-gpu-stack-remediation",
    "create-aligned-sfs",
    "online-bulk-data-sync",
    "rolling-compute-migration",
    "final-control-plane-cutover",
    "validation-and-rollback-hold",
    "retire-old-resources",
)
_SUPPORTED_EXECUTE_PHASE_IDS = frozenset(_ORDERED_EXECUTE_PHASE_IDS)
_SOPERATOR_STORAGE_KEYS = ("jail", "controller-spool", "accounting")
_SOPERATOR_SERVICE_ROLES = ("system", "controller", "login", "accounting")
_SOPERATOR_COMPUTE_ROLES = (*_SOPERATOR_SERVICE_ROLES, "worker")
_TARGET_GPU_STACK_PHASE_ID = "target-gpu-stack-remediation"
_EXTERNAL_NODE_TEMPLATE_PHASE_ID = "external-node-template-upgrade"
_STATUS_PHASE_LABELS = {
    "discovery-and-plan": "Discovery and migration plan",
    "customer-approval": "Customer approval gate",
    _EXTERNAL_NODE_TEMPLATE_PHASE_ID: "External node-template upgrade",
    _TARGET_GPU_STACK_PHASE_ID: "Target GPU stack reconciliation",
    "create-aligned-sfs": "Aligned SFS creation",
    "online-bulk-data-sync": "Online bulk data sync",
    "rolling-compute-migration": "Rolling compute migration",
    "final-control-plane-cutover": "Final control-plane cutover",
    "validation-and-rollback-hold": "Validation and rollback hold",
    "retire-old-resources": "Retire old resources",
}
_TARGET_GPU_STACK_APP_ORDER = ("nvidia-gpu-operator", "nvidia-network-operator")
_SOPERATOR_ROLE_STORAGE_KEYS: Mapping[str, tuple[str, ...]] = {
    "system": ("jail",),
    "controller": ("jail", "controller-spool"),
    "login": ("jail",),
    "accounting": ("jail", "accounting"),
    "worker": ("jail",),
}
_SOPERATOR_ROLE_SOURCE_KIND: Mapping[str, str] = {
    "system": "cpu",
    "controller": "cpu",
    "login": "cpu",
    "accounting": "cpu",
    "worker": "gpu",
}
_SOURCE_NODE_GROUP_ID_LABEL_KEYS = ("nebius.com/node-group-id", "yandex.cloud/node-group-id")
_SOURCE_NODE_GROUP_NAME_LABEL_KEYS = ("nebius.com/node-group",)
_SOURCE_NODE_GROUP_SELECTOR_KEYS = (
    *_SOURCE_NODE_GROUP_NAME_LABEL_KEYS,
    *_SOURCE_NODE_GROUP_ID_LABEL_KEYS,
    "node.kubernetes.io/instance-type",
)
_SOPERATOR_NODESET_LABEL_KEYS = (
    "slurm.nebius.ai/nodeset-name",
    "slurm.nebius.ai/nodeset",
)
_SOURCE_WORKER_NODESET_PREFIX = "worker"
SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE = "safe-surge"
SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE = "zero-surge"
SOPERATOR_WORKER_ROLLOUT_DEFAULT_STRATEGY = SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE
SOPERATOR_WORKER_ROLLOUT_STRATEGIES = frozenset(
    {
        SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE,
        SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE,
    }
)
SOPERATOR_WORKER_ROLLOUT_DEFAULT_WAVE_PERCENT = 1
SOPERATOR_SAFE_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_SURGE_COUNT = 1
SOPERATOR_SAFE_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_UNAVAILABLE_COUNT = 0
SOPERATOR_ZERO_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_SURGE_COUNT = 0
SOPERATOR_ZERO_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_UNAVAILABLE_COUNT = 1
SOPERATOR_WORKER_GROUP_STRATEGY_DEFAULT_DRAIN_TIMEOUT = "30m"
_SOPERATOR_STORAGE_DEFAULTS: Mapping[str, Mapping[str, Any]] = {
    "jail": {
        "size_gib": 1024,
        "block_size_kib": 4,
        "mount_tag": "jail",
        "forbid_deletion": False,
        "type": "NETWORK_SSD",
    },
    "controller-spool": {
        "size_gib": 128,
        "block_size_kib": 4,
        "mount_tag": "controller-spool",
        "forbid_deletion": False,
        "type": "NETWORK_SSD",
    },
    "accounting": {
        "size_gib": 128,
        "block_size_kib": 4,
        "mount_tag": "accounting",
        "forbid_deletion": False,
        "type": "NETWORK_SSD",
    },
}
_SOPERATOR_NAMESPACE = "soperator"
_SOPERATOR_TARGET_RELEASE_NAME = "soperator"
_SOPERATOR_SOURCE_RELEASE_NAMES = frozenset(
    {
        "flux-system-soperator-fluxcd",
        "soperator-fluxcd",
        "soperator-fluxcd-values",
        "slurm-cluster-storage",
        "slurm-operator",
        "slurm-operator-crds",
        "soperator-checks",
        "soperator-activechecks",
        "soperator-controller",
        "soperator-dcgm-exporter",
        "soperator-node-configurator",
        "soperator-nodesets",
        "soperator-storageclasses",
    }
)
_SOPERATOR_SOURCE_RELEASE_RETIRE_ORDER = (
    "soperator-checks",
    "soperator-node-configurator",
    "soperator-controller",
    "soperator-nodesets",
    "slurm-cluster-storage",
    "soperator-storageclasses",
    "flux-system-soperator-fluxcd",
)
_SOPERATOR_SOURCE_RELEASE_NAME_PREFIXES = (
    "flux-system-soperator-fluxcd-",
    "soperator-fluxcd-",
)
_SOPERATOR_SOURCE_RELEASE_NAMESPACES = (_SOPERATOR_NAMESPACE, "soperator-system", "flux-system")
_SOPERATOR_SOURCE_SAFE_PRUNE_RELEASE_NAMES = frozenset(
    {
        "soperator-checks",
        "soperator-activechecks",
        "soperator-controller",
        "soperator-dcgm-exporter",
        "soperator-node-configurator",
    }
)
_SOPERATOR_SOURCE_SAFE_NAMESPACED_RESOURCE_TYPES = (
    "deployment",
    "daemonset",
    "statefulset",
    "service",
    "serviceaccount",
    "role",
    "rolebinding",
    "configmap",
    "secret",
    "job",
    "cronjob",
    "poddisruptionbudget",
    "servicemonitor",
)
_SOPERATOR_SOURCE_SAFE_CLUSTER_RESOURCE_TYPES = (
    "clusterrole",
    "clusterrolebinding",
    "mutatingwebhookconfiguration",
    "validatingwebhookconfiguration",
)
_SOPERATOR_SOURCE_CHART_PREFIXES = (
    "helm-soperator",
    "helm-slurm",
    "helm-nodeconfigurator",
    "helm-nodesets",
    "helm-nfs-server",
    "helm-storageclasses",
    "slurm-operator",
)
_ROLLING_COMPUTE_VALUES_REVISION = 8
_VALIDATION_HOLD_REVISION = 2
_TARGET_SLURM_PLUGIN_DIR = "/usr/lib/x86_64-linux-gnu/slurm"
_HELM_OWNERSHIP_CONFLICT_RE = re.compile(
    r'(?P<kind>[A-Za-z][A-Za-z0-9.]*)\s+"(?P<name>[^"]+)"\s+in namespace '
    r'"(?P<namespace>[^"]*)"\s+exists and cannot be imported into the current release',
    re.DOTALL,
)
_KUBECTL_RESOURCE_BY_KIND = {
    "ClusterRole": "clusterrole",
    "ClusterRoleBinding": "clusterrolebinding",
    "CustomResourceDefinition": "customresourcedefinition",
    "MutatingWebhookConfiguration": "mutatingwebhookconfiguration",
    "PriorityClass": "priorityclass",
    "StorageClass": "storageclass",
    "ValidatingWebhookConfiguration": "validatingwebhookconfiguration",
}
_GIB = 1024 * 1024 * 1024


class SoperatorMigrationPhasePending(RuntimeError):
    """Checkpointed migration phase pending before an unsafe mutation."""


@dataclass(frozen=True)
class SoperatorMigrationCommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class SoperatorMigrationCommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorMigrationCommandResult:
        """Run an external command for a live migration phase."""


@dataclass(frozen=True)
class SoperatorAlignedFilesystemSpec:
    key: str
    name: str
    size_gib: int
    block_size_kib: int
    mount_tag: str
    forbid_deletion: bool
    filesystem_type: str


@dataclass(frozen=True)
class SoperatorExternalNodeTemplateTarget:
    k8s_version: str
    os: str
    gpu_stack_preset: str


@dataclass(frozen=True)
class SoperatorExternalNodeTemplateRollout:
    strategy: str
    worker_wave_groups: int | None = None
    worker_wave_percent: int | None = None
    max_parallel_worker_groups: int | None = None
    strategy_max_surge_count: int = (
        SOPERATOR_ZERO_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_SURGE_COUNT
    )
    strategy_max_unavailable_count: int = (
        SOPERATOR_ZERO_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_UNAVAILABLE_COUNT
    )
    strategy_drain_timeout: str = SOPERATOR_WORKER_GROUP_STRATEGY_DEFAULT_DRAIN_TIMEOUT

    def to_manifest_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"strategy": self.strategy}
        if self.worker_wave_groups is not None:
            result["worker_wave_groups"] = self.worker_wave_groups
        if self.worker_wave_percent is not None:
            result["worker_wave_percent"] = self.worker_wave_percent
        if self.max_parallel_worker_groups is not None:
            result["max_parallel_worker_groups"] = self.max_parallel_worker_groups
        result["worker_group_strategy"] = {
            "max_surge_count": self.strategy_max_surge_count,
            "max_unavailable_count": self.strategy_max_unavailable_count,
            "drain_timeout": self.strategy_drain_timeout,
        }
        return result


@dataclass(frozen=True)
class SoperatorMigrationExecutionResult:
    checkpoint_path: Path
    completed_phases: tuple[str, ...]
    pending_phase: str
    pending_reason: str
    live_source_version: str
    target_version: str
    mutation_performed: bool
    lines: tuple[str, ...]
    report_path: Path | None = None


@dataclass(frozen=True)
class SoperatorMigrationStatusSignal:
    name: str
    state: str
    summary: str


@dataclass(frozen=True)
class SoperatorMigrationStatusSnapshot:
    phase: str
    elapsed: str
    state: str
    signals: tuple[SoperatorMigrationStatusSignal, ...]
    summary: str


def soperator_migration_checkpoint_path(config_path: Path, target_ref: str) -> Path:
    normalized = normalize_component_token(target_ref) or "mk8s"
    return config_path.parent / SOPERATOR_MIGRATION_CHECKPOINT_DIR / normalized / "checkpoint.json"


def soperator_migration_lock_path(config_path: Path, target_ref: str) -> Path:
    return soperator_migration_checkpoint_path(config_path, target_ref).with_suffix(".lock")


class SoperatorMigrationExecutionLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> SoperatorMigrationExecutionLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._acquire()
        payload = {
            "pid": os.getpid(),
            "created_at": _utc_now(),
        }
        os.write(self._fd, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
        return self

    def _acquire(self) -> None:
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            if _soperator_migration_lock_is_stale(self.path):
                with suppress(FileNotFoundError):
                    self.path.unlink()
                try:
                    self._fd = os.open(
                        str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                    )
                    return
                except FileExistsError:
                    pass
            raise RuntimeError(
                f"Soperator migration is already running for this target or left a lock: {self.path}. "
                "Remove the lock only after verifying no matching migration process is active."
            ) from exc

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        with suppress(FileNotFoundError):
            self.path.unlink()


def _soperator_migration_lock_is_stale(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, Mapping):
        return False
    try:
        pid = int(payload.get("pid", 0) or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(to_plain_data(value), sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _source_report_checkpoint_material(source_report: Mapping[str, Any]) -> dict[str, Any]:
    material = dict(copy.deepcopy(to_plain_data(source_report)))
    material.pop("generated_at", None)
    report = material.get("report")
    if isinstance(report, dict):
        report.pop("analyzed_at", None)
    snapshot = material.get("snapshot")
    if isinstance(snapshot, dict):
        snapshot["helm_releases"] = _helm_release_contracts(snapshot.get("helm_releases"))
    return material


def _source_report_checkpoint_fingerprint(source_report: Mapping[str, Any]) -> str:
    return _fingerprint(_source_report_checkpoint_material(source_report))


def _command_text(args: Sequence[str]) -> str:
    return " ".join(str(item) for item in args)


def _default_command_runner(
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int = 300,
    check: bool = True,
) -> SoperatorMigrationCommandResult:
    completed = subprocess.run(
        list(args),
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    result = SoperatorMigrationCommandResult(
        args=tuple(str(item) for item in args),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"{_command_text(args)} failed: {detail}")
    return result


def _json_from_command(
    command_runner: SoperatorMigrationCommandRunner,
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int = 300,
    check: bool = True,
) -> Mapping[str, Any]:
    result = command_runner(
        args,
        input_text=input_text,
        timeout_seconds=timeout_seconds,
        check=check,
    )
    if result.returncode != 0 and not check:
        return {}
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_command_text(args)} returned invalid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"{_command_text(args)} returned a non-object JSON payload")
    return payload


def _append_event(checkpoint: dict[str, Any], event: str, **details: Any) -> None:
    events = checkpoint.setdefault("events", [])
    if not isinstance(events, list):
        return
    item: dict[str, Any] = {"at": _utc_now(), "event": event}
    for key, value in details.items():
        if value not in (None, "", (), [], {}):
            item[key] = to_plain_data(value)
    events.append(item)


def _ordered_phase_list(phases: set[str], planned_phases: Sequence[str]) -> list[str]:
    planned_order = [phase for phase in planned_phases if phase in phases]
    remaining = sorted(phases - set(planned_order))
    return [*planned_order, *remaining]


def _phase_state(checkpoint: dict[str, Any], phase_id: str) -> dict[str, Any]:
    state = checkpoint.setdefault("phase_state", {})
    if not isinstance(state, dict):
        raise RuntimeError("Soperator migration checkpoint phase_state must be a mapping.")
    phase = state.setdefault(phase_id, {})
    if not isinstance(phase, dict):
        raise RuntimeError(
            f"Soperator migration checkpoint phase_state.{phase_id} must be a mapping."
        )
    return phase


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _onboarding_actions(onboarding: Mapping[str, Any]) -> set[str]:
    return {
        str(action or "").strip()
        for action in onboarding.get("actions", []) or []
        if str(action or "").strip()
    }


def _target_payload(payload: Mapping[str, Any], target_ref: str) -> Mapping[str, Any]:
    target = soperator_onboarding_target(payload, target_ref=target_ref)
    if not isinstance(target, Mapping):
        raise RuntimeError(f"Soperator target '{target_ref}' was not found in config.yaml.")
    return target


def _target_onboarding(payload: Mapping[str, Any], target_ref: str) -> Mapping[str, Any]:
    target = _target_payload(payload, target_ref)
    onboarding = target.get("soperator_onboarding")
    if not isinstance(onboarding, Mapping):
        raise RuntimeError(
            f"Soperator target '{target_ref}' is missing deploy.targets[].soperator_onboarding."
        )
    return onboarding


def _target_kube_context(payload: Mapping[str, Any], target_ref: str) -> str:
    target = _target_payload(payload, target_ref)
    context = str((_mapping(target)).get("kube_context", "") or "").strip()
    if not context:
        raise RuntimeError(
            f"Soperator migration execute requires deploy.targets[].kube_context for "
            f"target '{target_ref}'. Rerun onboarding with --kube-context or select a "
            "Nebius MK8s target interactively."
        )
    return context


def _target_cluster_id(payload: Mapping[str, Any], target_ref: str) -> str:
    target = _target_payload(payload, target_ref)
    return str(target.get("cluster_id", "") or "").strip()


def _external_node_template_target(
    onboarding: Mapping[str, Any],
) -> SoperatorExternalNodeTemplateTarget:
    configured = _mapping(onboarding.get("node_template_upgrade"))
    target_version = str(
        configured.get(
            "target_k8s_version",
            configured.get(
                "k8s_version",
                configured.get(
                    "version",
                    ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION,
                ),
            ),
        )
        or ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION
    ).strip()
    target_os = str(
        configured.get(
            "target_os", configured.get("os", ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_OS)
        )
        or ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_OS
    ).strip()
    gpu_stack_preset = str(
        configured.get(
            "target_gpu_stack_preset",
            configured.get(
                "gpu_stack_preset",
                configured.get(
                    "drivers_preset",
                    ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_GPU_STACK_PRESET,
                ),
            ),
        )
        or ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_GPU_STACK_PRESET
    ).strip()
    return SoperatorExternalNodeTemplateTarget(
        k8s_version=parse_k8s_version(target_version).minor_text,
        os=validate_os_image_value(target_os),
        gpu_stack_preset=validate_node_layer_value(
            gpu_stack_preset,
            flag_name="target GPU stack preset",
        ),
    )


def _positive_int_or_none(value: Any, *, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer.")
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a positive integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")
    return parsed


def _non_negative_int_or_default(
    value: Any,
    *,
    field_name: str,
    default: int,
) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative integer.")
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a non-negative integer.") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be a non-negative integer.")
    return parsed


def _rollout_drain_timeout_or_default(
    value: Any,
    *,
    field_name: str,
    default: str = SOPERATOR_WORKER_GROUP_STRATEGY_DEFAULT_DRAIN_TIMEOUT,
) -> str:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        raw = default
    if raw == "none":
        return "none"
    if re.fullmatch(r"[0-9]+", raw):
        raise ValueError(
            f"{field_name} must be 'none' or an explicit Go-style duration "
            "such as 30s, 30m, or 1h."
        )
    try:
        parse_go_duration_seconds(raw)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be 'none' or an explicit Go-style duration "
            "such as 30s, 30m, or 1h."
        ) from exc
    return raw


def _rollout_drain_timeout_cli_value(value: str) -> str:
    return "0s" if value == "none" else value


def _external_node_template_rollout_config(
    onboarding: Mapping[str, Any],
) -> Mapping[str, Any]:
    node_template = _mapping(onboarding.get("node_template_upgrade"))
    return _mapping(node_template.get("rollout"))


def _default_worker_group_strategy_values(strategy: str) -> tuple[int, int]:
    if strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE:
        return (
            SOPERATOR_SAFE_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_SURGE_COUNT,
            SOPERATOR_SAFE_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_UNAVAILABLE_COUNT,
        )
    return (
        SOPERATOR_ZERO_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_SURGE_COUNT,
        SOPERATOR_ZERO_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_UNAVAILABLE_COUNT,
    )


def resolve_external_node_template_rollout(
    onboarding: Mapping[str, Any],
    *,
    strategy: str | None = None,
    worker_wave_groups: int | None = None,
    worker_wave_percent: int | None = None,
    max_parallel_worker_groups: int | None = None,
    strategy_max_surge_count: int | None = None,
    strategy_max_unavailable_count: int | None = None,
    strategy_drain_timeout: str | None = None,
) -> SoperatorExternalNodeTemplateRollout:
    """Resolve external worker rollout settings from config plus CLI overrides."""

    config = _external_node_template_rollout_config(onboarding)
    config_strategy = normalize_component_token(str(config.get("strategy", "") or ""))
    cli_strategy = normalize_component_token(strategy or "") if strategy is not None else ""
    resolved_strategy = normalize_component_token(
        cli_strategy or config_strategy
    ) or SOPERATOR_WORKER_ROLLOUT_DEFAULT_STRATEGY
    if resolved_strategy not in SOPERATOR_WORKER_ROLLOUT_STRATEGIES:
        available = ", ".join(sorted(SOPERATOR_WORKER_ROLLOUT_STRATEGIES))
        raise ValueError(
            "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout.strategy "
            f"must be one of: {available}."
        )
    for legacy_key in (
        "max_global_unavailable_worker_nodes",
        "max_global_unavailable_worker_percent",
    ):
        if config.get(legacy_key) is not None and str(config.get(legacy_key) or "").strip():
            raise ValueError(
                "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
                f"{legacy_key} is unsupported; use worker_wave_groups or worker_wave_percent."
            )

    cli_budget_provided = worker_wave_groups is not None or worker_wave_percent is not None
    if cli_budget_provided:
        if worker_wave_groups is not None and worker_wave_percent is not None:
            raise ValueError(
                "--worker-wave-groups and --worker-wave-percent are mutually exclusive."
            )
        resolved_wave_groups = _positive_int_or_none(
            worker_wave_groups,
            field_name="--worker-wave-groups",
        )
        resolved_wave_percent = _positive_int_or_none(
            worker_wave_percent,
            field_name="--worker-wave-percent",
        )
    else:
        config_wave_groups = config.get("worker_wave_groups")
        config_wave_percent = config.get("worker_wave_percent")
        resolved_wave_groups = _positive_int_or_none(
            config_wave_groups,
            field_name=(
                "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
                "worker_wave_groups"
            ),
        )
        resolved_wave_percent = _positive_int_or_none(
            config_wave_percent,
            field_name=(
                "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
                "worker_wave_percent"
            ),
        )
        if resolved_wave_groups is not None and resolved_wave_percent is not None:
            raise ValueError(
                "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout "
                "must set only one of worker_wave_groups or worker_wave_percent."
            )
        if (
            resolved_strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE
            and resolved_wave_groups is None
            and resolved_wave_percent is None
        ):
            resolved_wave_percent = SOPERATOR_WORKER_ROLLOUT_DEFAULT_WAVE_PERCENT

    if resolved_strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE:
        zero_surge_wave_fields: list[str] = []
        if resolved_wave_groups is not None:
            zero_surge_wave_fields.append("worker_wave_groups")
        if resolved_wave_percent is not None:
            zero_surge_wave_fields.append("worker_wave_percent")
        raw_max_parallel = (
            max_parallel_worker_groups
            if max_parallel_worker_groups is not None
            else config.get("max_parallel_worker_groups")
        )
        if raw_max_parallel is not None and str(raw_max_parallel).strip():
            zero_surge_wave_fields.append("max_parallel_worker_groups")
        if zero_surge_wave_fields:
            raise ValueError(
                "zero-surge worker rollout does not use worker wave budget fields "
                f"({', '.join(zero_surge_wave_fields)}); set strategy to safe-surge "
                "or remove those fields."
            )
        resolved_wave_groups = None
        resolved_wave_percent = None
        resolved_parallel = None
    else:
        resolved_parallel = _positive_int_or_none(
            max_parallel_worker_groups
            if max_parallel_worker_groups is not None
            else config.get("max_parallel_worker_groups"),
            field_name=(
                "--max-parallel-worker-groups"
                if max_parallel_worker_groups is not None
                else (
                    "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
                    "max_parallel_worker_groups"
                )
            ),
        )
    use_config_worker_group_strategy = not cli_strategy or cli_strategy == config_strategy
    worker_group_strategy = (
        _mapping(config.get("worker_group_strategy")) if use_config_worker_group_strategy else {}
    )
    default_max_surge, default_max_unavailable = _default_worker_group_strategy_values(
        resolved_strategy
    )
    resolved_max_surge = _non_negative_int_or_default(
        strategy_max_surge_count
        if strategy_max_surge_count is not None
        else worker_group_strategy.get("max_surge_count"),
        field_name=(
            "--strategy-max-surge-count"
            if strategy_max_surge_count is not None
            else (
                "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
                "worker_group_strategy.max_surge_count"
            )
        ),
        default=default_max_surge,
    )
    resolved_max_unavailable = _non_negative_int_or_default(
        strategy_max_unavailable_count
        if strategy_max_unavailable_count is not None
        else worker_group_strategy.get("max_unavailable_count"),
        field_name=(
            "--strategy-max-unavailable-count"
            if strategy_max_unavailable_count is not None
            else (
                "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
                "worker_group_strategy.max_unavailable_count"
            )
        ),
        default=default_max_unavailable,
    )
    if resolved_max_surge == 0 and resolved_max_unavailable == 0:
        raise ValueError(
            "worker_group_strategy must keep at least one of max_surge_count or "
            "max_unavailable_count greater than zero."
        )
    if (
        resolved_strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE
        and resolved_max_surge != 0
    ):
        raise ValueError("zero-surge worker rollout requires max_surge_count to be 0.")
    if (
        resolved_strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE
        and resolved_max_surge <= 0
    ):
        raise ValueError("safe-surge worker rollout requires max_surge_count greater than 0.")
    resolved_drain_timeout = _rollout_drain_timeout_or_default(
        strategy_drain_timeout
        if strategy_drain_timeout is not None
        else worker_group_strategy.get("drain_timeout"),
        field_name=(
            "--strategy-drain-timeout"
            if strategy_drain_timeout is not None
            else (
                "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
                "worker_group_strategy.drain_timeout"
            )
        ),
    )
    return SoperatorExternalNodeTemplateRollout(
        strategy=resolved_strategy,
        worker_wave_groups=resolved_wave_groups,
        worker_wave_percent=resolved_wave_percent,
        max_parallel_worker_groups=resolved_parallel,
        strategy_max_surge_count=resolved_max_surge,
        strategy_max_unavailable_count=resolved_max_unavailable,
        strategy_drain_timeout=resolved_drain_timeout,
    )


def _worker_rollout_budget(
    rollout: SoperatorExternalNodeTemplateRollout,
    *,
    worker_group_count: int,
) -> int:
    if worker_group_count <= 0:
        return 0
    if rollout.strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE:
        return 1
    if rollout.worker_wave_groups is not None:
        budget = rollout.worker_wave_groups
    else:
        percent = rollout.worker_wave_percent
        if percent is None:
            percent = SOPERATOR_WORKER_ROLLOUT_DEFAULT_WAVE_PERCENT
        budget = math.ceil(worker_group_count * percent / 100)
    budget = max(1, min(worker_group_count, budget))
    if rollout.max_parallel_worker_groups is not None:
        budget = min(budget, rollout.max_parallel_worker_groups)
    return max(1, budget)


def _worker_rollout_budget_label(
    rollout: SoperatorExternalNodeTemplateRollout,
    *,
    worker_group_count: int,
) -> str:
    if worker_group_count <= 0:
        return "no worker node groups"
    budget = _worker_rollout_budget(rollout, worker_group_count=worker_group_count)
    if rollout.strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE:
        return "1 worker group at a time (zero-surge fallback)"
    source = (
        f"{rollout.worker_wave_groups} worker group(s)"
        if rollout.worker_wave_groups is not None
        else f"{rollout.worker_wave_percent}% of worker groups"
    )
    cap = (
        f", capped by max_parallel_worker_groups={rollout.max_parallel_worker_groups}"
        if rollout.max_parallel_worker_groups is not None
        else ""
    )
    return f"{budget} concurrent worker group(s) from {source}{cap}"


def _worker_group_strategy_label(rollout: SoperatorExternalNodeTemplateRollout) -> str:
    return (
        f"max_surge={rollout.strategy_max_surge_count}, "
        f"max_unavailable={rollout.strategy_max_unavailable_count}, "
        f"drain_timeout={rollout.strategy_drain_timeout}"
    )


def _effective_worker_group_strategy_label(
    rollout: SoperatorExternalNodeTemplateRollout,
) -> str:
    if rollout.strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE:
        return _worker_group_strategy_label(rollout) + " (zero-surge)"
    return _worker_group_strategy_label(rollout)


def _chunked_groups(
    groups: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    size: int,
) -> tuple[tuple[tuple[str, Mapping[str, Any]], ...], ...]:
    if not groups:
        return ()
    width = max(1, size)
    return tuple(tuple(groups[index : index + width]) for index in range(0, len(groups), width))


def _external_node_template_worker_waves(
    worker_groups: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    rollout: SoperatorExternalNodeTemplateRollout,
) -> tuple[tuple[tuple[str, Mapping[str, Any]], ...], ...]:
    return _chunked_groups(
        worker_groups,
        size=_worker_rollout_budget(rollout, worker_group_count=len(worker_groups)),
    )


def _external_node_template_rollout_plan_lines(
    *,
    rollout: SoperatorExternalNodeTemplateRollout,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str] = (),
) -> list[str]:
    service_groups, worker_groups = _split_external_node_template_upgrade_groups(
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    waves = _external_node_template_worker_waves(worker_groups, rollout=rollout)
    worker_names = [name for name, _raw_group in worker_groups]
    lines = [
        f"Worker rollout strategy: {rollout.strategy}",
        "Worker wave parallelism: "
        + _worker_rollout_budget_label(rollout, worker_group_count=len(worker_groups)),
        "Worker per-group strategy: " + _effective_worker_group_strategy_label(rollout),
    ]
    if rollout.strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE:
        surge_count = rollout.strategy_max_surge_count
        lines.append(
            f"Worker spare capacity required: {surge_count} surge node(s) per worker "
            "group in the active wave; --execute preflight verifies quota and capacity "
            "before any cluster mutation."
        )
        if rollout.strategy_drain_timeout != "none":
            lines.append(
                "Worker drain timeout: finite timeout may let Nebius delete a node after "
                f"{rollout.strategy_drain_timeout} if draining is still blocked."
            )
    else:
        unavailable_count = rollout.strategy_max_unavailable_count
        lines.append(
            "Worker spare capacity required: no surge worker quota; active worker group "
            f"capacity may be reduced by {unavailable_count} node"
            f"{'' if unavailable_count == 1 else 's'} during rollout."
        )
    if service_groups:
        lines.append(
            "Service-role rollout: serial zero-surge for "
            + ", ".join(name for name, _raw_group in service_groups)
            + "."
        )
    if worker_names:
        wave_text = "; ".join(
            f"wave {index}: " + ", ".join(name for name, _raw_group in wave)
            for index, wave in enumerate(waves, start=1)
        )
        lines.append("Planned worker waves: " + wave_text + ".")
    else:
        lines.append("Planned worker waves: none detected.")
    return lines


def _nebius_project_id(payload: Mapping[str, Any]) -> str:
    client_info = _mapping(payload.get("client_info"))
    nebius = _mapping(client_info.get("nebius"))
    project_id = str(nebius.get("project_id", "") or "").strip()
    if not project_id:
        raise RuntimeError("Soperator migration execute requires client_info.nebius.project_id.")
    return project_id


def _nebius_identity(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    client_info = _mapping(payload.get("client_info"))
    nebius = _mapping(client_info.get("nebius"))
    tenant_id = str(nebius.get("tenant_id", "") or "").strip()
    project_id = str(nebius.get("project_id", "") or "").strip()
    region_id = str(nebius.get("region_id", "") or "").strip()
    if not tenant_id or not project_id or not region_id:
        raise RuntimeError(
            "Soperator migration quota preflight requires "
            "client_info.nebius.tenant_id, project_id, and region_id."
        )
    return tenant_id, project_id, region_id


def _target_soperator_values(payload: Mapping[str, Any], target_ref: str) -> Mapping[str, Any]:
    apps = _mapping(payload.get("apps"))
    charts = apps.get("charts")
    if not isinstance(charts, Sequence) or isinstance(charts, (str, bytes, bytearray)):
        return {}
    for row in charts:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("id", "") or "").strip() != "soperator":
            continue
        instance_id = normalize_component_token(row.get("instance_id"))
        if instance_id != target_ref:
            continue
        return _mapping(row.get("values"))
    return {}


def _target_gpu_stack_app_rows(
    payload: Mapping[str, Any],
    target_ref: str,
) -> tuple[Mapping[str, Any], ...]:
    apps = _mapping(payload.get("apps"))
    charts = apps.get("charts")
    if not isinstance(charts, Sequence) or isinstance(charts, (str, bytes, bytearray)):
        return ()
    normalized_target = normalize_component_token(target_ref)
    rows: list[Mapping[str, Any]] = []
    for row in charts:
        if not isinstance(row, Mapping):
            continue
        component_id = normalize_component_token(row.get("id"))
        if component_id not in _TARGET_GPU_STACK_APP_ORDER:
            continue
        if row.get("enabled") is False:
            continue
        instance_id = normalize_component_token(row.get("instance_id"))
        if instance_id != normalized_target:
            continue
        rows.append(row)
    order = {component_id: index for index, component_id in enumerate(_TARGET_GPU_STACK_APP_ORDER)}
    return tuple(
        sorted(
            rows,
            key=lambda row: order.get(
                normalize_component_token(row.get("id")),
                len(order),
            ),
        )
    )


def _app_chart_ref(row: Mapping[str, Any]) -> str:
    repo = str(row.get("repo", "") or "").strip()
    chart = str(row.get("chart", "") or "").strip()
    if repo.startswith("oci://"):
        repo_ref = repo.rstrip("/")
        if chart and not repo_ref.endswith(f"/{chart}"):
            return f"{repo_ref}/{chart}"
        return repo_ref
    return chart or repo


def _target_soperator_chart_path() -> Path:
    override = str(os.environ.get("NEBIUS_CXCLI_SOPERATOR_CHART_PATH", "") or "").strip()
    if override:
        return Path(override).expanduser()
    # services/nebius-cxcli/src/nebius_cxcli/soperator_migration.py -> repo root
    return Path(__file__).resolve().parents[4] / "helm-charts" / "soperator"


def _string_sequence(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _target_role_mapping(
    payload: Mapping[str, Any], target_ref: str
) -> Mapping[str, tuple[str, ...]]:
    values = _target_soperator_values(payload, target_ref)
    raw_mapping = _mapping(values.get("nodeGroupMapping"))
    result: dict[str, tuple[str, ...]] = {}
    for role in (*_SOPERATOR_SERVICE_ROLES, "worker"):
        result[role] = tuple(
            dict.fromkeys(
                normalize_component_token(item)
                for item in _string_sequence(raw_mapping.get(role))
                if normalize_component_token(item)
            )
        )
    return result


def _approved_role_attachment_keys(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    worker_node_groups: Sequence[str],
    source_report: Mapping[str, Any] | None = None,
) -> Mapping[str, tuple[str, ...]]:
    role_mapping = _target_role_mapping(payload, target_ref)
    result: dict[str, list[str]] = {}
    worker_groups = {
        group for group in (normalize_component_token(item) for item in worker_node_groups) if group
    }
    for group in worker_groups:
        result.setdefault(group, []).append("jail")
    for role in _SOPERATOR_SERVICE_ROLES:
        for group in role_mapping.get(role, ()):
            if group in worker_groups:
                continue
            keys = result.setdefault(group, [])
            if "jail" not in keys:
                keys.append("jail")
            if role in {"system", "controller", "login"} and "controller-spool" not in keys:
                keys.append("controller-spool")
            if role == "accounting" and "accounting" not in keys:
                keys.append("accounting")
    inventory = _source_node_group_inventory(source_report or {})
    for raw_group_name, raw_group in inventory.items():
        if not isinstance(raw_group, Mapping):
            continue
        group = normalize_component_token(raw_group_name)
        role = normalize_component_token(_source_group_nodeset(raw_group))
        if not group or role not in _SOPERATOR_SERVICE_ROLES or group in worker_groups:
            continue
        keys = result.setdefault(group, [])
        if "jail" not in keys:
            keys.append("jail")
        if role in {"system", "controller", "login"} and "controller-spool" not in keys:
            keys.append("controller-spool")
        if role == "accounting" and "accounting" not in keys:
            keys.append("accounting")
    return {group: tuple(keys) for group, keys in result.items() if keys}


def _source_report_payload(
    source_report: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    snapshot = _mapping(source_report.get("snapshot"))
    report = _mapping(source_report.get("report"))
    if not snapshot or not report:
        raise RuntimeError("Soperator source discovery report is missing snapshot or report data.")
    return snapshot, report


_VOLATILE_KUBERNETES_CONTRACT_KEYS = frozenset(
    {
        "creationTimestamp",
        "deletionGracePeriodSeconds",
        "deletionTimestamp",
        "finalizers",
        "generation",
        "managedFields",
        "observedGeneration",
        "ownerReferences",
        "resourceVersion",
        "selfLink",
        "status",
        "uid",
    }
)
_KUBERNETES_CONTRACT_METADATA_KEYS = frozenset({"labels", "name", "namespace"})
_HELM_RELEASE_CONTRACT_KEYS = ("name", "namespace", "chart", "app_version")
_NODE_GROUP_CONTRACT_KEYS = ("gpu", "node_count", "labels", "selector", "taints")
_NODE_GROUP_CONTRACT_PROVIDER_ONLY_LABEL_KEYS = frozenset({"nebius.com/node-group"})
_EXECUTION_INVENTORY_STABLE_LABEL_KEYS = (
    *_SOPERATOR_NODESET_LABEL_KEYS,
    "slurm.nebius.ai/workload",
    "slurm.nebius.ai/jail",
    "nebius.com/node-group",
)
_SOPERATOR_DEFAULTED_SPEC_PATHS: Mapping[str, tuple[tuple[str, ...], ...]] = {
    "NodeSet": (
        ("spec", "initialNumberEphemeralNodes"),
        ("spec", "sssdDebugLevel"),
    ),
    "SlurmCluster": (
        ("spec", "clusterType"),
        ("spec", "plugStackConfig", "pyxis", "importerPath"),
        ("spec", "slurmNodes", "controller", "openMetrics"),
        ("spec", "slurmNodes", "controller", "sssdDebugLevel"),
        ("spec", "slurmNodes", "login", "sssdDebugLevel"),
    ),
}


def _strip_volatile_kubernetes_contract(value: Any) -> Any:
    plain = to_plain_data(value)
    if isinstance(plain, Mapping):
        result: dict[str, Any] = {}
        for key, item in plain.items():
            text_key = str(key)
            if text_key in _VOLATILE_KUBERNETES_CONTRACT_KEYS:
                continue
            result[text_key] = _strip_volatile_kubernetes_contract(item)
        return result
    if isinstance(plain, Sequence) and not isinstance(plain, (str, bytes, bytearray)):
        return [_strip_volatile_kubernetes_contract(item) for item in plain]
    return plain


def _resource_contract_metadata(metadata: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    raw = _mapping(metadata)
    for key in sorted(_KUBERNETES_CONTRACT_METADATA_KEYS):
        if key in raw:
            result[key] = _strip_volatile_kubernetes_contract(raw.get(key))
    return result


def _kubernetes_resource_contract(resource: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("apiVersion", "kind"):
        value = str(resource.get(key, "") or "").strip()
        if value:
            result[key] = value
    metadata = _resource_contract_metadata(resource.get("metadata"))
    if metadata:
        result["metadata"] = metadata
    spec = resource.get("spec")
    if isinstance(spec, Mapping):
        result["spec"] = _strip_volatile_kubernetes_contract(spec)
    _normalize_soperator_resource_contract(result)
    return result


def _drop_contract_path(value: dict[str, Any], path: tuple[str, ...]) -> None:
    cursor: Any = value
    for key in path[:-1]:
        if not isinstance(cursor, dict):
            return
        cursor = cursor.get(key)
    if isinstance(cursor, dict):
        cursor.pop(path[-1], None)


def _normalize_soperator_resource_contract(resource: dict[str, Any]) -> None:
    kind = str(resource.get("kind", "") or "").strip()
    for path in _SOPERATOR_DEFAULTED_SPEC_PATHS.get(kind, ()):
        _drop_contract_path(resource, path)


def _kubernetes_resource_contracts(value: Any) -> list[dict[str, Any]]:
    resources = [_kubernetes_resource_contract(item) for item in _sequence_of_mappings(value)]
    return sorted(
        resources,
        key=lambda item: (
            str(_mapping(item.get("metadata")).get("namespace", "") or ""),
            str(_mapping(item.get("metadata")).get("name", "") or ""),
            str(item.get("apiVersion", "") or ""),
            str(item.get("kind", "") or ""),
            _stable_json(item),
        ),
    )


def _helm_release_contracts(value: Any) -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    for item in _sequence_of_mappings(value):
        release: dict[str, Any] = {}
        for key in _HELM_RELEASE_CONTRACT_KEYS:
            release_value = str(item.get(key, "") or "").strip()
            if release_value:
                release[key] = release_value
        if release:
            releases.append(release)
    return sorted(releases, key=_stable_json)


def _node_group_contracts(
    value: Any,
    *,
    ignored_node_groups: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    raw_groups = value if isinstance(value, Mapping) else {}
    for raw_name, raw_group in raw_groups.items():
        name = normalize_component_token(raw_name)
        group = _mapping(raw_group)
        if not name or name in ignored_node_groups:
            continue
        contract: dict[str, Any] = {}
        for key in _NODE_GROUP_CONTRACT_KEYS:
            if key in group:
                contract[key] = _strip_volatile_kubernetes_contract(group.get(key))
        labels = contract.get("labels")
        if isinstance(labels, Mapping):
            contract["labels"] = dict(
                sorted(
                    (
                        str(label_key),
                        label_value,
                    )
                    for label_key, label_value in labels.items()
                    if str(label_key) not in _NODE_GROUP_CONTRACT_PROVIDER_ONLY_LABEL_KEYS
                )
            )
        allocatable = _mapping(group.get("allocatable"))
        accelerator_resources = {
            str(key): str(item)
            for key, item in sorted(allocatable.items())
            if str(key).startswith(("nvidia.com/", "rdma/"))
        }
        if accelerator_resources:
            contract["accelerator_allocatable"] = accelerator_resources
        groups[name] = contract
    return dict(sorted(groups.items()))


def _execution_source_contract(
    snapshot: Mapping[str, Any],
    *,
    ignored_node_groups: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Return the source material that must stay stable before live mutation."""
    return {
        "collection_errors": _strip_volatile_kubernetes_contract(
            snapshot.get("collection_errors", [])
        ),
        "crds": sorted(
            str(item).strip() for item in snapshot.get("crds", []) or [] if str(item).strip()
        ),
        "helm_releases": _helm_release_contracts(snapshot.get("helm_releases")),
        "namespaces": sorted(
            str(item).strip() for item in snapshot.get("namespaces", []) or [] if str(item).strip()
        ),
        "node_groups": _node_group_contracts(
            snapshot.get("node_groups"),
            ignored_node_groups=ignored_node_groups,
        ),
        "pvcs": _kubernetes_resource_contracts(snapshot.get("pvcs")),
        "pvs": _kubernetes_resource_contracts(snapshot.get("pvs")),
        "soperator_resources": _kubernetes_resource_contracts(snapshot.get("soperator_resources")),
        "storage": _strip_volatile_kubernetes_contract(_mapping(snapshot.get("storage"))),
    }


def _expected_source_version(
    *,
    onboarding: Mapping[str, Any],
    report: Mapping[str, Any],
) -> str:
    for value in (onboarding.get("source_version"), report.get("source_version")):
        normalized = normalize_soperator_release_version(str(value or ""))
        if normalized:
            return normalized
    return ""


def _phase_ids(report: Mapping[str, Any]) -> tuple[str, ...]:
    phases: list[str] = []
    for phase in _sequence_of_mappings(report.get("migration_plan")):
        phase_id = str(phase.get("id", "") or "").strip()
        if phase_id:
            phases.append(phase_id)
    return tuple(phases)


def _phase_ids_for_actions(
    *,
    report: Mapping[str, Any],
    onboarding: Mapping[str, Any],
) -> tuple[str, ...]:
    phase_ids = _phase_ids(report)
    actions = _onboarding_actions(onboarding)
    if not phase_ids and (
        ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE in actions
        or ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK in actions
        or ONBOARDING_ACTION_UPGRADE_SOPERATOR in actions
    ):
        phase_ids = ("discovery-and-plan", "customer-approval")
    if (
        phase_ids
        and ONBOARDING_ACTION_UPGRADE_SOPERATOR in actions
        and "rolling-compute-migration" not in phase_ids
    ):
        phases = list(phase_ids)
        insert_at = len(phases)
        for predecessor in (
            "online-bulk-data-sync",
            "create-aligned-sfs",
            _TARGET_GPU_STACK_PHASE_ID,
            _EXTERNAL_NODE_TEMPLATE_PHASE_ID,
            "customer-approval",
        ):
            if predecessor in phases:
                insert_at = phases.index(predecessor) + 1
                break
        phases.insert(insert_at, "rolling-compute-migration")
        phase_ids = tuple(phases)
    if (
        phase_ids
        and ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE in actions
        and _EXTERNAL_NODE_TEMPLATE_PHASE_ID not in phase_ids
    ):
        phases = list(phase_ids)
        try:
            insert_at = phases.index("customer-approval") + 1
        except ValueError:
            insert_at = min(1, len(phases))
        phases.insert(insert_at, _EXTERNAL_NODE_TEMPLATE_PHASE_ID)
        phase_ids = tuple(phases)
    if (
        phase_ids
        and ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK in actions
        and _TARGET_GPU_STACK_PHASE_ID not in phase_ids
    ):
        phases = list(phase_ids)
        try:
            insert_at = phases.index("customer-approval") + 1
        except ValueError:
            insert_at = min(1, len(phases))
        if _EXTERNAL_NODE_TEMPLATE_PHASE_ID in phases:
            insert_at = max(insert_at, phases.index(_EXTERNAL_NODE_TEMPLATE_PHASE_ID) + 1)
        phases.insert(insert_at, _TARGET_GPU_STACK_PHASE_ID)
        phase_ids = tuple(phases)
    return phase_ids


def _normalize_worker_node_groups(worker_node_groups: Sequence[str]) -> tuple[str, ...]:
    groups: list[str] = []
    for raw_value in worker_node_groups:
        for item in str(raw_value or "").split(","):
            normalized = normalize_component_token(item)
            if normalized:
                groups.append(normalized)
    return tuple(dict.fromkeys(groups))


def _source_node_group_inventory(source_report: Mapping[str, Any]) -> Mapping[str, Any]:
    snapshot = _mapping(source_report.get("snapshot"))
    node_groups = snapshot.get("node_groups")
    return node_groups if isinstance(node_groups, Mapping) else {}


def _source_group_labels(source_group: Mapping[str, Any]) -> Mapping[str, str]:
    labels: dict[str, str] = {}
    for key in ("labels", "node_labels"):
        raw_labels = source_group.get(key)
        if isinstance(raw_labels, Mapping):
            labels.update({str(label): str(value) for label, value in raw_labels.items()})
    return labels


def _source_group_node_group_name(source_group: Mapping[str, Any]) -> str:
    labels = _source_group_labels(source_group)
    for key in _SOURCE_NODE_GROUP_NAME_LABEL_KEYS:
        value = str(labels.get(key, "") or "").strip()
        if value:
            return value
    return str(
        source_group.get("node_group_name", "") or source_group.get("name", "") or ""
    ).strip()


def _source_group_nodeset(source_group: Mapping[str, Any]) -> str:
    labels = _source_group_labels(source_group)
    for key in _SOPERATOR_NODESET_LABEL_KEYS:
        value = str(labels.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _source_group_workload(source_group: Mapping[str, Any]) -> str:
    return str(_source_group_labels(source_group).get("slurm.nebius.ai/workload", "") or "").strip()


def _source_group_is_gpu(source_group: Mapping[str, Any]) -> bool:
    if _bool_value(source_group.get("gpu"), fallback=False):
        return True
    labels = _source_group_labels(source_group)
    if _bool_value(labels.get("nebius.com/gpu"), fallback=False):
        return True
    allocatable = _mapping(source_group.get("allocatable"))
    return any(
        str(key).startswith("nvidia.com/gpu") and str(value) not in {"", "0"}
        for key, value in allocatable.items()
    )


def _source_group_aliases(group_name: str, source_group: Mapping[str, Any]) -> tuple[str, ...]:
    labels = _source_group_labels(source_group)
    aliases = [
        group_name,
        _source_group_node_group_id(source_group),
        _source_group_node_group_name(source_group),
        _source_group_nodeset(source_group),
        _source_group_workload(source_group),
        str(source_group.get("node_group_id", "") or source_group.get("id", "") or ""),
        str(source_group.get("node_group_name", "") or source_group.get("name", "") or ""),
    ]
    aliases.extend(str(labels.get(key, "") or "") for key in _SOURCE_NODE_GROUP_SELECTOR_KEYS)
    return tuple(
        dict.fromkeys(
            normalize_component_token(alias)
            for alias in aliases
            if normalize_component_token(alias)
        )
    )


def _source_group_alias_map(
    inventory: Mapping[str, Any],
) -> tuple[dict[str, str], set[str]]:
    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()
    for raw_group_name, raw_group in inventory.items():
        group_name = normalize_component_token(raw_group_name)
        if not group_name or not isinstance(raw_group, Mapping):
            continue
        for alias in _source_group_aliases(group_name, raw_group):
            existing = aliases.get(alias)
            if existing and existing != group_name:
                ambiguous.add(alias)
                continue
            aliases[alias] = group_name
    for alias in ambiguous:
        aliases.pop(alias, None)
    return aliases, ambiguous


def _source_group_is_worker(group_name: str, source_group: Mapping[str, Any]) -> bool:
    nodeset = normalize_component_token(_source_group_nodeset(source_group))
    if nodeset.startswith(_SOURCE_WORKER_NODESET_PREFIX):
        return True
    name = normalize_component_token(_source_group_node_group_name(source_group))
    if name.startswith(_SOURCE_WORKER_NODESET_PREFIX):
        return True
    normalized_group = normalize_component_token(group_name)
    if normalized_group.startswith(_SOURCE_WORKER_NODESET_PREFIX):
        return True
    workload = normalize_component_token(_source_group_workload(source_group))
    if workload == "gpu" and _source_group_is_gpu(source_group):
        return True
    return _source_group_is_gpu(source_group) and nodeset not in _SOPERATOR_SERVICE_ROLES


def _source_group_service_quiesce_role(
    group_name: str,
    source_group: Mapping[str, Any],
) -> str:
    for candidate in (
        group_name,
        _source_group_nodeset(source_group),
        _source_group_node_group_name(source_group),
        _source_group_workload(source_group),
    ):
        normalized = normalize_component_token(candidate)
        if normalized in {"accounting", "controller", "login"}:
            return normalized
    return ""


def _infer_worker_node_groups(source_report: Mapping[str, Any]) -> tuple[str, ...]:
    inventory = _source_node_group_inventory(source_report)
    candidates: list[tuple[int, str]] = []
    for raw_group_name, raw_group in inventory.items():
        group_name = normalize_component_token(raw_group_name)
        if not group_name or not isinstance(raw_group, Mapping):
            continue
        if not _source_group_is_worker(group_name, raw_group):
            continue
        gpu_rank = 0 if _source_group_is_gpu(raw_group) else 1
        candidates.append((gpu_rank, group_name))
    return tuple(group for _gpu_rank, group in sorted(dict.fromkeys(candidates)))


def _validate_worker_node_groups(
    *,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str] = (),
) -> tuple[str, ...]:
    inventory = _source_node_group_inventory(source_report)
    alias_map, ambiguous_aliases = _source_group_alias_map(inventory)
    requested_groups = _normalize_worker_node_groups(worker_node_groups)
    if requested_groups:
        normalized_groups = tuple(
            alias_map.get(group, group)
            for group in requested_groups
            if group not in ambiguous_aliases
        )
    else:
        normalized_groups = _infer_worker_node_groups(source_report)
    if not normalized_groups:
        raise RuntimeError(
            "Soperator compute migration could not infer source worker node groups from "
            "the accepted onboarding inventory. Rerun `nebius-cxcli ext-soperator onboard` "
            "against the Nebius MK8s target so cxcli can read live node-group names and "
            "slurm.nebius.ai/nodeset worker labels."
        )
    available = {normalize_component_token(name) for name in inventory}
    missing = tuple(group for group in normalized_groups if group not in available)
    if missing:
        raise RuntimeError(
            "Soperator compute migration worker node group(s) were not found in source "
            "discovery inventory: "
            + ", ".join(missing)
            + ". Available groups: "
            + ", ".join(sorted(group for group in available if group))
        )
    return tuple(dict.fromkeys(normalized_groups))


def _positive_int(value: Any, *, fallback: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _ceil_cpu_quantity(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("m"):
        try:
            milli = int(text[:-1])
        except ValueError:
            return None
        if milli <= 0:
            return None
        return max(1, (milli + 999) // 1000)
    with suppress(ValueError):
        parsed = float(text)
        if parsed > 0:
            return max(1, math.ceil(parsed))
    return None


def _nonnegative_int(value: Any, *, fallback: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def _bool_value(value: Any, *, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return fallback


def _nebius_filesystem_type(value: Any) -> str:
    text = str(value or "NETWORK_SSD").strip().lower().replace("-", "_")
    allowed = {"network_ssd", "network_hdd", "weka", "vast"}
    return text if text in allowed else "network_ssd"


def _aligned_filesystem_specs(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
) -> tuple[SoperatorAlignedFilesystemSpec, ...]:
    values = _target_soperator_values(payload, target_ref)
    configured = _mapping(_mapping(values.get("sfs")).get("filesystems"))
    specs: list[SoperatorAlignedFilesystemSpec] = []
    for key in _SOPERATOR_STORAGE_KEYS:
        defaults = _SOPERATOR_STORAGE_DEFAULTS[key]
        configured_spec = _mapping(configured.get(key))
        name_template = str(configured_spec.get("name") or f"{target_ref}-{key}")
        name = name_template.replace("{target}", target_ref)
        specs.append(
            SoperatorAlignedFilesystemSpec(
                key=key,
                name=name,
                size_gib=_positive_int(
                    configured_spec.get("size_gib", configured_spec.get("size_gibibytes")),
                    fallback=int(defaults["size_gib"]),
                ),
                block_size_kib=_positive_int(
                    configured_spec.get("block_size_kib"),
                    fallback=int(defaults["block_size_kib"]),
                ),
                mount_tag=str(configured_spec.get("mount_tag") or defaults["mount_tag"]),
                forbid_deletion=_bool_value(
                    configured_spec.get("forbid_deletion"),
                    fallback=bool(defaults["forbid_deletion"]),
                ),
                filesystem_type=_nebius_filesystem_type(
                    configured_spec.get(
                        "type", configured_spec.get("filesystem_type", defaults["type"])
                    )
                ),
            )
        )
    return tuple(specs)


def _filesystem_metadata(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _filesystem_id(payload: Mapping[str, Any]) -> str:
    return str(_filesystem_metadata(payload).get("id", "") or "").strip()


def _filesystem_spec(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    spec = payload.get("spec")
    return spec if isinstance(spec, Mapping) else {}


def _validate_existing_filesystem(
    spec: SoperatorAlignedFilesystemSpec, payload: Mapping[str, Any]
) -> None:
    live_spec = _filesystem_spec(payload)
    mismatches: list[str] = []
    live_size = _positive_int(live_spec.get("size_gibibytes"), fallback=spec.size_gib)
    if live_size != spec.size_gib:
        mismatches.append(f"size_gibibytes={live_size} expected {spec.size_gib}")
    live_block = _positive_int(
        live_spec.get("block_size_bytes"), fallback=spec.block_size_kib * 1024
    )
    if live_block != spec.block_size_kib * 1024:
        mismatches.append(f"block_size_bytes={live_block} expected {spec.block_size_kib * 1024}")
    live_type = _nebius_filesystem_type(live_spec.get("type"))
    if live_type != spec.filesystem_type:
        mismatches.append(f"type={live_type} expected {spec.filesystem_type}")
    if mismatches:
        raise SoperatorMigrationPhasePending(
            f"existing aligned SFS filesystem '{spec.name}' is incompatible: "
            + "; ".join(mismatches)
            + ". Rename or fix the existing filesystem before resuming."
        )


def _get_filesystem_by_name(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    project_id: str,
    name: str,
) -> Mapping[str, Any]:
    result = command_runner(
        [
            "nebius",
            "compute",
            "filesystem",
            "get-by-name",
            "--parent-id",
            project_id,
            "--name",
            name,
            "--format",
            "json",
        ],
        timeout_seconds=120,
        check=False,
    )
    if result.returncode != 0:
        return {}
    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"nebius compute filesystem get-by-name returned invalid JSON: {exc}"
        ) from exc
    return parsed if isinstance(parsed, Mapping) else {}


def _create_filesystem(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    project_id: str,
    spec: SoperatorAlignedFilesystemSpec,
) -> Mapping[str, Any]:
    args = [
        "nebius",
        "compute",
        "filesystem",
        "create",
        "--parent-id",
        project_id,
        "--name",
        spec.name,
        "--type",
        spec.filesystem_type,
        "--size-gibibytes",
        str(spec.size_gib),
        "--block-size-bytes",
        str(spec.block_size_kib * 1024),
        "--format",
        "json",
        "--timeout",
        "30m",
    ]
    if spec.forbid_deletion:
        args.insert(-4, "--forbid-deletion")
    return _json_from_command(command_runner, args, timeout_seconds=1800)


def _source_group_node_group_id(source_group: Mapping[str, Any]) -> str:
    labels = _source_group_labels(source_group)
    for key in _SOURCE_NODE_GROUP_ID_LABEL_KEYS:
        value = str(labels.get(key, "") or "").strip()
        if value:
            return value
    return str(source_group.get("node_group_id", "") or source_group.get("id", "") or "").strip()


def _node_group_payload_by_id(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    node_group_id: str,
) -> Mapping[str, Any]:
    return _json_from_command(
        command_runner,
        [
            "nebius",
            "mk8s",
            "node-group",
            "get",
            node_group_id,
            "--format",
            "json",
        ],
        timeout_seconds=120,
    )


def _cluster_payload_by_id(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    cluster_id: str,
) -> Mapping[str, Any]:
    return _json_from_command(
        command_runner,
        [
            "nebius",
            "mk8s",
            "cluster",
            "get",
            cluster_id,
            "--format",
            "json",
        ],
        timeout_seconds=120,
    )


def _cluster_control_plane_version(cluster: Mapping[str, Any]) -> str:
    spec = _mapping(cluster.get("spec"))
    control_plane = _mapping(spec.get("control_plane"))
    if not control_plane:
        control_plane = _mapping(spec.get("controlPlane"))
    return str(
        control_plane.get("version") or spec.get("version") or cluster.get("version") or ""
    ).strip()


def _cluster_update_command(
    cluster_id: str,
    *,
    control_plane_version: str,
    timeout: str,
) -> list[str]:
    return [
        "nebius",
        "mk8s",
        "cluster",
        "update",
        cluster_id,
        "--control-plane-version",
        control_plane_version,
        "--format",
        "json",
        "--timeout",
        timeout,
    ]


def _update_cluster_control_plane(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    cluster_id: str,
    control_plane_version: str,
) -> Mapping[str, Any]:
    return _json_from_command(
        command_runner,
        _cluster_update_command(
            cluster_id,
            control_plane_version=control_plane_version,
            timeout="60m",
        ),
        timeout_seconds=3600,
    )


def _node_group_strategy(node_group: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(_mapping(node_group.get("spec")).get("strategy"))


def _minor_version_text_or_empty(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return parse_k8s_version(raw).minor_text
    except ValueError:
        return raw.lstrip("v")


def _version_prefix_matches(raw: Any, target_version: str) -> bool:
    current = _minor_version_text_or_empty(raw)
    return bool(current) and current == parse_k8s_version(target_version).minor_text


def _minor_version_at_least(current: str, target: str) -> bool:
    try:
        current_version = parse_k8s_version(current)
        target_version = parse_k8s_version(target)
    except ValueError as exc:
        raise RuntimeError(
            "external MK8s node-template Kubernetes version check could not parse "
            f"live control plane '{current or 'unknown'}' or target '{target or 'unknown'}'. "
            "Rerun onboarding after confirming the live MK8s version is reported in a "
            "supported semantic form such as 1.33."
        ) from exc
    return (current_version.major, current_version.minor) >= (
        target_version.major,
        target_version.minor,
    )


def _external_node_template_k8s_downgrade_error(current: str, target: str) -> str:
    if not current:
        return ""
    try:
        current_version = parse_k8s_version(current)
        target_version = parse_k8s_version(target)
    except ValueError:
        return (
            "external MK8s node-template Kubernetes version check could not parse "
            f"live control plane '{current or 'unknown'}' or target '{target or 'unknown'}'. "
            "Rerun onboarding after confirming the live MK8s version is reported in a "
            "supported semantic form such as 1.33."
        )
    if (target_version.major, target_version.minor) >= (
        current_version.major,
        current_version.minor,
    ):
        return ""
    return (
        "external MK8s node-template downgrade is not supported: "
        f"live control plane is {current_version.minor_text}, target is "
        f"{target_version.minor_text}. Update "
        "deploy.targets[].soperator_onboarding.node_template_upgrade.target_k8s_version "
        "to the live or newer version, or use a blue/green replacement path."
    )


def _ensure_external_node_template_k8s_not_downgrade(current: str, target: str) -> None:
    message = _external_node_template_k8s_downgrade_error(current, target)
    if message:
        raise RuntimeError(message)


def _node_group_version(node_group: Mapping[str, Any]) -> str:
    return str(_mapping(node_group.get("spec")).get("version", "") or "").strip()


def _node_group_template(node_group: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(_mapping(node_group.get("spec")).get("template"))


def _node_group_template_os(node_group: Mapping[str, Any]) -> str:
    raw_os = _node_group_template(node_group).get("os")
    if isinstance(raw_os, Mapping):
        return str(raw_os.get("name") or raw_os.get("id") or raw_os.get("value") or "").strip()
    return str(raw_os or "").strip()


def _node_group_template_gpu_drivers_preset(node_group: Mapping[str, Any]) -> str:
    template = _node_group_template(node_group)
    gpu_settings = _mapping(template.get("gpu_settings"))
    if not gpu_settings:
        gpu_settings = _mapping(template.get("gpuSettings"))
    return str(
        gpu_settings.get("drivers_preset")
        or gpu_settings.get("driversPreset")
        or template.get("gpu_settings_drivers_preset")
        or template.get("gpuSettingsDriversPreset")
        or ""
    ).strip()


def _external_node_template_update_args(
    *,
    node_group: Mapping[str, Any],
    source_group: Mapping[str, Any],
    target: SoperatorExternalNodeTemplateTarget,
) -> tuple[str, ...]:
    args: list[str] = []
    if not _version_prefix_matches(_node_group_version(node_group), target.k8s_version):
        args.extend(["--version", target.k8s_version])
    if _node_group_template_os(node_group) != target.os:
        args.extend(["--template-os", target.os])
    if _source_group_is_gpu(source_group):
        current_preset = _node_group_template_gpu_drivers_preset(node_group)
        if current_preset != target.gpu_stack_preset:
            args.extend(
                [
                    "--template-gpu-settings-drivers-preset",
                    target.gpu_stack_preset,
                ]
            )
    return tuple(args)


def _external_node_template_clears_cpu_gpu_settings(
    *,
    node_group: Mapping[str, Any],
    source_group: Mapping[str, Any],
) -> bool:
    return not _source_group_is_gpu(source_group) and bool(
        _node_group_template_gpu_drivers_preset(node_group)
    )


def _nonnegative_int_text(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        number = int(raw)
    except ValueError:
        return None
    return str(number) if number >= 0 else None


def _strategy_limit_args(
    strategy: Mapping[str, Any],
    *,
    snake_key: str,
    camel_key: str,
    count_flag: str,
    percent_flag: str,
    default_count: int,
) -> list[str]:
    raw_limit = strategy.get(snake_key)
    if not isinstance(raw_limit, Mapping):
        raw_limit = strategy.get(camel_key)
    limit = _mapping(raw_limit)
    count = _nonnegative_int_text(limit.get("count"))
    if count is not None:
        return [count_flag, count]
    percent = _nonnegative_int_text(limit.get("percent"))
    if percent is not None:
        return [percent_flag, percent]
    return [count_flag, str(default_count)]


def _strategy_drain_timeout_text(value: Any, *, default: str) -> str:
    if isinstance(value, Mapping):
        seconds = _nonnegative_int_text(value.get("seconds"))
        nanos = _nonnegative_int_text(value.get("nanos"))
        if seconds is not None and nanos in {None, "0"}:
            return f"{seconds}s"
    raw = str(value or "").strip()
    return raw or default


def _node_group_strategy_cli_args(
    strategy: Mapping[str, Any],
    *,
    default_max_surge_count: int,
    default_max_unavailable_count: int,
    default_drain_timeout: str,
) -> list[str]:
    args: list[str] = []
    args.extend(
        _strategy_limit_args(
            strategy,
            snake_key="max_surge",
            camel_key="maxSurge",
            count_flag="--strategy-max-surge-count",
            percent_flag="--strategy-max-surge-percent",
            default_count=default_max_surge_count,
        )
    )
    args.extend(
        _strategy_limit_args(
            strategy,
            snake_key="max_unavailable",
            camel_key="maxUnavailable",
            count_flag="--strategy-max-unavailable-count",
            percent_flag="--strategy-max-unavailable-percent",
            default_count=default_max_unavailable_count,
        )
    )
    args.extend(
        [
            "--strategy-drain-timeout",
            _strategy_drain_timeout_text(
                strategy.get("drain_timeout", strategy.get("drainTimeout")),
                default=default_drain_timeout,
            ),
        ]
    )
    return args


def _soperator_zero_surge_strategy_cli_args() -> list[str]:
    timeout = resolve_drain_timeout(DISRUPTION_POLICY_ALLOW_UNAVAILABLE, "auto")
    strategy = terraform_node_group_strategy_for_policy(
        DISRUPTION_POLICY_ALLOW_UNAVAILABLE,
        timeout,
    )
    if strategy is None:
        raise RuntimeError("Soperator external migration requires a zero-surge strategy.")
    return _node_group_strategy_cli_args(
        strategy,
        default_max_surge_count=0,
        default_max_unavailable_count=1,
        default_drain_timeout=timeout.label,
    )


def _soperator_worker_strategy_cli_args(
    rollout: SoperatorExternalNodeTemplateRollout,
) -> list[str]:
    return [
        "--strategy-max-surge-count",
        str(rollout.strategy_max_surge_count),
        "--strategy-max-unavailable-count",
        str(rollout.strategy_max_unavailable_count),
        "--strategy-drain-timeout",
        _rollout_drain_timeout_cli_value(rollout.strategy_drain_timeout),
    ]


def _external_node_template_strategy_cli_args(
    rollout: SoperatorExternalNodeTemplateRollout,
    *,
    worker_group: bool,
) -> tuple[list[str], str]:
    if worker_group:
        return _soperator_worker_strategy_cli_args(rollout), rollout.strategy
    return _soperator_zero_surge_strategy_cli_args(), "zero-surge"


def _node_group_update_command(
    node_group_id: str,
    *,
    update_args: Sequence[str],
    strategy_args: Sequence[str],
    timeout: str,
) -> list[str]:
    return [
        "nebius",
        "mk8s",
        "node-group",
        "update",
        node_group_id,
        *update_args,
        *strategy_args,
        "--format",
        "json",
        "--timeout",
        timeout,
    ]


def _set_node_group_strategy_from_args(
    payload: dict[str, Any],
    strategy_args: Sequence[str],
) -> None:
    spec = payload.setdefault("spec", {})
    if not isinstance(spec, dict):
        spec = {}
        payload["spec"] = spec
    strategy = spec.setdefault("strategy", {})
    if not isinstance(strategy, dict):
        strategy = {}
        spec["strategy"] = strategy
    command = tuple(strategy_args)
    if "--strategy-max-surge-count" in command:
        strategy["max_surge"] = {"count": command[command.index("--strategy-max-surge-count") + 1]}
    if "--strategy-max-surge-percent" in command:
        strategy["max_surge"] = {
            "percent": command[command.index("--strategy-max-surge-percent") + 1]
        }
    if "--strategy-max-unavailable-count" in command:
        strategy["max_unavailable"] = {
            "count": command[command.index("--strategy-max-unavailable-count") + 1]
        }
    if "--strategy-max-unavailable-percent" in command:
        strategy["max_unavailable"] = {
            "percent": command[command.index("--strategy-max-unavailable-percent") + 1]
        }
    if "--strategy-drain-timeout" in command:
        strategy["drain_timeout"] = _protobuf_duration_text(
            command[command.index("--strategy-drain-timeout") + 1]
        )


def _protobuf_duration_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return raw
    match = re.fullmatch(r"(?P<minutes>\d+)m", raw)
    if match:
        return f"{int(match.group('minutes')) * 60}s"
    match = re.fullmatch(r"(?P<hours>\d+)h", raw)
    if match:
        return f"{int(match.group('hours')) * 3600}s"
    return raw


def _node_group_full_update_payload(
    *,
    original_node_group: Mapping[str, Any],
    update_args: Sequence[str],
    strategy_args: Sequence[str],
    clear_template_gpu_settings: bool,
) -> dict[str, Any]:
    payload = dict(copy.deepcopy(to_plain_data(original_node_group)))
    payload.pop("status", None)
    spec = payload.setdefault("spec", {})
    if not isinstance(spec, dict):
        spec = {}
        payload["spec"] = spec
    template = spec.setdefault("template", {})
    if not isinstance(template, dict):
        template = {}
        spec["template"] = template
    command = tuple(update_args)
    if "--version" in command:
        spec["version"] = command[command.index("--version") + 1]
    if "--template-os" in command:
        template["os"] = command[command.index("--template-os") + 1]
    if "--template-filesystems" in command:
        template["filesystems"] = json.loads(command[command.index("--template-filesystems") + 1])
    if "--template-gpu-settings-drivers-preset" in command:
        preset = command[command.index("--template-gpu-settings-drivers-preset") + 1]
        gpu_settings = template.setdefault("gpu_settings", {})
        if not isinstance(gpu_settings, dict):
            gpu_settings = {}
            template["gpu_settings"] = gpu_settings
        gpu_settings["drivers_preset"] = preset
    if clear_template_gpu_settings:
        template.pop("gpu_settings", None)
        template.pop("gpuSettings", None)
    _set_node_group_strategy_from_args(payload, strategy_args)
    return payload


def _node_group_update_file_command(
    node_group_id: str,
    *,
    file_path: Path,
    timeout: str,
) -> list[str]:
    return [
        "nebius",
        "mk8s",
        "node-group",
        "update",
        node_group_id,
        "--file",
        str(file_path),
        "--format",
        "json",
        "--timeout",
        timeout,
    ]


def _node_group_strategy_matches_args(
    node_group: Mapping[str, Any],
    strategy_args: Sequence[str],
) -> bool:
    actual_args = _node_group_strategy_cli_args(
        _node_group_strategy(node_group),
        default_max_surge_count=1,
        default_max_unavailable_count=0,
        default_drain_timeout="0s",
    )
    return tuple(actual_args) == tuple(str(item) for item in strategy_args)


def _node_group_template_filesystems_match(
    node_group: Mapping[str, Any],
    raw_desired: str,
) -> bool:
    try:
        desired = json.loads(raw_desired)
    except json.JSONDecodeError:
        return False
    return _stable_json(_node_group_template_filesystems(node_group)) == _stable_json(desired)


def _node_group_update_request_satisfied(
    node_group: Mapping[str, Any],
    *,
    update_args: Sequence[str],
    strategy_args: Sequence[str],
    clear_template_gpu_settings: bool,
) -> bool:
    command = tuple(str(item) for item in update_args)
    if "--version" in command and not _version_prefix_matches(
        _node_group_version(node_group),
        command[command.index("--version") + 1],
    ):
        return False
    if "--template-os" in command and _node_group_template_os(node_group) != command[
        command.index("--template-os") + 1
    ]:
        return False
    if "--template-filesystems" in command and not _node_group_template_filesystems_match(
        node_group,
        command[command.index("--template-filesystems") + 1],
    ):
        return False
    if "--template-gpu-settings-drivers-preset" in command:
        expected_preset = command[command.index("--template-gpu-settings-drivers-preset") + 1]
        if _node_group_template_gpu_drivers_preset(node_group) != expected_preset:
            return False
    if clear_template_gpu_settings and _node_group_template_gpu_drivers_preset(node_group):
        return False
    return _node_group_strategy_matches_args(node_group, strategy_args)


def _node_group_update_timeout_message(
    *,
    node_group_id: str,
    action: str,
    readiness_summary: str,
) -> str:
    return (
        f"Nebius node-group update for {node_group_id} timed out after the {action} "
        f"was accepted, but the rollout is still in progress: {readiness_summary}. "
        "Rerun the same `nebius-cxcli ext-soperator migrate ... --execute --approve` "
        "command; cxcli will re-read the live node group and resume without starting "
        "a duplicate update."
    )


def _reconcile_node_group_update_timeout(
    command_runner: SoperatorMigrationCommandRunner,
    *,
    node_group_id: str,
    update_args: Sequence[str],
    strategy_args: Sequence[str],
    clear_template_gpu_settings: bool,
    action: str,
    timeout: subprocess.TimeoutExpired,
) -> Mapping[str, Any]:
    live_node_group = _node_group_payload_by_id(
        command_runner=command_runner,
        node_group_id=node_group_id,
    )
    if not _node_group_update_request_satisfied(
        live_node_group,
        update_args=update_args,
        strategy_args=strategy_args,
        clear_template_gpu_settings=clear_template_gpu_settings,
    ):
        raise RuntimeError(
            f"Nebius node-group update for {node_group_id} timed out before the live "
            f"node group reported the requested {action}. Rerun the same "
            "`nebius-cxcli ext-soperator migrate ... --execute --approve` command; "
            "cxcli will retry from live state."
        ) from timeout
    ready, readiness_summary = _node_group_readiness_summary(live_node_group)
    if not ready:
        raise SoperatorMigrationPhasePending(
            _node_group_update_timeout_message(
                node_group_id=node_group_id,
                action=action,
                readiness_summary=readiness_summary,
            )
        ) from timeout
    return live_node_group


def _json_from_node_group_update_file(
    command_runner: SoperatorMigrationCommandRunner,
    node_group_id: str,
    *,
    payload: Mapping[str, Any],
    timeout: str,
    timeout_seconds: int,
) -> Mapping[str, Any]:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix="nebius-cxcli-node-group-",
            suffix=".json",
            delete=False,
        ) as handle:
            json.dump(to_plain_data(payload), handle, sort_keys=True)
            temp_path = Path(handle.name)
        return _json_from_command(
            command_runner,
            _node_group_update_file_command(
                node_group_id,
                file_path=temp_path,
                timeout=timeout,
            ),
            timeout_seconds=timeout_seconds,
        )
    finally:
        if temp_path is not None:
            with suppress(FileNotFoundError):
                temp_path.unlink()


def _node_group_rollout_timeout_seconds(node_group: Mapping[str, Any]) -> int:
    status = _mapping(node_group.get("status"))
    node_count = max(
        1,
        _node_group_fixed_count(node_group),
        _positive_int(
            status.get("target_node_count", status.get("targetNodeCount")),
            fallback=0,
        ),
        _positive_int(status.get("node_count", status.get("nodeCount")), fallback=0),
    )
    return max(3600, node_count * 600)


def _node_group_rollout_timeout_text(timeout_seconds: int) -> str:
    return f"{math.ceil(timeout_seconds / 60)}m"


def _update_node_group_with_temporary_strategy(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    node_group_id: str,
    update_args: Sequence[str],
    strategy_args: Sequence[str],
    original_node_group: Mapping[str, Any] | None = None,
    clear_template_gpu_settings: bool = False,
    timeout: str = "45m",
    timeout_seconds: int = 2700,
) -> Mapping[str, Any]:
    original_node_group = original_node_group or _node_group_payload_by_id(
        command_runner=command_runner,
        node_group_id=node_group_id,
    )
    original_strategy_args = _node_group_strategy_cli_args(
        _node_group_strategy(original_node_group),
        default_max_surge_count=1,
        default_max_unavailable_count=0,
        default_drain_timeout="0s",
    )
    restore_strategy = tuple(original_strategy_args) != tuple(str(item) for item in strategy_args)
    updated = False
    primary_exc: BaseException | None = None
    try:
        if clear_template_gpu_settings:
            try:
                result = _json_from_node_group_update_file(
                    command_runner,
                    node_group_id,
                    payload=_node_group_full_update_payload(
                        original_node_group=original_node_group,
                        update_args=update_args,
                        strategy_args=strategy_args,
                        clear_template_gpu_settings=True,
                    ),
                    timeout=timeout,
                    timeout_seconds=timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                result = _reconcile_node_group_update_timeout(
                    command_runner,
                    node_group_id=node_group_id,
                    update_args=update_args,
                    strategy_args=strategy_args,
                    clear_template_gpu_settings=True,
                    action="node-template update",
                    timeout=exc,
                )
        else:
            try:
                result = _json_from_command(
                    command_runner,
                    _node_group_update_command(
                        node_group_id,
                        update_args=update_args,
                        strategy_args=strategy_args,
                        timeout=timeout,
                    ),
                    timeout_seconds=timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                result = _reconcile_node_group_update_timeout(
                    command_runner,
                    node_group_id=node_group_id,
                    update_args=update_args,
                    strategy_args=strategy_args,
                    clear_template_gpu_settings=False,
                    action="node-template update",
                    timeout=exc,
                )
        updated = True
        return result
    except Exception as exc:
        primary_exc = exc
        raise
    finally:
        if restore_strategy and not isinstance(primary_exc, SoperatorMigrationPhasePending):
            try:
                try:
                    _json_from_command(
                        command_runner,
                        _node_group_update_command(
                            node_group_id,
                            update_args=(),
                            strategy_args=original_strategy_args,
                            timeout=timeout,
                        ),
                        timeout_seconds=timeout_seconds,
                    )
                except subprocess.TimeoutExpired as exc:
                    _reconcile_node_group_update_timeout(
                        command_runner,
                        node_group_id=node_group_id,
                        update_args=(),
                        strategy_args=original_strategy_args,
                        clear_template_gpu_settings=False,
                        action="strategy restore",
                        timeout=exc,
                    )
            except Exception as exc:
                if updated:
                    raise RuntimeError(
                        f"Could not restore original update strategy for node group {node_group_id}."
                    ) from exc
                if primary_exc is None:
                    raise


def _update_node_group_with_zero_surge_strategy(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    node_group_id: str,
    update_args: Sequence[str],
    original_node_group: Mapping[str, Any] | None = None,
    clear_template_gpu_settings: bool = False,
    timeout_seconds: int = 2700,
) -> Mapping[str, Any]:
    timeout = _node_group_rollout_timeout_text(timeout_seconds)
    return _update_node_group_with_temporary_strategy(
        command_runner=command_runner,
        node_group_id=node_group_id,
        update_args=update_args,
        strategy_args=_soperator_zero_surge_strategy_cli_args(),
        original_node_group=original_node_group,
        clear_template_gpu_settings=clear_template_gpu_settings,
        timeout=timeout,
        timeout_seconds=timeout_seconds,
    )


def _node_group_template_filesystems(node_group: Mapping[str, Any]) -> list[dict[str, Any]]:
    template = _mapping(_mapping(node_group.get("spec")).get("template"))
    filesystems = template.get("filesystems")
    if not isinstance(filesystems, Sequence) or isinstance(filesystems, (str, bytes, bytearray)):
        return []
    items: list[dict[str, Any]] = []
    for item in filesystems:
        if isinstance(item, Mapping):
            items.append(dict(to_plain_data(item)))
    return items


def _filesystem_attachment(
    spec: SoperatorAlignedFilesystemSpec, filesystem_id: str
) -> dict[str, Any]:
    return {
        "attach_mode": "READ_WRITE",
        "existing_filesystem": {"id": filesystem_id},
        "mount_tag": spec.mount_tag,
    }


def _merge_filesystem_attachments(
    existing: Sequence[Mapping[str, Any]],
    desired: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [dict(to_plain_data(item)) for item in existing]
    seen_mounts = {
        str(item.get("mount_tag", "") or "").strip()
        for item in merged
        if str(item.get("mount_tag", "") or "").strip()
    }
    seen_ids = {
        str(_mapping(item.get("existing_filesystem")).get("id", "") or "").strip()
        for item in merged
        if str(_mapping(item.get("existing_filesystem")).get("id", "") or "").strip()
    }
    for item in desired:
        mount_tag = str(item.get("mount_tag", "") or "").strip()
        filesystem_id = str(_mapping(item.get("existing_filesystem")).get("id", "") or "").strip()
        if (mount_tag and mount_tag in seen_mounts) or (
            filesystem_id and filesystem_id in seen_ids
        ):
            continue
        merged.append(dict(to_plain_data(item)))
        if mount_tag:
            seen_mounts.add(mount_tag)
        if filesystem_id:
            seen_ids.add(filesystem_id)
    return merged


def _attach_filesystems_to_source_node_groups(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    source_report: Mapping[str, Any],
    attachment_keys_by_group: Mapping[str, Sequence[str]],
    filesystem_ids_by_key: Mapping[str, str],
    specs_by_key: Mapping[str, SoperatorAlignedFilesystemSpec],
) -> tuple[bool, list[dict[str, Any]]]:
    inventory = _source_node_group_inventory(source_report)
    attachments: list[dict[str, Any]] = []
    mutation_performed = False
    for raw_group_name, raw_group in sorted(inventory.items()):
        if not isinstance(raw_group, Mapping):
            continue
        group_name = normalize_component_token(raw_group_name)
        if not group_name:
            continue
        desired_keys = tuple(
            key
            for key in attachment_keys_by_group.get(group_name, ())
            if key in filesystem_ids_by_key and key in specs_by_key
        )
        if not desired_keys:
            continue
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            raise SoperatorMigrationPhasePending(
                "create-aligned-sfs requires Nebius node group ids in the onboarding "
                f"inventory before it can attach SFS to source group '{group_name}'. "
                "Rerun `nebius-cxcli ext-soperator onboard` against a Nebius MK8s target."
            )
        desired = [
            _filesystem_attachment(specs_by_key[key], filesystem_ids_by_key[key])
            for key in desired_keys
        ]
        node_group = _node_group_payload_by_id(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
        existing = _node_group_template_filesystems(node_group)
        merged = _merge_filesystem_attachments(existing, desired)
        updated = len(merged) != len(existing)
        if updated:
            _update_node_group_with_zero_surge_strategy(
                command_runner=command_runner,
                node_group_id=node_group_id,
                update_args=("--template-filesystems", json.dumps(merged, sort_keys=True)),
                original_node_group=node_group,
                timeout_seconds=_node_group_rollout_timeout_seconds(node_group),
            )
            mutation_performed = True
        attachments.append(
            {
                "source_group": group_name,
                "node_group_id": node_group_id,
                "filesystem_keys": list(desired_keys),
                "strategy": "zero-surge",
                "strategy_restored": updated,
                "updated": updated,
            }
        )
    return mutation_performed, attachments


def _snapshot_storage(source_report: Mapping[str, Any]) -> Mapping[str, Any]:
    snapshot = _mapping(source_report.get("snapshot"))
    return _mapping(snapshot.get("storage"))


def _snapshot_pvc_names(snapshot: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    pvcs = snapshot.get("pvcs")
    if not isinstance(pvcs, Sequence) or isinstance(pvcs, (str, bytes, bytearray)):
        return names
    for pvc in pvcs:
        if not isinstance(pvc, Mapping):
            continue
        metadata = _mapping(pvc.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        if name:
            names.add(name)
    return names


def _source_pvc_name_for_storage_key(source_report: Mapping[str, Any], key: str) -> str:
    storage = _snapshot_storage(source_report)
    item = _mapping(storage.get(key))
    source = str(item.get("source", "") or "").strip()
    if source.startswith("pvc/"):
        return source.removeprefix("pvc/").strip()
    pvc = str(item.get("pvc", "") or item.get("claimName", "") or "").strip()
    return pvc


def _target_pvc_name_for_storage_key(payload: Mapping[str, Any], target_ref: str, key: str) -> str:
    values = _target_soperator_values(payload, target_ref)
    if key == "jail":
        nodesets = values.get("nodesets")
        if isinstance(nodesets, Sequence) and not isinstance(nodesets, (str, bytes, bytearray)):
            for nodeset in nodesets:
                if not isinstance(nodeset, Mapping):
                    continue
                volumes = _mapping(_mapping(nodeset.get("slurmd")).get("volumes"))
                claim_name = str(
                    _mapping(_mapping(volumes.get("jail")).get("persistentVolumeClaim")).get(
                        "claimName", ""
                    )
                    or ""
                ).strip()
                if claim_name:
                    return claim_name
    defaults = {
        "jail": "jail-pvc",
        "controller-spool": "controller-spool-pvc",
        "accounting": "accounting-pvc",
    }
    return defaults[key]


def _copy_job_manifest(
    *,
    key: str,
    source_pvc: str,
    target_pvc: str,
) -> dict[str, Any]:
    normalized = normalize_component_token(key) or key.replace("_", "-")
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": f"cxcli-soperator-sync-{normalized}",
            "namespace": _SOPERATOR_NAMESPACE,
            "labels": {
                "app.kubernetes.io/managed-by": "nebius-cxcli",
                "nebius-cxcli.io/soperator-migration": "true",
                "nebius-cxcli.io/storage-key": key,
            },
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "copy",
                            "image": "ubuntu:24.04",
                            "command": [
                                "/bin/sh",
                                "-ceu",
                                "cd /old && tar --xattrs --acls --numeric-owner -cpf - . "
                                "| tar --xattrs --acls --numeric-owner -xpf - -C /new",
                            ],
                            "volumeMounts": [
                                {"name": "old", "mountPath": "/old", "readOnly": True},
                                {"name": "new", "mountPath": "/new"},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "old", "persistentVolumeClaim": {"claimName": source_pvc}},
                        {"name": "new", "persistentVolumeClaim": {"claimName": target_pvc}},
                    ],
                }
            },
        },
    }


def _kubectl_apply_objects(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    objects: Sequence[Mapping[str, Any]],
    timeout_seconds: int = 300,
) -> None:
    payload = {
        "apiVersion": "v1",
        "kind": "List",
        "items": [to_plain_data(item) for item in objects],
    }
    command_runner(
        ["kubectl", "--context", kube_context, "apply", "-f", "-"],
        input_text=json.dumps(payload, sort_keys=True),
        timeout_seconds=timeout_seconds,
    )


def _kubectl_wait(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    namespace: str,
    resource: str,
    condition: str,
    timeout: str,
    timeout_seconds: int,
) -> None:
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            namespace,
            "wait",
            f"--for={condition}",
            resource,
            f"--timeout={timeout}",
        ],
        timeout_seconds=timeout_seconds,
    )


def _kubectl_rollout_status(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    namespace: str,
    resource: str,
    timeout: str = "10m",
) -> None:
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            namespace,
            "rollout",
            "status",
            resource,
            f"--timeout={timeout}",
        ],
        timeout_seconds=900,
    )


def _has_soperator_custom_resources(snapshot: Mapping[str, Any]) -> bool:
    resources = snapshot.get("soperator_resources")
    return (
        isinstance(resources, Sequence)
        and not isinstance(resources, (str, bytes, bytearray))
        and any(isinstance(item, Mapping) for item in resources)
    )


def _has_live_slurmcluster_resource(snapshot: Mapping[str, Any]) -> bool:
    for resource in _sequence_of_mappings(snapshot.get("soperator_resources")):
        if str(resource.get("kind", "") or "").strip() == "SlurmCluster":
            return True
    return False


def _live_source_slurmcluster_present(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    source_report: Mapping[str, Any],
    target_ref: str,
) -> bool:
    source_names = set(_source_slurmcluster_names(source_report, target_ref=target_ref))
    if not source_names:
        return False
    try:
        result = _json_from_command(
            command_runner,
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "get",
                "slurmclusters",
                "-o",
                "json",
                "--request-timeout=20s",
            ],
            timeout_seconds=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    for item in _sequence_of_mappings(result.get("items")):
        name = str(_mapping(item.get("metadata")).get("name", "") or "").strip()
        if name in source_names:
            return True
    return False


def _nodes_for_worker_groups(
    *,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
) -> tuple[str, ...]:
    inventory = _source_node_group_inventory(source_report)
    alias_map, ambiguous_aliases = _source_group_alias_map(inventory)
    requested = {
        alias_map.get(normalize_component_token(group), normalize_component_token(group))
        for group in worker_node_groups
        if normalize_component_token(group) not in ambiguous_aliases
    }
    nodes: list[str] = []
    for group_name, raw_group in inventory.items():
        normalized = normalize_component_token(group_name)
        if normalized not in requested or not isinstance(raw_group, Mapping):
            continue
        raw_nodes = raw_group.get("nodes")
        if isinstance(raw_nodes, Sequence) and not isinstance(raw_nodes, (str, bytes, bytearray)):
            nodes.extend(str(node).strip() for node in raw_nodes if str(node).strip())
    return tuple(dict.fromkeys(nodes))


def _source_worker_node_count(
    *,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
) -> int:
    inventory = _source_node_group_inventory(source_report)
    alias_map, ambiguous_aliases = _source_group_alias_map(inventory)
    requested = {
        alias_map.get(normalize_component_token(group), normalize_component_token(group))
        for group in worker_node_groups
        if normalize_component_token(group) not in ambiguous_aliases
    }
    count = 0
    for group_name, raw_group in inventory.items():
        normalized = normalize_component_token(group_name)
        if normalized not in requested or not isinstance(raw_group, Mapping):
            continue
        count += _positive_int(raw_group.get("node_count"), fallback=0)
    return count


def _nodes_for_source_groups(
    *,
    source_report: Mapping[str, Any],
    source_groups: Sequence[str],
) -> tuple[str, ...]:
    inventory = _source_node_group_inventory(source_report)
    requested = {normalize_component_token(group) for group in source_groups}
    nodes: list[str] = []
    for group_name, raw_group in inventory.items():
        normalized = normalize_component_token(group_name)
        if normalized not in requested or not isinstance(raw_group, Mapping):
            continue
        raw_nodes = raw_group.get("nodes")
        if isinstance(raw_nodes, Sequence) and not isinstance(raw_nodes, (str, bytes, bytearray)):
            nodes.extend(str(node).strip() for node in raw_nodes if str(node).strip())
    return tuple(dict.fromkeys(nodes))


def _node_group_id(payload: Mapping[str, Any]) -> str:
    metadata = _mapping(payload.get("metadata"))
    return str(metadata.get("id", "") or payload.get("id", "") or "").strip()


def _node_group_name(payload: Mapping[str, Any]) -> str:
    metadata = _mapping(payload.get("metadata"))
    return str(metadata.get("name", "") or payload.get("name", "") or "").strip()


def _node_group_parent_id(payload: Mapping[str, Any]) -> str:
    metadata = _mapping(payload.get("metadata"))
    return str(metadata.get("parent_id", "") or metadata.get("parentId", "") or "").strip()


def _node_group_fixed_count(payload: Mapping[str, Any]) -> int:
    spec = _mapping(payload.get("spec"))
    return _positive_int(spec.get("fixed_node_count", spec.get("fixedNodeCount")), fallback=1)


def _source_compute_group_names(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
) -> Mapping[str, str]:
    inventory = _source_node_group_inventory(source_report)
    alias_map, ambiguous_aliases = _source_group_alias_map(inventory)
    worker_set = {
        alias_map.get(group, group)
        for group in (normalize_component_token(item) for item in worker_node_groups)
        if group and group not in ambiguous_aliases
    }
    worker_group = next(
        (
            group
            for group in worker_set
            if group in inventory and _source_group_is_gpu(_mapping(inventory.get(group)))
        ),
        "",
    )
    if not worker_group:
        worker_group = next((group for group in worker_set if group in inventory), "")
    if not worker_group:
        raise SoperatorMigrationPhasePending(
            "rolling-compute-migration requires at least one approved source worker node group."
        )

    role_mapping = _target_role_mapping(payload, target_ref)
    cpu_candidates: list[str] = []
    for role in _SOPERATOR_SERVICE_ROLES:
        for group in role_mapping.get(role, ()):
            resolved_group = alias_map.get(group, group)
            if resolved_group and resolved_group in inventory and resolved_group not in worker_set:
                cpu_candidates.append(resolved_group)
    for preferred_nodeset in _SOPERATOR_SERVICE_ROLES:
        for group_name, raw_group in inventory.items():
            normalized = normalize_component_token(group_name)
            if not normalized or normalized in worker_set or not isinstance(raw_group, Mapping):
                continue
            if normalize_component_token(_source_group_nodeset(raw_group)) == preferred_nodeset:
                cpu_candidates.append(normalized)
    if not cpu_candidates:
        for group_name, raw_group in inventory.items():
            normalized = normalize_component_token(group_name)
            if not normalized or normalized in worker_set or not isinstance(raw_group, Mapping):
                continue
            if not _source_group_is_gpu(raw_group):
                cpu_candidates.append(normalized)
    cpu_group = next((group for group in dict.fromkeys(cpu_candidates) if group in inventory), "")
    if not cpu_group:
        raise SoperatorMigrationPhasePending(
            "rolling-compute-migration could not identify a non-GPU source node group "
            "to clone for Soperator system/controller/login/accounting roles. Rerun "
            "onboarding with explicit compute role mapping."
        )
    return {"cpu": cpu_group, "gpu": worker_group}


def _external_node_template_upgrade_groups(
    *,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    service_groups, worker_groups = _split_external_node_template_upgrade_groups(
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    return tuple([*service_groups, *worker_groups])


def _split_external_node_template_upgrade_groups(
    *,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
) -> tuple[
    tuple[tuple[str, Mapping[str, Any]], ...],
    tuple[tuple[str, Mapping[str, Any]], ...],
]:
    inventory = _source_node_group_inventory(source_report)
    worker_set = {
        normalize_component_token(group)
        for group in (worker_node_groups or _infer_worker_node_groups(source_report))
        if normalize_component_token(group)
    }
    service_groups: list[tuple[str, Mapping[str, Any]]] = []
    worker_groups: list[tuple[str, Mapping[str, Any]]] = []
    for raw_group_name, raw_group in sorted(inventory.items()):
        group_name = normalize_component_token(raw_group_name)
        if not group_name or not isinstance(raw_group, Mapping):
            continue
        if group_name in worker_set or _source_group_is_worker(group_name, raw_group):
            worker_groups.append((group_name, raw_group))
        else:
            service_groups.append((group_name, raw_group))
    return tuple(service_groups), tuple(worker_groups)


def _external_migration_cluster_id(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    source_report: Mapping[str, Any],
    command_runner: SoperatorMigrationCommandRunner,
) -> str:
    cluster_id = _target_cluster_id(payload, target_ref)
    if cluster_id:
        return cluster_id
    for _group_name, raw_group in _external_node_template_upgrade_groups(
        source_report=source_report,
        worker_node_groups=(),
    ):
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            continue
        node_group = _node_group_payload_by_id(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
        cluster_id = _node_group_parent_id(node_group)
        if cluster_id:
            return cluster_id
    raise SoperatorMigrationPhasePending(
        "external-node-template-upgrade requires a Nebius MK8s cluster id in the "
        "onboarded target or source node-group metadata. Rerun `nebius-cxcli "
        "ext-soperator onboard` against a Nebius MK8s target."
    )


def _json_value_from_command(
    command_runner: SoperatorMigrationCommandRunner,
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int = 300,
    check: bool = True,
) -> Any:
    result = command_runner(
        args,
        input_text=input_text,
        timeout_seconds=timeout_seconds,
        check=check,
    )
    if result.returncode != 0 and not check:
        return {}
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_command_text(args)} returned invalid JSON: {exc}") from exc


def _list_node_groups(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    cluster_id: str,
) -> tuple[Mapping[str, Any], ...]:
    parsed = _json_value_from_command(
        command_runner,
        [
            "nebius",
            "mk8s",
            "node-group",
            "list",
            "--parent-id",
            cluster_id,
            "--format",
            "json",
            "--all",
        ],
        timeout_seconds=180,
    )
    if isinstance(parsed, Mapping):
        items = parsed.get("items", parsed.get("node_groups", parsed.get("nodeGroups", [])))
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
            return tuple(item for item in items if isinstance(item, Mapping))
        if _node_group_id(parsed):
            return (parsed,)
    if isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes, bytearray)):
        return tuple(item for item in parsed if isinstance(item, Mapping))
    return ()


def _kubectl_live_nodes(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> tuple[Mapping[str, Any], ...]:
    parsed = _json_value_from_command(
        command_runner,
        [
            "kubectl",
            "--context",
            kube_context,
            "get",
            "nodes",
            "-o",
            "json",
            "--request-timeout=20s",
        ],
        timeout_seconds=30,
    )
    items = parsed.get("items") if isinstance(parsed, Mapping) else []
    if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
        return tuple(item for item in items if isinstance(item, Mapping))
    return ()


def _node_metadata_labels(item: Mapping[str, Any]) -> Mapping[str, str]:
    metadata = _mapping(item.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    return {str(key): str(value) for key, value in labels.items()}


def _node_group_template_labels(item: Mapping[str, Any]) -> Mapping[str, str]:
    template = _mapping(_mapping(item.get("spec")).get("template"))
    metadata = _mapping(template.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    return {str(key): str(value) for key, value in labels.items()}


def _node_group_metadata_labels(item: Mapping[str, Any]) -> Mapping[str, str]:
    metadata = _mapping(item.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    return {str(key): str(value) for key, value in labels.items()}


def _node_group_role_label(item: Mapping[str, Any]) -> str:
    labels: dict[str, str] = {}
    labels.update(_node_group_metadata_labels(item))
    labels.update(_node_group_template_labels(item))
    for key in _SOPERATOR_NODESET_LABEL_KEYS:
        value = normalize_component_token(labels.get(key))
        if value:
            return value
    return ""


def _node_allocatable(item: Mapping[str, Any]) -> Mapping[str, str]:
    status = _mapping(item.get("status"))
    allocatable = _mapping(status.get("allocatable"))
    return {str(key): str(value) for key, value in allocatable.items()}


def _node_name(item: Mapping[str, Any]) -> str:
    return str(_mapping(item.get("metadata")).get("name", "") or "").strip()


def _nodes_by_node_group_id(
    nodes: Sequence[Mapping[str, Any]],
) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in nodes:
        labels = _node_metadata_labels(item)
        node_group_id = next(
            (
                str(labels.get(key, "") or "").strip()
                for key in _SOURCE_NODE_GROUP_ID_LABEL_KEYS
                if str(labels.get(key, "") or "").strip()
            ),
            "",
        )
        if node_group_id:
            grouped.setdefault(node_group_id, []).append(item)
    return {key: tuple(value) for key, value in grouped.items()}


def _node_group_platform(item: Mapping[str, Any]) -> str:
    resources = _mapping(_mapping(_mapping(item.get("spec")).get("template")).get("resources"))
    return str(resources.get("platform", "") or resources.get("platform_id", "") or "").strip()


def _node_group_is_gpu_from_payload(
    node_group: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
) -> bool:
    labels = dict(_node_group_template_labels(node_group))
    for node in nodes:
        labels.update(_node_metadata_labels(node))
    if _bool_value(labels.get("nebius.com/gpu"), fallback=False):
        return True
    platform = _node_group_platform(node_group)
    if normalize_component_token(platform).startswith("gpu"):
        return True
    return any(
        str(key).startswith("nvidia.com/gpu") and str(value) not in {"", "0"}
        for node in nodes
        for key, value in _node_allocatable(node).items()
    )


def _live_nebius_node_group_inventory(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    cluster_id: str,
) -> Mapping[str, Mapping[str, Any]]:
    node_groups = _list_node_groups(command_runner=command_runner, cluster_id=cluster_id)
    if not node_groups:
        return {}
    live_nodes = _kubectl_live_nodes(command_runner=command_runner, kube_context=kube_context)
    live_nodes_by_group_id = _nodes_by_node_group_id(live_nodes)
    inventory: dict[str, Mapping[str, Any]] = {}
    used_keys: set[str] = set()
    for node_group in node_groups:
        node_group_id = _node_group_id(node_group)
        if not node_group_id:
            continue
        node_group_name = _node_group_name(node_group) or node_group_id
        key = normalize_component_token(node_group_name) or normalize_component_token(node_group_id)
        if not key:
            continue
        if key in used_keys:
            key = normalize_component_token(node_group_id) or key
        used_keys.add(key)
        nodes = live_nodes_by_group_id.get(node_group_id, ())
        labels: dict[str, str] = {}
        labels.update(_node_group_template_labels(node_group))
        labels.update(
            {
                str(k): str(v)
                for k, v in _mapping(_mapping(node_group.get("metadata")).get("labels")).items()
            }
        )
        for node in nodes:
            labels.update(_node_metadata_labels(node))
        labels.setdefault("nebius.com/node-group", node_group_name)
        labels.setdefault("nebius.com/node-group-id", node_group_id)
        platform = _node_group_platform(node_group)
        if platform:
            labels.setdefault("node.kubernetes.io/instance-type", platform)
        allocatable: dict[str, str] = {}
        for node in nodes:
            allocatable.update(_node_allocatable(node))
        inventory[key] = {
            "allocatable": allocatable,
            "gpu": _node_group_is_gpu_from_payload(node_group, nodes),
            "node_count": len(nodes) or _node_group_fixed_count(node_group),
            "labels": labels,
            "node_group_id": node_group_id,
            "node_group_name": node_group_name,
            "nodes": tuple(node_name for node in nodes if (node_name := _node_name(node))),
            "selector": {
                "key": "nebius.com/node-group-id",
                "operator": "In",
                "values": [node_group_id],
            },
            "taints": _mapping(_mapping(node_group.get("spec")).get("template")).get("taints", []),
        }
    return inventory


def _source_report_with_execution_inventory(
    *,
    source_report: Mapping[str, Any],
    payload: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> Mapping[str, Any]:
    cluster_id = _target_cluster_id(payload, target_ref)
    if not cluster_id:
        return source_report
    inventory = _live_nebius_node_group_inventory(
        command_runner=command_runner,
        kube_context=kube_context,
        cluster_id=cluster_id,
    )
    if not inventory:
        return source_report
    inventory = _inventory_with_stable_source_identity_labels(
        live_inventory=inventory,
        source_report=source_report,
    )
    execution_report = copy.deepcopy(to_plain_data(source_report))
    snapshot = execution_report.setdefault("snapshot", {})
    if isinstance(snapshot, dict):
        snapshot["node_groups"] = to_plain_data(inventory)
    return execution_report if isinstance(execution_report, Mapping) else source_report


def _source_groups_by_stable_identity(
    source_report: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    groups: dict[str, Mapping[str, Any]] = {}
    for raw_name, raw_group in _source_node_group_inventory(source_report).items():
        if not isinstance(raw_group, Mapping):
            continue
        for value in (
            raw_name,
            _source_group_node_group_id(raw_group),
            _source_group_node_group_name(raw_group),
        ):
            key = normalize_component_token(value)
            if key:
                groups.setdefault(key, raw_group)
    return groups


def _inventory_with_stable_source_identity_labels(
    *,
    live_inventory: Mapping[str, Mapping[str, Any]],
    source_report: Mapping[str, Any],
) -> Mapping[str, Mapping[str, Any]]:
    source_groups = _source_groups_by_stable_identity(source_report)
    if not source_groups:
        return live_inventory
    merged_inventory: dict[str, Mapping[str, Any]] = {}
    for raw_name, raw_group in live_inventory.items():
        if not isinstance(raw_group, Mapping):
            merged_inventory[raw_name] = raw_group
            continue
        source_group = source_groups.get(normalize_component_token(raw_name))
        if source_group is None:
            source_group = source_groups.get(
                normalize_component_token(_source_group_node_group_id(raw_group))
            )
        if source_group is None:
            source_group = source_groups.get(
                normalize_component_token(_source_group_node_group_name(raw_group))
            )
        if source_group is None:
            merged_inventory[raw_name] = raw_group
            continue
        source_labels = _source_group_labels(source_group)
        labels = dict(_mapping(raw_group.get("labels")))
        for label_key in _EXECUTION_INVENTORY_STABLE_LABEL_KEYS:
            value = str(source_labels.get(label_key, "") or "").strip()
            if value:
                labels[label_key] = value
        merged_group = dict(to_plain_data(raw_group))
        merged_group["labels"] = labels
        merged_inventory[raw_name] = merged_group
    return merged_inventory


def _find_node_group_by_name(
    node_groups: Sequence[Mapping[str, Any]],
    name: str,
) -> Mapping[str, Any]:
    for node_group in node_groups:
        if _node_group_name(node_group) == name:
            return node_group
    return {}


def _find_service_role_node_group(
    node_groups: Sequence[Mapping[str, Any]],
    *,
    role: str,
    target_name: str,
) -> tuple[Mapping[str, Any], str, bool]:
    exact = _find_node_group_by_name(node_groups, target_name)
    if exact:
        return exact, target_name, False
    normalized_role = normalize_component_token(role)
    for node_group in node_groups:
        node_group_name = _node_group_name(node_group)
        if normalize_component_token(node_group_name) == normalized_role:
            return node_group, node_group_name or role, True
    for node_group in node_groups:
        node_group_name = _node_group_name(node_group)
        if _node_group_role_label(node_group) == normalized_role:
            return node_group, node_group_name or role, True
    return {}, target_name, False


def _lower_nebius_enums(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _lower_nebius_enums(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_lower_nebius_enums(item) for item in value]
    return value


def _source_node_group_spec_for_role(
    *,
    role: str,
    source_groups_by_kind: Mapping[str, str],
    inventory: Mapping[str, Any],
) -> Mapping[str, Any]:
    source_kind = _SOPERATOR_ROLE_SOURCE_KIND[role]
    source_group = source_groups_by_kind[source_kind]
    raw_group = inventory.get(source_group)
    if not isinstance(raw_group, Mapping):
        raise SoperatorMigrationPhasePending(
            f"rolling-compute-migration could not find source node group '{source_group}'."
        )
    return raw_group


def _role_filesystem_attachments(
    *,
    role: str,
    checkpoint: Mapping[str, Any],
    specs_by_key: Mapping[str, SoperatorAlignedFilesystemSpec],
) -> list[dict[str, Any]]:
    phase = _mapping(_mapping(checkpoint.get("phase_state")).get("create-aligned-sfs"))
    raw_filesystems = _mapping(phase.get("filesystems"))
    attachments: list[dict[str, Any]] = []
    for key in _SOPERATOR_ROLE_STORAGE_KEYS[role]:
        filesystem = _mapping(raw_filesystems.get(key))
        filesystem_id = str(filesystem.get("id", "") or "").strip()
        spec = specs_by_key.get(key)
        if not filesystem_id or spec is None:
            continue
        attachments.append(_filesystem_attachment(spec, filesystem_id))
    return attachments


def _source_slurmcluster_names(
    source_report: Mapping[str, Any],
    *,
    target_ref: str,
) -> tuple[str, ...]:
    snapshot = _mapping(source_report.get("snapshot"))
    names: list[str] = []
    for resource in _sequence_of_mappings(snapshot.get("soperator_resources")):
        if str(resource.get("kind", "") or "").strip() != "SlurmCluster":
            continue
        metadata = _mapping(resource.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        if name and name != target_ref:
            names.append(name)
    return tuple(dict.fromkeys(names))


def _target_node_group_name(target_ref: str, role: str) -> str:
    return normalize_component_token(f"{target_ref}-{role}") or f"{target_ref}-{role}"


def _role_node_group_taints(role: str, source_template: Mapping[str, Any]) -> list[dict[str, Any]]:
    if role in {"controller", "login", "accounting"}:
        return [
            {
                "key": "slurm.nebius.ai/nodeset-name",
                "value": role,
                "effect": "NO_SCHEDULE",
            }
        ]
    if role == "worker":
        existing = source_template.get("taints")
        taints: list[dict[str, Any]] = []
        if isinstance(existing, Sequence) and not isinstance(existing, (str, bytes, bytearray)):
            for item in existing:
                if isinstance(item, Mapping):
                    taints.append(dict(to_plain_data(_lower_nebius_enums(item))))
        if not any(str(item.get("key", "")) == "nvidia.com/gpu" for item in taints):
            taints.append(
                {
                    "key": "nvidia.com/gpu",
                    "value": "true",
                    "effect": "NO_SCHEDULE",
                }
            )
        return taints
    return []


def _role_node_group_template(
    *,
    role: str,
    source_node_group: Mapping[str, Any],
    filesystems: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    source_template = copy.deepcopy(
        _mapping(_mapping(source_node_group.get("spec")).get("template"))
    )
    if not source_template:
        raise SoperatorMigrationPhasePending(
            f"rolling-compute-migration could not clone a Nebius node template for role '{role}'."
        )
    template = dict(to_plain_data(_lower_nebius_enums(source_template)))
    metadata = dict(_mapping(template.get("metadata")))
    labels = dict(_mapping(metadata.get("labels")))
    source_node_group_label = str(labels.get("nebius.com/node-group", "") or "").strip()
    labels["nebius.com/node-group"] = source_node_group_label or _SOPERATOR_ROLE_SOURCE_KIND[role]
    labels["slurm.nebius.ai/nodeset-name"] = role
    if role != "worker":
        labels["nebius.com/gpu"] = "false"
    metadata["labels"] = labels
    template["metadata"] = metadata
    template["filesystems"] = [dict(to_plain_data(item)) for item in filesystems]
    template["taints"] = _role_node_group_taints(role, template)
    return template


def _path_text(value: Mapping[str, Any], path: Sequence[str]) -> str:
    cursor: Any = value
    for key in path:
        if not isinstance(cursor, Mapping):
            return ""
        cursor = cursor.get(key)
    if isinstance(cursor, Mapping):
        return str(cursor.get("name", "") or "").strip()
    return str(cursor or "").strip()


def _normalized_text(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _filesystem_identity_set(filesystems: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    for filesystem in filesystems:
        mount_tag = str(filesystem.get("mount_tag", "") or "").strip()
        filesystem_id = str(
            _mapping(filesystem.get("existing_filesystem")).get("id", "") or ""
        ).strip()
        if mount_tag or filesystem_id:
            identities.add((mount_tag, filesystem_id))
    return identities


def _validate_reused_target_node_group(
    *,
    role: str,
    target_name: str,
    node_group: Mapping[str, Any],
    expected_count: int,
    expected_template: Mapping[str, Any],
    allow_larger_count: bool = False,
    validate_template_shape: bool = True,
) -> None:
    mismatches: list[str] = []
    actual_count = _node_group_fixed_count(node_group)
    if allow_larger_count and actual_count < expected_count:
        mismatches.append(f"fixed_node_count={actual_count} expected at least {expected_count}")
    elif not allow_larger_count and actual_count != expected_count:
        mismatches.append(f"fixed_node_count={actual_count} expected {expected_count}")

    spec = _mapping(node_group.get("spec"))
    template = _mapping(spec.get("template"))
    labels = dict(_node_group_metadata_labels(node_group))
    labels.update(_node_group_template_labels(node_group))
    role_label = next(
        (
            str(labels.get(key, "") or "").strip()
            for key in _SOPERATOR_NODESET_LABEL_KEYS
            if str(labels.get(key, "") or "").strip()
        ),
        "",
    )
    if normalize_component_token(role_label) != normalize_component_token(role):
        mismatches.append(
            f"missing compatible Soperator role label for {role} "
            f"({', '.join(_SOPERATOR_NODESET_LABEL_KEYS)})"
        )

    if validate_template_shape:
        for path in (
            ("resources", "platform"),
            ("resources", "preset"),
            ("os",),
        ):
            actual = _normalized_text(_path_text(template, path))
            expected = _normalized_text(_path_text(expected_template, path))
            if expected and actual and actual != expected:
                mismatches.append(f"template.{'.'.join(path)}={actual} expected {expected}")

    expected_filesystems = _filesystem_identity_set(
        _sequence_of_mappings(expected_template.get("filesystems"))
    )
    if expected_filesystems:
        actual_filesystems = _filesystem_identity_set(
            _sequence_of_mappings(template.get("filesystems"))
        )
        missing_filesystems = expected_filesystems - actual_filesystems
        if missing_filesystems:
            formatted = ", ".join(
                f"{mount_tag or '?'}:{filesystem_id or '?'}"
                for mount_tag, filesystem_id in sorted(missing_filesystems)
            )
            mismatches.append(f"missing filesystem attachment(s): {formatted}")

    if role in {"controller", "login", "accounting"}:
        taints = _sequence_of_mappings(template.get("taints"))
        if not any(
            str(taint.get("key", "") or "").strip() in _SOPERATOR_NODESET_LABEL_KEYS
            and str(taint.get("value", "") or "").strip() == role
            for taint in taints
        ):
            mismatches.append(f"missing compatible Soperator role NoSchedule taint for {role}")
    if role == "worker":
        taints = _sequence_of_mappings(template.get("taints"))
        if not any(str(taint.get("key", "") or "").strip() == "nvidia.com/gpu" for taint in taints):
            mismatches.append("missing nvidia.com/gpu worker taint")

    if mismatches:
        raise SoperatorMigrationPhasePending(
            f"existing target node group '{target_name}' is incompatible with "
            f"Soperator role '{role}': "
            + "; ".join(mismatches)
            + ". Rename or fix the existing node group before resuming."
        )


def _create_or_reuse_target_node_groups(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    target_ref: str,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "rolling-compute-migration")
    target_groups = phase.setdefault("target_node_groups", {})
    if not isinstance(target_groups, dict):
        raise RuntimeError(
            "Soperator migration checkpoint rolling-compute-migration.target_node_groups must be a mapping."
        )
    old_groups = phase.setdefault("old_node_groups", {})
    if not isinstance(old_groups, dict):
        raise RuntimeError(
            "Soperator migration checkpoint rolling-compute-migration.old_node_groups must be a mapping."
        )
    in_place_worker_groups = tuple(
        dict.fromkeys(
            normalize_component_token(group)
            for group in worker_node_groups
            if normalize_component_token(group)
        )
    )
    phase["in_place_worker_node_groups"] = list(in_place_worker_groups)
    phase["in_place_worker_node_count"] = _source_worker_node_count(
        source_report=source_report,
        worker_node_groups=in_place_worker_groups,
    )

    inventory = _source_node_group_inventory(source_report)
    source_groups_by_kind = _source_compute_group_names(
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    source_payloads: dict[str, Mapping[str, Any]] = {}
    for kind, group_name in source_groups_by_kind.items():
        raw_group = inventory.get(group_name)
        if not isinstance(raw_group, Mapping):
            continue
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            raise SoperatorMigrationPhasePending(
                f"rolling-compute-migration requires Nebius node group id for source group '{group_name}'."
            )
        source_payloads[kind] = _node_group_payload_by_id(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
    worker_group_ids: dict[str, str] = {}
    for group_name in in_place_worker_groups:
        raw_group = inventory.get(group_name)
        if not isinstance(raw_group, Mapping):
            continue
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            raise SoperatorMigrationPhasePending(
                f"rolling-compute-migration requires Nebius node group id for source worker group '{group_name}'."
            )
        worker_group_ids[group_name] = node_group_id
    phase["in_place_worker_node_group_ids"] = worker_group_ids

    cluster_id = _target_cluster_id(payload, target_ref)
    if not cluster_id:
        cluster_id = next(
            (
                _node_group_parent_id(source_payload)
                for source_payload in source_payloads.values()
                if _node_group_parent_id(source_payload)
            ),
            "",
        )
    if not cluster_id:
        raise SoperatorMigrationPhasePending(
            "rolling-compute-migration could not resolve the Nebius MK8s cluster id "
            "from config or source node-group metadata."
        )

    specs_by_key = {
        spec.key: spec for spec in _aligned_filesystem_specs(payload=payload, target_ref=target_ref)
    }
    live_node_groups = _list_node_groups(command_runner=command_runner, cluster_id=cluster_id)
    mutation_performed = False
    lines: list[str] = []
    for role in _SOPERATOR_SERVICE_ROLES:
        target_name = _target_node_group_name(target_ref, role)
        existing, existing_name, role_based_reuse = _find_service_role_node_group(
            live_node_groups,
            role=role,
            target_name=target_name,
        )
        source_kind = _SOPERATOR_ROLE_SOURCE_KIND[role]
        source_node_group = source_payloads[source_kind]
        target_count = 1
        filesystems = _role_filesystem_attachments(
            role=role,
            checkpoint=checkpoint,
            specs_by_key=specs_by_key,
        )
        if not filesystems:
            filesystems = [
                item
                for item in _node_group_template_filesystems(source_node_group)
                if str(item.get("mount_tag", "") or "").strip()
                in _SOPERATOR_ROLE_STORAGE_KEYS[role]
            ]
        expected_template = _role_node_group_template(
            role=role,
            source_node_group=source_node_group,
            filesystems=filesystems,
        )
        if existing:
            node_group_id = _node_group_id(existing)
            if not node_group_id:
                raise RuntimeError(f"existing target node group '{target_name}' has no id.")
            existing_payload = _node_group_payload_by_id(
                command_runner=command_runner,
                node_group_id=node_group_id,
            )
            _validate_reused_target_node_group(
                role=role,
                target_name=existing_name,
                node_group=existing_payload or existing,
                expected_count=target_count,
                expected_template=expected_template,
                allow_larger_count=role_based_reuse,
                validate_template_shape=not role_based_reuse,
            )
            target_groups[role] = {
                "id": node_group_id,
                "name": existing_name,
                "fixed_node_count": _node_group_fixed_count(existing_payload or existing),
                "created": False,
                "role_based_reuse": role_based_reuse,
            }
            lines.append(f"Target node group {role}: reused {existing_name} ({node_group_id}).")
            continue

        create_payload = {
            "metadata": {"parent_id": cluster_id, "name": target_name},
            "spec": {
                "version": str(_mapping(source_node_group.get("spec")).get("version", "") or ""),
                "fixed_node_count": target_count,
                "template": expected_template,
            },
        }
        created = _json_from_command(
            command_runner,
            [
                "nebius",
                "mk8s",
                "node-group",
                "create",
                json.dumps(create_payload, sort_keys=True),
                "--format",
                "json",
                "--timeout",
                "60m",
            ],
            timeout_seconds=3900,
        )
        node_group_id = _node_group_id(created)
        if not node_group_id:
            node_group_id = _node_group_id(
                _find_node_group_by_name(
                    _list_node_groups(command_runner=command_runner, cluster_id=cluster_id),
                    target_name,
                )
            )
        if not node_group_id:
            raise RuntimeError(f"target node group '{target_name}' did not return an id.")
        target_groups[role] = {
            "id": node_group_id,
            "name": target_name,
            "fixed_node_count": target_count,
            "created": True,
        }
        mutation_performed = True
        lines.append(f"Target node group {role}: created {target_name} ({node_group_id}).")
    phase["cluster_id"] = cluster_id
    phase["source_groups"] = dict(source_groups_by_kind)
    lines.append(
        "Worker node groups preserved in place: " + ", ".join(in_place_worker_groups) + "."
    )
    return mutation_performed, lines


_SOPERATOR_FS_QUOTA_SUFFIX_BY_TYPE = {
    "network_ssd": "network-ssd",
    "network_hdd": "network-hdd",
    "weka": "weka",
    "vast": "vast",
}


def _soperator_quota_component_label(target_ref: str) -> str:
    return f"Soperator migration target {target_ref}"


def _soperator_quota_requirement(
    *,
    target_ref: str,
    quota_name: str,
    region: str,
    required: int,
    reason: str,
) -> QuotaRequirement:
    return QuotaRequirement(
        component_id="soperator-migration",
        instance_id=target_ref,
        component_label=_soperator_quota_component_label(target_ref),
        quota_name=quota_name,
        region=region,
        required=required,
        reason=reason,
    )


def _new_aligned_sfs_quota_requirements(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[list[QuotaRequirement], list[QuotaCoverageGap], list[str]]:
    project_id = _nebius_project_id(payload)
    _tenant_id, _project_id, region = _nebius_identity(payload)
    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []
    lines: list[str] = []
    for spec in _aligned_filesystem_specs(payload=payload, target_ref=target_ref):
        existing = _get_filesystem_by_name(
            command_runner=command_runner,
            project_id=project_id,
            name=spec.name,
        )
        if _filesystem_id(existing):
            lines.append(f"Quota preflight SFS {spec.key}: existing {spec.name}, no new quota.")
            continue
        quota_suffix = _SOPERATOR_FS_QUOTA_SUFFIX_BY_TYPE.get(spec.filesystem_type)
        requirements.append(
            _soperator_quota_requirement(
                target_ref=target_ref,
                quota_name="compute.filesystem.count",
                region=region,
                required=1,
                reason=f"aligned SFS filesystem '{spec.name}' for {spec.key}",
            )
        )
        if quota_suffix:
            requirements.append(
                _soperator_quota_requirement(
                    target_ref=target_ref,
                    quota_name=f"compute.filesystem.size.{quota_suffix}",
                    region=region,
                    required=spec.size_gib * _GIB,
                    reason=f"{spec.size_gib} GiB aligned SFS filesystem '{spec.name}'",
                )
            )
        else:
            gaps.append(
                QuotaCoverageGap(
                    component_id="soperator-migration",
                    instance_id=target_ref,
                    component_label=_soperator_quota_component_label(target_ref),
                    message=(
                        f"quota name for SFS filesystem type '{spec.filesystem_type}' "
                        f"could not be resolved for '{spec.name}'"
                    ),
                )
            )
        lines.append(f"Quota preflight SFS {spec.key}: will create {spec.name}.")
    return requirements, gaps, lines


def _planned_target_node_group_quota_inputs(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[Mapping[str, Any], list[str]]:
    inventory = _source_node_group_inventory(source_report)
    source_groups_by_kind = _source_compute_group_names(
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    source_payloads: dict[str, Mapping[str, Any]] = {}
    for kind, group_name in source_groups_by_kind.items():
        raw_group = inventory.get(group_name)
        if not isinstance(raw_group, Mapping):
            continue
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            raise SoperatorMigrationPhasePending(
                f"quota preflight requires Nebius node group id for source group '{group_name}'."
            )
        source_payloads[kind] = _node_group_payload_by_id(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
    cluster_id = _target_cluster_id(payload, target_ref)
    if not cluster_id:
        cluster_id = next(
            (
                _node_group_parent_id(source_payload)
                for source_payload in source_payloads.values()
                if _node_group_parent_id(source_payload)
            ),
            "",
        )
    if not cluster_id:
        raise SoperatorMigrationPhasePending(
            "quota preflight could not resolve the Nebius MK8s cluster id from config "
            "or source node-group metadata."
        )

    live_node_groups = _list_node_groups(command_runner=command_runner, cluster_id=cluster_id)
    planned_groups: dict[str, Any] = {}
    lines: list[str] = []
    for role in _SOPERATOR_SERVICE_ROLES:
        target_name = _target_node_group_name(target_ref, role)
        existing, existing_name, role_based_reuse = _find_service_role_node_group(
            live_node_groups,
            role=role,
            target_name=target_name,
        )
        source_kind = _SOPERATOR_ROLE_SOURCE_KIND[role]
        source_node_group = source_payloads[source_kind]
        target_count = 1
        expected_template = _role_node_group_template(
            role=role,
            source_node_group=source_node_group,
            filesystems=[],
        )
        if existing:
            node_group_id = _node_group_id(existing)
            existing_payload = (
                _node_group_payload_by_id(
                    command_runner=command_runner,
                    node_group_id=node_group_id,
                )
                if node_group_id
                else existing
            )
            _validate_reused_target_node_group(
                role=role,
                target_name=existing_name,
                node_group=existing_payload or existing,
                expected_count=target_count,
                expected_template=expected_template,
                allow_larger_count=role_based_reuse,
                validate_template_shape=not role_based_reuse,
            )
            lines.append(
                f"Quota preflight node group {role}: existing {existing_name}, no new quota."
            )
            continue
        planned_groups[target_name] = {
            "node_count": target_count,
            "gpu": False,
            "template": expected_template,
        }
        lines.append(
            f"Quota preflight node group {role}: will create {target_name} "
            f"with {target_count} node(s)."
        )
    if worker_node_groups:
        lines.append(
            "Quota preflight worker node groups: compute migration preserves existing "
            "worker groups in place ("
            + ", ".join(worker_node_groups)
            + "); external node-template safe-surge spare capacity is checked separately "
            "when that action is selected."
        )
    return planned_groups, lines


def _new_target_node_group_quota_requirements(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[list[QuotaRequirement], list[QuotaCoverageGap], list[str]]:
    _tenant_id, project_id, region = _nebius_identity(payload)
    planned_groups, lines = _planned_target_node_group_quota_inputs(
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        worker_node_groups=worker_node_groups,
        command_runner=command_runner,
    )
    if not planned_groups:
        return [], [], lines
    requirements, gaps = estimate_mk8s_quota_requirements(
        project_id=project_id,
        region=region,
        instance_id=f"{target_ref}-soperator-migration",
        inputs={"node_groups": dict(planned_groups)},
        context="soperator migration quota preflight",
    )
    return (
        [item for item in requirements if item.quota_name != "mk8s.cluster.count"],
        list(gaps),
        lines,
    )


def _max_quota_requirements_by_shape(
    requirements: Sequence[QuotaRequirement],
) -> list[QuotaRequirement]:
    by_key: dict[tuple[str, str, Any], QuotaRequirement] = {}
    for item in requirements:
        key = (item.quota_name, item.region, item.gpu_capacity_shape)
        existing = by_key.get(key)
        if existing is None or item.required > existing.required:
            by_key[key] = item
    return list(by_key.values())


def _quota_requirement_totals_label(requirements: Sequence[QuotaRequirement]) -> str:
    totals: dict[tuple[str, str], int] = {}
    for item in requirements:
        totals[(item.region, item.quota_name)] = (
            totals.get((item.region, item.quota_name), 0) + int(item.required)
        )
    if not totals:
        return "no quota counters"
    parts = [
        f"{region} {quota_name}={required}"
        for (region, quota_name), required in sorted(totals.items())
    ]
    if len(parts) > 8:
        parts = [*parts[:8], f"+{len(parts) - 8} more"]
    return "; ".join(parts)


def _worker_surge_node_group_quota_requirements(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
    rollout: SoperatorExternalNodeTemplateRollout,
) -> tuple[list[QuotaRequirement], list[QuotaCoverageGap], list[str]]:
    _tenant_id, project_id, region = _nebius_identity(payload)
    if rollout.strategy != SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE:
        return (
            [],
            [],
            [
                "Quota preflight worker safe-surge: disabled by zero-surge worker "
                "rollout strategy; no surge worker quota required."
            ],
        )
    _service_groups, worker_groups = _split_external_node_template_upgrade_groups(
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    if not worker_groups:
        return [], [], ["Quota preflight worker safe-surge: no worker groups detected."]
    waves = _external_node_template_worker_waves(worker_groups, rollout=rollout)
    surge_count = rollout.strategy_max_surge_count
    if surge_count <= 0:
        return (
            [],
            [],
            [
                "Quota preflight worker safe-surge: no surge worker quota required "
                "because strategy max_surge_count is 0."
            ],
        )
    all_requirements: list[QuotaRequirement] = []
    all_gaps: list[QuotaCoverageGap] = []
    lines: list[str] = [
        "[green]Verified[/green] worker safe-surge preflight: checking spare capacity for "
        + _worker_rollout_budget_label(rollout, worker_group_count=len(worker_groups))
        + f" with {surge_count} surge node(s) per active worker group"
        + "."
    ]
    for wave_index, wave in enumerate(waves, start=1):
        planned_groups: dict[str, Any] = {}
        for group_name, raw_group in wave:
            node_group_id = _source_group_node_group_id(raw_group)
            if not node_group_id:
                raise SoperatorMigrationPhasePending(
                    "quota preflight requires Nebius node group id for source worker "
                    f"group '{group_name}'."
                )
            node_group = _node_group_payload_by_id(
                command_runner=command_runner,
                node_group_id=node_group_id,
            )
            template = _mapping(_mapping(node_group.get("spec")).get("template"))
            if not template:
                raise SoperatorMigrationPhasePending(
                    "quota preflight could not clone a Nebius node template for source "
                    f"worker group '{group_name}'."
                )
            planned_groups[f"{target_ref}-{group_name}-safe-surge"] = {
                "node_count": surge_count,
                "gpu": _source_group_is_gpu(raw_group),
                "template": dict(to_plain_data(_lower_nebius_enums(template))),
            }
        requirements, gaps = estimate_mk8s_quota_requirements(
            project_id=project_id,
            region=region,
            instance_id=f"{target_ref}-soperator-worker-surge-wave-{wave_index}",
            inputs={"node_groups": planned_groups},
            context="soperator migration worker safe-surge quota preflight",
        )
        wave_requirements = [
            item for item in requirements if item.quota_name != "mk8s.cluster.count"
        ]
        all_requirements.extend(wave_requirements)
        all_gaps.extend(gaps)
        lines.append(
            f"[green]Verified[/green] worker safe-surge wave {wave_index}: "
            + ", ".join(name for name, _raw_group in wave)
            + (
                f" requires {surge_count} temporary surge node(s) per group, "
                f"{len(wave) * surge_count} total; "
            )
            + "quota totals: "
            + _quota_requirement_totals_label(wave_requirements)
            + "."
        )
    return _max_quota_requirements_by_shape(all_requirements), all_gaps, lines


def _normalized_token_set(*values: Any) -> frozenset[str]:
    return frozenset(
        token
        for token in (normalize_component_token(value) for value in values)
        if token
    )


def _source_group_label_tokens(
    raw_group: Mapping[str, Any],
    keys: Sequence[str],
) -> frozenset[str]:
    labels = _source_group_labels(raw_group)
    return _normalized_token_set(*(labels.get(key) for key in keys))


def _source_group_node_tokens(raw_group: Mapping[str, Any]) -> frozenset[str]:
    raw_nodes = raw_group.get("nodes")
    if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes, bytearray)):
        return frozenset()
    return _normalized_token_set(*raw_nodes)


def _worker_node_source_group_match_score(
    item: Mapping[str, Any],
    group_name: str,
    raw_group: Mapping[str, Any],
) -> int:
    node_name = normalize_component_token(_node_name(item))
    if node_name and node_name in _source_group_node_tokens(raw_group):
        return 100

    labels = _node_labels(item)
    id_tokens = _normalized_token_set(
        group_name,
        _source_group_node_group_id(raw_group),
        raw_group.get("node_group_id"),
        raw_group.get("id"),
        *_source_group_label_tokens(raw_group, _SOURCE_NODE_GROUP_ID_LABEL_KEYS),
    )
    if any(
        normalize_component_token(labels.get(key)) in id_tokens
        for key in _SOURCE_NODE_GROUP_ID_LABEL_KEYS
        if normalize_component_token(labels.get(key))
    ):
        return 90

    name_tokens = _normalized_token_set(
        group_name,
        _source_group_node_group_name(raw_group),
        raw_group.get("node_group_name"),
        raw_group.get("name"),
        *_source_group_label_tokens(raw_group, _SOURCE_NODE_GROUP_NAME_LABEL_KEYS),
    )
    if any(
        normalize_component_token(labels.get(key)) in name_tokens
        for key in _SOURCE_NODE_GROUP_NAME_LABEL_KEYS
        if normalize_component_token(labels.get(key))
    ):
        return 80

    nodeset_name_tokens = _source_group_label_tokens(
        raw_group,
        ("slurm.nebius.ai/nodeset-name",),
    )
    nodeset_name = normalize_component_token(labels.get("slurm.nebius.ai/nodeset-name"))
    if nodeset_name and nodeset_name in nodeset_name_tokens:
        return 70

    nodeset_tokens = _source_group_label_tokens(raw_group, ("slurm.nebius.ai/nodeset",))
    nodeset = normalize_component_token(labels.get("slurm.nebius.ai/nodeset"))
    if nodeset and nodeset in nodeset_tokens:
        return 10

    return 0


def _source_group_match_tokens(group_name: str, raw_group: Mapping[str, Any]) -> frozenset[str]:
    labels = _source_group_labels(raw_group)
    tokens = set(
        _normalized_token_set(
            group_name,
            _source_group_node_group_id(raw_group),
            _source_group_node_group_name(raw_group),
            _source_group_nodeset(raw_group),
            raw_group.get("node_group_id"),
            raw_group.get("id"),
            raw_group.get("node_group_name"),
            raw_group.get("name"),
        )
    )
    raw_nodes = raw_group.get("nodes")
    if isinstance(raw_nodes, Sequence) and not isinstance(raw_nodes, (str, bytes, bytearray)):
        tokens.update(_source_group_node_tokens(raw_group))
    tokens.update(
        normalize_component_token(labels.get(key))
        for key in (
            *_SOURCE_NODE_GROUP_NAME_LABEL_KEYS,
            *_SOURCE_NODE_GROUP_ID_LABEL_KEYS,
            *_SOPERATOR_NODESET_LABEL_KEYS,
        )
        if normalize_component_token(labels.get(key))
    )
    return frozenset(token for token in tokens if token)


def _worker_nodes_by_source_group(
    *,
    nodes: Sequence[Mapping[str, Any]],
    worker_groups: Sequence[tuple[str, Mapping[str, Any]]],
) -> dict[str, list[Mapping[str, Any]]]:
    grouped = {group_name: [] for group_name, _raw_group in worker_groups}
    for item in nodes:
        matches = [
            (score, group_name)
            for group_name, raw_group in worker_groups
            if (score := _worker_node_source_group_match_score(item, group_name, raw_group)) > 0
        ]
        if not matches:
            continue
        best_score = max(score for score, _group_name in matches)
        winners = [group_name for score, group_name in matches if score == best_score]
        if len(winners) == 1:
            grouped[winners[0]].append(item)
    return grouped


def _node_matches_worker_tokens(item: Mapping[str, Any], tokens: frozenset[str]) -> bool:
    if not tokens:
        return False
    node_name = normalize_component_token(_node_name(item))
    if node_name and node_name in tokens:
        return True
    labels = _node_labels(item)
    for key in (
        *_SOURCE_NODE_GROUP_NAME_LABEL_KEYS,
        *_SOURCE_NODE_GROUP_ID_LABEL_KEYS,
        *_SOPERATOR_NODESET_LABEL_KEYS,
    ):
        value = normalize_component_token(labels.get(key))
        if value and value in tokens:
            return True
    group = normalize_component_token(_node_group_label(item))
    return bool(group and group in tokens)


def _read_kubernetes_nodes_for_worker_rollout(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> tuple[Mapping[str, Any], ...]:
    try:
        result = command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "get",
                "nodes",
                "-o",
                "json",
                "--request-timeout=20s",
            ],
            timeout_seconds=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Soperator worker rollout preflight timed out while inspecting Kubernetes "
            "nodes before mutation."
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            "Soperator worker rollout preflight could not inspect Kubernetes nodes "
            "before mutation: " + _command_detail(result)
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Soperator worker rollout preflight received invalid Kubernetes node JSON."
        ) from exc
    return tuple(
        item
        for item in _sequence_of_mappings(
            payload.get("items") if isinstance(payload, Mapping) else None
        )
    )


def _slurm_queue_preflight_lines(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    source_report: Mapping[str, Any],
) -> list[str]:
    source_snapshot = _mapping(source_report.get("snapshot"))
    if not _has_live_slurmcluster_resource(source_snapshot):
        return ["Slurm worker rollout preflight: no live source SlurmCluster detected."]
    result = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("squeue", "-h", "-o", "%T"),
        check=False,
        timeout_seconds=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Soperator worker rollout preflight could not inspect Slurm jobs from a "
            "login pod before mutation: "
            + _command_detail(result)
        )
    jobs = [line.strip().lower() for line in result.stdout.splitlines() if line.strip()]
    if jobs:
        raise RuntimeError(
            "Soperator worker rollout preflight failed before mutation: Slurm queue is "
            "not empty (jobs "
            + _state_counts(jobs)
            + "). Wait for jobs to finish or drain the workload intentionally before "
            "rerunning migration."
        )
    return ["Slurm worker rollout preflight: queue empty."]


def _run_soperator_worker_rollout_live_preflight(
    *,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    rollout: SoperatorExternalNodeTemplateRollout,
) -> list[str]:
    unavailable_budget = 0
    budget_label = f"{rollout.strategy} requires selected worker groups to start healthy"
    _service_groups, worker_groups = _split_external_node_template_upgrade_groups(
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    if not worker_groups:
        return ["Worker rollout live preflight: no worker node groups selected."]
    nodes = _read_kubernetes_nodes_for_worker_rollout(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    nodes_by_group = _worker_nodes_by_source_group(
        nodes=nodes,
        worker_groups=worker_groups,
    )
    selected_nodes = tuple(item for group_nodes in nodes_by_group.values() for item in group_nodes)
    if not selected_nodes:
        raise RuntimeError(
            "Soperator worker rollout preflight failed before mutation: no Kubernetes "
            "nodes matched the selected source worker node groups "
            + ", ".join(name for name, _raw_group in worker_groups)
            + ". Rerun onboarding after confirming live node labels are readable."
        )

    unavailable = [
        item for item in selected_nodes if not _node_ready(item) or _node_cordoned(item)
    ]
    empty_groups = sorted(
        name
        for name, group_nodes in nodes_by_group.items()
        if not any(_node_ready(item) and not _node_cordoned(item) for item in group_nodes)
    )
    if empty_groups:
        raise RuntimeError(
            "Soperator worker rollout preflight failed before mutation: selected worker "
            "node group(s) have no Ready schedulable nodes: "
            + ", ".join(empty_groups)
            + ". Restore worker health before changing node templates."
        )
    if len(unavailable) > unavailable_budget:
        details = [
            (_node_name(item) or f"worker-node-{index}")
            + (
                ":NotReady"
                if not _node_ready(item)
                else ":cordoned"
            )
            for index, item in enumerate(unavailable, start=1)
        ]
        raise RuntimeError(
            "Soperator worker rollout preflight failed before mutation: selected worker "
            "nodes must start Ready and schedulable, but "
            f"{len(unavailable)} unavailable node(s) were found. "
            "Problem nodes: "
            + _format_problem_node_details(details)
            + "."
        )

    ready = len(selected_nodes) - len(unavailable)
    lines = [
        "Worker rollout live preflight: "
        f"{ready}/{len(selected_nodes)} selected worker nodes Ready/schedulable; "
        f"current unavailable {len(unavailable)}/{unavailable_budget} ({budget_label})."
    ]
    lines.extend(
        _slurm_queue_preflight_lines(
            command_runner=command_runner,
            kube_context=kube_context,
            source_report=source_report,
        )
    )
    return lines


def _quota_preflight_failure_message(report: QuotaReport) -> str:
    detail_lines = format_quota_report_lines(
        report,
        phase="soperator migration",
        include_confirmed_components=True,
    )
    details = "\n".join(detail_lines).strip()
    if not details:
        details = "live quota could not be confirmed for this migration plan"
    return (
        "Soperator migration quota preflight failed before any cluster mutation. "
        "Resolve confirmed shortages, unresolved quota limits, quota coverage gaps, "
        "or quota lookup errors before rerunning migration.\n" + details
    )


def _quota_preflight_success_lines(report: QuotaReport, plan_lines: Sequence[str]) -> list[str]:
    lines = list(plan_lines)
    if not report.checks and not report.coverage_gaps and not report.errors:
        lines.append("Quota preflight: no net-new Soperator migration quota required.")
        return lines
    lines.extend(
        format_quota_report_lines(
            report,
            phase="soperator migration",
            include_confirmed_components=True,
        )
    )
    if report.sufficient_checks:
        lines.append("Quota preflight: all checked migration quota requirements are sufficient.")
    return lines


def _run_soperator_migration_quota_preflight(
    *,
    checkpoint: dict[str, Any],
    completed_phases: set[str],
    phase_ids: Sequence[str],
    payload: Mapping[str, Any],
    target_ref: str,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
    rollout: SoperatorExternalNodeTemplateRollout,
) -> list[str]:
    tenant_id, project_id, region_id = _nebius_identity(payload)
    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []
    plan_lines: list[str] = []

    if "create-aligned-sfs" in phase_ids and "create-aligned-sfs" not in completed_phases:
        storage_requirements, storage_gaps, storage_lines = _new_aligned_sfs_quota_requirements(
            payload=payload,
            target_ref=target_ref,
            command_runner=command_runner,
        )
        requirements.extend(storage_requirements)
        gaps.extend(storage_gaps)
        plan_lines.extend(storage_lines)

    source_snapshot = _mapping(source_report.get("snapshot"))
    if (
        "rolling-compute-migration" in phase_ids
        and "rolling-compute-migration" not in completed_phases
        and _has_soperator_custom_resources(source_snapshot)
    ):
        compute_requirements, compute_gaps, compute_lines = (
            _new_target_node_group_quota_requirements(
                payload=payload,
                target_ref=target_ref,
                source_report=source_report,
                worker_node_groups=worker_node_groups,
                command_runner=command_runner,
            )
        )
        requirements.extend(compute_requirements)
        gaps.extend(compute_gaps)
        plan_lines.extend(compute_lines)

    if (
        _EXTERNAL_NODE_TEMPLATE_PHASE_ID in phase_ids
        and _EXTERNAL_NODE_TEMPLATE_PHASE_ID not in completed_phases
    ):
        worker_requirements, worker_gaps, worker_lines = (
            _worker_surge_node_group_quota_requirements(
                payload=payload,
                target_ref=target_ref,
                source_report=source_report,
                worker_node_groups=worker_node_groups,
                command_runner=command_runner,
                rollout=rollout,
            )
        )
        requirements.extend(worker_requirements)
        gaps.extend(worker_gaps)
        plan_lines.extend(worker_lines)

    if not requirements and not gaps:
        report = QuotaReport(
            tenant_id=tenant_id,
            project_id=project_id,
            region_id=region_id,
            checked_at=datetime.now(UTC).isoformat(),
        )
    else:
        report = assess_live_quota_requirements(
            tenant_id=tenant_id,
            project_id=project_id,
            region_id=region_id,
            requirements=requirements,
            coverage_gaps=gaps,
            context="soperator migration quota preflight",
        )
    checkpoint["quota_preflight"] = report.to_manifest_dict()
    if (
        report.has_confirmed_insufficiency
        or report.unknown_checks
        or report.coverage_gaps
        or report.errors
    ):
        raise RuntimeError(_quota_preflight_failure_message(report))
    return _quota_preflight_success_lines(report, plan_lines)


def _login_pod_name(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> str:
    try:
        parsed = _json_value_from_command(
            command_runner,
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "get",
                "pods",
                "-o",
                "json",
                "--request-timeout=20s",
            ],
            timeout_seconds=30,
        )
    except subprocess.TimeoutExpired:
        return ""
    items = parsed.get("items", []) if isinstance(parsed, Mapping) else []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return ""
    best_name = ""
    for item in items:
        if not isinstance(item, Mapping):
            continue
        metadata = _mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        labels = _mapping(metadata.get("labels"))
        label_text = " ".join(str(value) for value in labels.values())
        phase = str(_mapping(item.get("status")).get("phase", "") or "")
        if name and phase == "Running" and ("login" in name or "login" in label_text):
            return name
        if name and not best_name and ("login" in name or "login" in label_text):
            best_name = name
    return best_name


def _kubectl_exec_login(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    args: Sequence[str],
    check: bool = True,
    timeout_seconds: int = 300,
) -> SoperatorMigrationCommandResult:
    pod = _login_pod_name(command_runner=command_runner, kube_context=kube_context)
    if not pod:
        return SoperatorMigrationCommandResult(tuple(args), 1, "", "login pod not found")
    return command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "exec",
            pod,
            "--",
            *args,
        ],
        check=check,
        timeout_seconds=timeout_seconds,
    )


_STATUS_PHASES_WITH_STORAGE = frozenset(
    {
        "create-aligned-sfs",
        "online-bulk-data-sync",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
        "retire-old-resources",
    }
)
_STATUS_PHASES_WITH_COMPUTE = frozenset(
    {
        "rolling-compute-migration",
        "final-control-plane-cutover",
        "validation-and-rollback-hold",
        "retire-old-resources",
    }
)
_STATUS_PHASES_WITH_MK8S_ONLY = frozenset(
    {_EXTERNAL_NODE_TEMPLATE_PHASE_ID, _TARGET_GPU_STACK_PHASE_ID}
)
_STATUS_STATE_RANK = {
    "serving": 0,
    "draining": 1,
    "degraded": 2,
    "unknown": 3,
    "pending": 4,
    "down": 5,
}
_STATUS_MAX_NODE_GROUP_DETAILS = 6
_STATUS_MAX_PROBLEM_NODE_DETAILS = 8


def _format_status_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, seconds_part = divmod(total, 60)
    hours, minutes_part = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes_part:02d}m{seconds_part:02d}s"
    if minutes:
        return f"{minutes}m{seconds_part:02d}s"
    return f"{seconds_part}s"


def _command_detail(result: SoperatorMigrationCommandResult) -> str:
    return result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"


def _node_ready(item: Mapping[str, Any]) -> bool:
    status = _mapping(item.get("status"))
    conditions = status.get("conditions")
    if not isinstance(conditions, Sequence) or isinstance(conditions, (str, bytes, bytearray)):
        return False
    for condition in conditions:
        if not isinstance(condition, Mapping):
            continue
        if str(condition.get("type", "") or "").strip() != "Ready":
            continue
        return str(condition.get("status", "") or "").strip().lower() == "true"
    return False


def _node_name(item: Mapping[str, Any]) -> str:
    return str(_mapping(item.get("metadata")).get("name", "") or "").strip()


def _node_labels(item: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(_mapping(item.get("metadata")).get("labels"))


def _node_cordoned(item: Mapping[str, Any]) -> bool:
    return _mapping(item.get("spec")).get("unschedulable") is True


def _node_group_label(item: Mapping[str, Any]) -> str:
    labels = _node_labels(item)
    for key in (
        "nebius.com/node-group",
        "yandex.cloud/node-group-id",
        "nebius.com/node-group-id",
        "slurm.nebius.ai/nodeset-name",
        "slurm.nebius.ai/nodeset",
        "node.kubernetes.io/instance-type",
    ):
        value = str(labels.get(key, "") or "").strip()
        if value:
            return value
    return "unlabeled"


def _updating_external_node_template_groups(
    *,
    checkpoint: Mapping[str, Any],
    phase_id: str,
) -> frozenset[str]:
    if phase_id != _EXTERNAL_NODE_TEMPLATE_PHASE_ID:
        return frozenset()
    phase = _mapping(_mapping(checkpoint.get("phase_state")).get(phase_id))
    node_groups = _mapping(phase.get("node_groups"))
    labels: set[str] = set()
    for source_group, raw_state in node_groups.items():
        state = _mapping(raw_state)
        if str(state.get("status", "") or "").strip().lower() != "updating":
            continue
        for value in (
            source_group,
            state.get("source_group"),
            state.get("node_group_name"),
            state.get("node_group_id"),
        ):
            text = str(value or "").strip()
            if text:
                labels.add(text)
                labels.add(normalize_component_token(text) or text)
    return frozenset(labels)


def _node_in_group_set(item: Mapping[str, Any], groups: frozenset[str]) -> bool:
    if not groups:
        return False
    labels = _node_labels(item)
    for key in (
        "nebius.com/node-group",
        "yandex.cloud/node-group-id",
        "nebius.com/node-group-id",
        "slurm.nebius.ai/nodeset-name",
        "slurm.nebius.ai/nodeset",
        "node.kubernetes.io/instance-type",
    ):
        value = str(labels.get(key, "") or "").strip()
        if value and (value in groups or (normalize_component_token(value) or value) in groups):
            return True
    group = _node_group_label(item)
    return group in groups or (normalize_component_token(group) or group) in groups


def _format_problem_node_details(
    details: Sequence[str],
    *,
    max_items: int = _STATUS_MAX_PROBLEM_NODE_DETAILS,
) -> str:
    visible = list(details[:max_items])
    suffix = f", +{len(details) - max_items} more" if len(details) > max_items else ""
    return ", ".join(visible) + suffix


def _collect_mk8s_status(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    checkpoint: Mapping[str, Any],
    phase_id: str,
) -> SoperatorMigrationStatusSignal:
    try:
        result = command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "get",
                "nodes",
                "-o",
                "json",
                "--request-timeout=20s",
            ],
            timeout_seconds=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return SoperatorMigrationStatusSignal(
            "MK8s Node Groups",
            "unknown",
            "node status timed out while listing Kubernetes nodes",
        )
    if result.returncode != 0:
        return SoperatorMigrationStatusSignal(
            "MK8s Node Groups",
            "unknown",
            "node status unavailable: " + _command_detail(result),
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return SoperatorMigrationStatusSignal(
            "MK8s Node Groups",
            "unknown",
            "node status returned invalid JSON",
        )
    items = payload.get("items") if isinstance(payload, Mapping) else None
    nodes = tuple(item for item in _sequence_of_mappings(items))
    if not nodes:
        return SoperatorMigrationStatusSignal(
            "MK8s Node Groups",
            "down",
            "Node groups: 0 group(s) || Nodes: 0/0 Ready",
        )
    total = len(nodes)
    ready = sum(1 for item in nodes if _node_ready(item))
    cordoned = sum(1 for item in nodes if _mapping(item.get("spec")).get("unschedulable") is True)
    updating_groups = _updating_external_node_template_groups(
        checkpoint=checkpoint,
        phase_id=phase_id,
    )
    groups: dict[str, list[int]] = {}
    transition_nodes: list[str] = []
    problem_nodes: list[str] = []
    for item in nodes:
        group = groups.setdefault(_node_group_label(item), [0, 0])
        group[1] += 1
        node_ready = _node_ready(item)
        cordoned_node = _node_cordoned(item)
        if node_ready:
            group[0] += 1
        if node_ready and not cordoned_node:
            continue
        node_name = _node_name(item) or f"node-{len(transition_nodes) + len(problem_nodes) + 1}"
        upgrading = _node_in_group_set(item, updating_groups)
        if upgrading:
            detail = "replacing (down)" if not node_ready else "replacing (cordoned)"
            transition_nodes.append(f"{node_name}:{detail}")
        elif cordoned_node:
            transition_nodes.append(f"{node_name}:cordoned")
        else:
            problem_nodes.append(f"{node_name}:NotReady (down)")
    group_summary = ", ".join(
        f"{name}:{counts[0]}/{counts[1]} Ready"
        for name, counts in sorted(groups.items())[:_STATUS_MAX_NODE_GROUP_DETAILS]
    )
    if len(groups) > _STATUS_MAX_NODE_GROUP_DETAILS:
        group_summary += f", +{len(groups) - _STATUS_MAX_NODE_GROUP_DETAILS} more groups"
    group_parts = [f"{len(groups)} group(s)"]
    if group_summary:
        group_parts.append(group_summary)
    node_parts = [f"{ready}/{total} Ready"]
    if cordoned:
        node_parts.append(f"{cordoned} cordoned")
    if transition_nodes:
        node_parts.append("in transition " + _format_problem_node_details(transition_nodes))
    if problem_nodes:
        node_parts.append("problem nodes " + _format_problem_node_details(problem_nodes))
    state = "serving"
    if ready <= 0:
        state = "down"
    elif ready < total or cordoned:
        state = "degraded"
    summary = "Node groups: " + "; ".join(group_parts) + " || Nodes: " + "; ".join(node_parts)
    return SoperatorMigrationStatusSignal("MK8s Node Groups", state, summary)


def _storage_status_expected_pvcs(
    *,
    source_report: Mapping[str, Any],
    payload: Mapping[str, Any],
    target_ref: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    source_names: list[str] = []
    target_names: list[str] = []
    for key in _SOPERATOR_STORAGE_KEYS:
        source_pvc = _source_pvc_name_for_storage_key(source_report, key)
        target_pvc = _target_pvc_name_for_storage_key(payload, target_ref, key)
        if source_pvc:
            source_names.append(source_pvc)
            if target_pvc and target_pvc != source_pvc:
                target_names.append(target_pvc)
    return tuple(dict.fromkeys(source_names)), tuple(dict.fromkeys(target_names))


def _collect_storage_status(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    checkpoint: Mapping[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    target_ref: str,
) -> SoperatorMigrationStatusSignal:
    create_state = _mapping(_mapping(checkpoint.get("phase_state")).get("create-aligned-sfs"))
    filesystems = _mapping(create_state.get("filesystems"))
    aligned_summary = f"aligned SFS {len(filesystems)}/{len(_SOPERATOR_STORAGE_KEYS)}"
    source_pvcs, target_pvcs = _storage_status_expected_pvcs(
        source_report=source_report,
        payload=payload,
        target_ref=target_ref,
    )
    if not source_pvcs and not _snapshot_storage(source_report):
        return SoperatorMigrationStatusSignal(
            "Storage",
            "serving",
            f"{aligned_summary}; no source PVC copy pairs planned",
        )

    result = command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "get",
            "pvc",
            "-o",
            "json",
        ],
        timeout_seconds=60,
        check=False,
    )
    if result.returncode != 0:
        return SoperatorMigrationStatusSignal(
            "Storage",
            "unknown",
            "PVC status unavailable: " + _command_detail(result),
        )
    try:
        payload_json = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return SoperatorMigrationStatusSignal(
            "Storage", "unknown", "PVC status returned invalid JSON"
        )
    present = {
        str(_mapping(item.get("metadata")).get("name", "") or "").strip()
        for item in _sequence_of_mappings(
            payload_json.get("items") if isinstance(payload_json, Mapping) else None
        )
    }
    present.discard("")
    source_present = sum(1 for name in source_pvcs if name in present)
    target_present = sum(1 for name in target_pvcs if name in present)
    online_state = _mapping(_mapping(checkpoint.get("phase_state")).get("online-bulk-data-sync"))
    jobs = _mapping(online_state.get("jobs"))
    copy_jobs = sum(
        1 for item in jobs.values() if isinstance(item, Mapping) and item.get("skipped") is not True
    )
    parts = [
        aligned_summary,
        f"source PVCs {source_present}/{len(source_pvcs)}",
        f"target PVCs {target_present}/{len(target_pvcs)}",
    ]
    if copy_jobs:
        parts.append(f"copy jobs {copy_jobs}")
    state = "serving"
    if source_present < len(source_pvcs):
        state = "down"
    elif target_pvcs and target_present < len(target_pvcs):
        state = "degraded"
    return SoperatorMigrationStatusSignal("Storage", state, "; ".join(parts))


def _normalize_slurm_node_state(value: str) -> str:
    text = value.strip().lower()
    while text and text[-1] in "*~#!%@^-+$":
        text = text[:-1]
    aliases = {
        "alloc": "allocated",
        "comp": "completing",
        "drain": "drained",
        "drng": "draining",
        "failg": "failing",
        "mix": "mixed",
        "unk": "unknown",
    }
    return aliases.get(text, text)


def _state_counts(values: Sequence[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        key = value.strip().lower() or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _parse_slurm_node_states(output: str) -> tuple[tuple[str, str], ...]:
    nodes: list[tuple[str, str]] = []
    for line in output.splitlines():
        text = line.strip()
        if not text:
            continue
        parts = text.split()
        if len(parts) >= 2:
            nodes.append((parts[0], _normalize_slurm_node_state(parts[-1])))
        else:
            nodes.append(("", _normalize_slurm_node_state(parts[0])))
    return tuple(nodes)


def _slurm_problem_worker_details(nodes: Sequence[tuple[str, str]]) -> tuple[str, ...]:
    unavailable = {
        "down",
        "fail",
        "failing",
        "unknown",
        "invalid",
        "future",
    }
    draining = {"drained", "draining"}
    details: list[str] = []
    for index, (name, state) in enumerate(nodes, start=1):
        if state not in unavailable and state not in draining:
            continue
        label = name or f"worker-{index}"
        if state in unavailable:
            details.append(f"{label}:{state} (down)")
        else:
            details.append(f"{label}:{state}")
    return tuple(details)


def _collect_slurm_status(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> SoperatorMigrationStatusSignal:
    try:
        sinfo = _kubectl_exec_login(
            command_runner=command_runner,
            kube_context=kube_context,
            args=("sinfo", "-h", "-o", "%N %T"),
            check=False,
            timeout_seconds=90,
        )
    except Exception as exc:
        return SoperatorMigrationStatusSignal(
            "Slurm",
            "unknown",
            f"login or sinfo status failed: {exc}",
        )
    if sinfo.returncode != 0:
        return SoperatorMigrationStatusSignal(
            "Slurm Workers",
            "down",
            "login or sinfo unavailable: " + _command_detail(sinfo),
        )
    node_states = _parse_slurm_node_states(sinfo.stdout)
    states = [state for _name, state in node_states]
    try:
        squeue = _kubectl_exec_login(
            command_runner=command_runner,
            kube_context=kube_context,
            args=("squeue", "-h", "-o", "%T"),
            check=False,
            timeout_seconds=90,
        )
    except Exception:
        squeue = SoperatorMigrationCommandResult((), 1, "", "squeue status failed")
    queue_summary = "queue unavailable"
    queue_state = "degraded"
    if squeue.returncode == 0:
        jobs = [line.strip().lower() for line in squeue.stdout.splitlines() if line.strip()]
        queue_summary = "queue empty" if not jobs else "jobs " + _state_counts(jobs)
        queue_state = "serving"
    if not states:
        node_state = "degraded"
        node_summary = "no worker nodes returned by sinfo"
    else:
        unavailable = {
            "down",
            "fail",
            "failing",
            "unknown",
            "invalid",
            "future",
        }
        draining = {"drained", "draining"}
        problem_workers = _slurm_problem_worker_details(node_states)
        if any(item in unavailable for item in states):
            node_state = "degraded"
        elif any(item in draining for item in states):
            node_state = "draining"
        else:
            node_state = "serving"
        node_summary = "workers " + _state_counts(states)
        if problem_workers:
            node_summary += "; problem workers " + _format_problem_node_details(problem_workers)
    state = node_state
    if _STATUS_STATE_RANK[queue_state] > _STATUS_STATE_RANK[state]:
        state = queue_state
    return SoperatorMigrationStatusSignal("Slurm Workers", state, f"{node_summary}; {queue_summary}")


def _collect_soperator_status(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
) -> SoperatorMigrationStatusSignal:
    try:
        result = command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "get",
                "slurmclusters",
                "-o",
                "json",
                "--request-timeout=20s",
            ],
            timeout_seconds=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return SoperatorMigrationStatusSignal(
            "Soperator",
            "unknown",
            "SlurmCluster status timed out",
        )
    if result.returncode != 0:
        return SoperatorMigrationStatusSignal(
            "Soperator",
            "unknown",
            "SlurmCluster status unavailable: " + _command_detail(result),
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return SoperatorMigrationStatusSignal(
            "Soperator",
            "unknown",
            "SlurmCluster status returned invalid JSON",
        )
    clusters = []
    target_phase = ""
    for item in _sequence_of_mappings(
        payload.get("items") if isinstance(payload, Mapping) else None
    ):
        metadata = _mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        if not name:
            continue
        phase = str(_mapping(item.get("status")).get("phase", "") or "present").strip()
        clusters.append(f"{name}={phase}")
        if name == target_ref:
            target_phase = phase
    if not clusters:
        return SoperatorMigrationStatusSignal(
            "Soperator", "unknown", "no SlurmCluster resources visible"
        )
    state = "serving"
    if target_phase and target_phase != "Available":
        state = "degraded"
    return SoperatorMigrationStatusSignal("Soperator", state, ", ".join(clusters[:4]))


def _status_scope_for_phase(phase_id: str, phase_ids: Sequence[str]) -> Mapping[str, bool]:
    storage_planned = any(
        phase in phase_ids for phase in ("create-aligned-sfs", "online-bulk-data-sync")
    )
    compute_planned = "rolling-compute-migration" in phase_ids
    storage = phase_id in _STATUS_PHASES_WITH_STORAGE and storage_planned
    compute = phase_id in _STATUS_PHASES_WITH_COMPUTE and compute_planned
    mk8s_only = phase_id in _STATUS_PHASES_WITH_MK8S_ONLY
    continuity = storage or compute
    return {
        "storage": storage,
        "mk8s": continuity or mk8s_only,
        "slurm": continuity,
        "soperator": continuity,
    }


def _overall_status_state(signals: Sequence[SoperatorMigrationStatusSignal]) -> str:
    if not signals:
        return "unknown"
    return max(signals, key=lambda item: _STATUS_STATE_RANK.get(item.state, 3)).state


def _status_phase_label(phase_id: str) -> str:
    return _STATUS_PHASE_LABELS.get(phase_id, phase_id.replace("-", " ").title())


class SoperatorMigrationStatusReporter:
    def __init__(
        self,
        *,
        emit: Callable[[str], None] | None,
        command_runner: SoperatorMigrationCommandRunner,
        kube_context: str,
        checkpoint: Mapping[str, Any],
        payload: Mapping[str, Any],
        source_report: Mapping[str, Any],
        target_ref: str,
        phase_ids: Sequence[str],
        poll_interval_seconds: float = 30.0,
        repeat_interval_seconds: float = 60.0,
    ) -> None:
        self._emit = emit
        self._command_runner = command_runner
        self._kube_context = kube_context
        self._checkpoint = checkpoint
        self._payload = payload
        self._source_report = source_report
        self._target_ref = target_ref
        self._phase_ids = tuple(phase_ids)
        self._poll_interval_seconds = max(0.0, poll_interval_seconds)
        self._repeat_interval_seconds = max(0.0, repeat_interval_seconds)
        self._started_at = time.monotonic()
        self._phase_id = ""
        self._last_message = ""
        self._last_emit_at = 0.0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._emit is None or self._poll_interval_seconds <= 0:
            return
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="soperator-migration-status", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=5.0)
        self._thread = None

    def set_phase(self, phase_id: str) -> SoperatorMigrationStatusSnapshot | None:
        with self._lock:
            self._phase_id = phase_id
        return self.emit(force=True)

    def emit(self, *, force: bool = False) -> SoperatorMigrationStatusSnapshot | None:
        if self._emit is None:
            return None
        snapshot = self.snapshot()
        if snapshot is None:
            return None
        now = time.monotonic()
        with self._lock:
            if (
                not force
                and snapshot.summary == self._last_message
                and (now - self._last_emit_at) < self._repeat_interval_seconds
            ):
                return snapshot
            self._last_message = snapshot.summary
            self._last_emit_at = now
        with suppress(Exception):
            self._emit(snapshot.summary)
        return snapshot

    def snapshot(self) -> SoperatorMigrationStatusSnapshot | None:
        with self._lock:
            phase_id = self._phase_id
        if not phase_id:
            return None
        scope = _status_scope_for_phase(phase_id, self._phase_ids)
        signals: list[SoperatorMigrationStatusSignal] = []
        try:
            if scope.get("storage"):
                signals.append(
                    _collect_storage_status(
                        command_runner=self._command_runner,
                        kube_context=self._kube_context,
                        checkpoint=self._checkpoint,
                        payload=self._payload,
                        source_report=self._source_report,
                        target_ref=self._target_ref,
                    )
                )
            if scope.get("mk8s"):
                signals.append(
                    _collect_mk8s_status(
                        command_runner=self._command_runner,
                        kube_context=self._kube_context,
                        checkpoint=self._checkpoint,
                        phase_id=phase_id,
                    )
                )
            if scope.get("slurm"):
                signals.append(
                    _collect_slurm_status(
                        command_runner=self._command_runner,
                        kube_context=self._kube_context,
                    )
                )
            if scope.get("soperator"):
                signals.append(
                    _collect_soperator_status(
                        command_runner=self._command_runner,
                        kube_context=self._kube_context,
                        target_ref=self._target_ref,
                    )
                )
        except Exception as exc:
            signals.append(
                SoperatorMigrationStatusSignal(
                    "Status",
                    "unknown",
                    f"status collection failed: {exc}",
                )
            )
        state = _overall_status_state(signals)
        elapsed = _format_status_elapsed(time.monotonic() - self._started_at)
        signal_text = " | ".join(
            f"{signal.name} {signal.state}: {signal.summary}" for signal in signals
        )
        if not signal_text:
            signal_text = "no phase-specific status checks are planned"
        summary = (
            f"Soperator migration status [{elapsed}] phase {phase_id} "
            f"[{_status_phase_label(phase_id)}] ({state}): {signal_text}"
        )
        return SoperatorMigrationStatusSnapshot(
            phase=phase_id,
            elapsed=elapsed,
            state=state,
            signals=tuple(signals),
            summary=summary,
        )

    def _run(self) -> None:
        while not self._stop_event.wait(self._poll_interval_seconds):
            with suppress(Exception):
                self.emit()


def _status_snapshot_event(snapshot: SoperatorMigrationStatusSnapshot | None) -> Mapping[str, Any]:
    if snapshot is None:
        return {}
    return {
        "phase": snapshot.phase,
        "elapsed": snapshot.elapsed,
        "state": snapshot.state,
        "summary": snapshot.summary,
        "signals": [
            {"name": item.name, "state": item.state, "summary": item.summary}
            for item in snapshot.signals
        ],
    }


def _ensure_slurm_quiet(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    allow_missing_login_recovery: bool = False,
) -> list[str]:
    result = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("squeue", "-h"),
        check=False,
        timeout_seconds=120,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "login pod not found"
        if allow_missing_login_recovery and "login pod not found" in detail.lower():
            return [
                "Slurm quiet window check skipped for partial cutover recovery: "
                "no live SlurmCluster/login pod remains to inspect before target "
                "chart reconciliation."
            ]
        raise SoperatorMigrationPhasePending(
            "rolling-compute-migration could not inspect Slurm jobs from a login pod: "
            + detail
            + ". Ensure the source Slurm data plane is healthy before executing."
        )
    queued = result.stdout.strip()
    if queued:
        raise SoperatorMigrationPhasePending(
            "rolling-compute-migration requires an empty Slurm queue before compute cutover. "
            "Running or pending jobs were returned by `squeue -h`."
        )
    drain = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("scontrol", "update", "PartitionName=ALL", "State=DRAIN"),
        check=False,
        timeout_seconds=120,
    )
    if drain.returncode != 0:
        return [
            "Slurm quiet window verified; partition drain command was not supported by the source release."
        ]
    return ["Slurm quiet window verified: no jobs in queue and partitions set to DRAIN."]


def _uncordon_or_drain_nodes(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    nodes: Sequence[str],
    action: str,
) -> None:
    for node in nodes:
        if action == "cordon":
            _run_kubectl_node_action(
                command_runner=command_runner,
                node=node,
                args=["kubectl", "--context", kube_context, "cordon", node],
                timeout_seconds=300,
            )
        elif action == "drain":
            _run_kubectl_node_action(
                command_runner=command_runner,
                node=node,
                args=[
                    "kubectl",
                    "--context",
                    kube_context,
                    "drain",
                    node,
                    "--ignore-daemonsets",
                    "--delete-emptydir-data",
                    "--timeout=20m",
                ],
                timeout_seconds=1500,
            )
        elif action == "uncordon":
            _run_kubectl_node_action(
                command_runner=command_runner,
                node=node,
                args=["kubectl", "--context", kube_context, "uncordon", node],
                timeout_seconds=300,
            )


def _run_kubectl_node_action(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    node: str,
    args: Sequence[str],
    timeout_seconds: int,
) -> None:
    result = command_runner(args, timeout_seconds=timeout_seconds, check=False)
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout or "").lower()
    if "notfound" in detail or "not found" in detail:
        return
    raise RuntimeError(
        f"{_command_text(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
    )


def _kubectl_get_namespace_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource: str,
) -> tuple[bool, Mapping[str, Any]]:
    result = command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "get",
            resource,
            "-o",
            "json",
        ],
        timeout_seconds=120,
        check=False,
    )
    if result.returncode != 0:
        detail = f"{result.stderr}\n{result.stdout}".lower()
        if "notfound" in detail or "not found" in detail:
            return False, {}
        raise RuntimeError(
            f"{_command_text(result.args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_command_text(result.args)} returned invalid JSON: {exc}") from exc
    return True, parsed if isinstance(parsed, Mapping) else {}


def _kubectl_scale_namespace_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource: str,
    replicas: int,
) -> None:
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "scale",
            resource,
            "--replicas",
            str(replicas),
        ],
        timeout_seconds=300,
    )


def _kubectl_patch_namespace_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource: str,
    patch: Mapping[str, Any],
) -> None:
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "patch",
            resource,
            "--type",
            "merge",
            "-p",
            json.dumps(to_plain_data(patch), sort_keys=True),
        ],
        timeout_seconds=300,
    )


def _kubectl_delete_namespace_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource: str,
) -> None:
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "delete",
            resource,
            "--ignore-not-found=true",
            "--timeout=5m",
        ],
        timeout_seconds=360,
        check=False,
    )


def _resource_replicas(payload: Mapping[str, Any], *, default: int = 1) -> int:
    spec = _mapping(payload.get("spec"))
    return _positive_int(spec.get("replicas"), fallback=default)


def _mariadb_suspend_value(payload: Mapping[str, Any]) -> bool:
    spec = _mapping(payload.get("spec"))
    return _bool_value(spec.get("suspend"), fallback=False)


def _quiesce_scale_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource: str,
) -> dict[str, Any]:
    exists, payload = _kubectl_get_namespace_resource(
        command_runner=command_runner,
        kube_context=kube_context,
        resource=resource,
    )
    item = {
        "action": "scale",
        "resource": resource,
        "exists": exists,
        "replicas": _resource_replicas(payload),
    }
    if exists:
        _kubectl_scale_namespace_resource(
            command_runner=command_runner,
            kube_context=kube_context,
            resource=resource,
            replicas=0,
        )
    return item


def _quiesce_mariadb_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource: str,
) -> dict[str, Any]:
    exists, payload = _kubectl_get_namespace_resource(
        command_runner=command_runner,
        kube_context=kube_context,
        resource=resource,
    )
    item = {
        "action": "mariadb-suspend",
        "resource": resource,
        "exists": exists,
        "suspend": _mariadb_suspend_value(payload),
    }
    if exists:
        _kubectl_patch_namespace_resource(
            command_runner=command_runner,
            kube_context=kube_context,
            resource=resource,
            patch={"spec": {"suspend": True}},
        )
    return item


def _external_service_role_quiesce_resources(role: str) -> tuple[tuple[str, str], ...]:
    if role == "controller":
        return (("scale", "statefulsets.apps.kruise.io/controller"),)
    if role == "login":
        return (("scale", "statefulsets.apps.kruise.io/login"),)
    if role == "accounting":
        return (
            ("scale", "deployment/accounting"),
            ("mariadb-suspend", "mariadb.k8s.mariadb.com/soperator-acct-db"),
            ("scale", "statefulset.apps/soperator-acct-db"),
            ("delete", "pod/soperator-acct-db-0"),
        )
    return ()


def _quiesce_external_service_role(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    role: str,
    state: dict[str, Any],
) -> list[str]:
    if state.get("status") == "restored":
        state.clear()
    if state.get("status") == "quiesced":
        return []
    resources: list[dict[str, Any]] = []
    for action, resource in _external_service_role_quiesce_resources(role):
        if action == "scale":
            resources.append(
                _quiesce_scale_resource(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    resource=resource,
                )
            )
        elif action == "mariadb-suspend":
            resources.append(
                _quiesce_mariadb_resource(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    resource=resource,
                )
            )
        elif action == "delete":
            _kubectl_delete_namespace_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource=resource,
            )
            resources.append({"action": "delete", "resource": resource, "exists": True})
    if not resources:
        return []
    state.update(
        {
            "role": role,
            "resources": resources,
            "status": "quiesced",
            "quiesced_at": state.get("quiesced_at") or _utc_now(),
        }
    )
    return [f"Quiesced Soperator {role} workloads before node-template rollout."]


def _restore_external_service_role(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    state: dict[str, Any],
) -> list[str]:
    if state.get("status") != "quiesced":
        return []
    role = str(state.get("role", "") or "").strip()
    for item in reversed(_sequence_of_mappings(state.get("resources"))):
        if not _bool_value(item.get("exists"), fallback=False):
            continue
        resource = str(item.get("resource", "") or "").strip()
        if not resource:
            continue
        action = str(item.get("action", "") or "").strip()
        if action == "scale":
            _kubectl_scale_namespace_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource=resource,
                replicas=_positive_int(item.get("replicas"), fallback=1),
            )
        elif action == "mariadb-suspend":
            _kubectl_patch_namespace_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource=resource,
                patch={"spec": {"suspend": _bool_value(item.get("suspend"), fallback=False)}},
            )
    state["status"] = "restored"
    state["restored_at"] = _utc_now()
    return [f"Restored Soperator {role} workloads after node-template rollout."] if role else []


def _ensure_soperator_chart_dependencies(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    chart_path: Path,
) -> None:
    if not (chart_path / "Chart.yaml").exists():
        raise SoperatorMigrationPhasePending(
            f"target Soperator chart path does not exist: {chart_path}. "
            "Set NEBIUS_CXCLI_SOPERATOR_CHART_PATH or run from the repository checkout."
        )
    command_runner(
        ["helm", "dependency", "build", str(chart_path)],
        timeout_seconds=600,
    )


def _apply_soperator_crds(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    chart_path: Path,
) -> None:
    crd_dir = chart_path / "crds"
    if not crd_dir.exists():
        return
    for crd_file in sorted(crd_dir.glob("*.yaml")):
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "apply",
                "--server-side",
                "--force-conflicts",
                "-f",
                str(crd_file),
            ],
            timeout_seconds=1200,
        )


def _target_role_mapping_values(
    worker_node_groups: Sequence[str],
    *,
    service_node_groups: Mapping[str, str] | None = None,
) -> Mapping[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for role in _SOPERATOR_SERVICE_ROLES:
        group_name = normalize_component_token(_mapping(service_node_groups or {}).get(role))
        mapping[role] = [group_name or role]
    preserved_workers = [
        normalize_component_token(group)
        for group in worker_node_groups
        if normalize_component_token(group)
    ]
    mapping["worker"] = list(dict.fromkeys(preserved_workers)) or ["worker"]
    return mapping


def _role_match_expression(
    role: str, *, label_key: str = "slurm.nebius.ai/nodeset-name"
) -> dict[str, Any]:
    return {
        "key": label_key,
        "operator": "In",
        "values": [role],
    }


def _role_node_selector_terms(role: str) -> list[dict[str, Any]]:
    return [
        {"matchExpressions": [_role_match_expression(role, label_key=label_key)]}
        for label_key in _SOPERATOR_NODESET_LABEL_KEYS
    ]


def _role_affinity(role: str) -> dict[str, Any]:
    return {
        "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": _role_node_selector_terms(role)
            }
        }
    }


def _role_toleration(
    role: str, *, label_key: str = "slurm.nebius.ai/nodeset-name"
) -> dict[str, str]:
    return {
        "key": label_key,
        "operator": "Equal",
        "value": role,
        "effect": "NoSchedule",
    }


def _role_tolerations(role: str) -> list[dict[str, str]]:
    return [
        _role_toleration(role, label_key=label_key) for label_key in _SOPERATOR_NODESET_LABEL_KEYS
    ]


def _target_k8s_node_filters() -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = [
        {
            "name": "no-gpu",
            "affinity": _role_affinity("system"),
        }
    ]
    for role in _SOPERATOR_SERVICE_ROLES:
        item: dict[str, Any] = {
            "name": role,
            "affinity": _role_affinity(role),
        }
        if role != "system":
            item["tolerations"] = _role_tolerations(role)
        filters.append(item)
    return filters


def _patch_target_operator_affinity(values: dict[str, Any]) -> None:
    system_affinity = _role_affinity("system")
    controller_manager = values.setdefault("controllerManager", {})
    if isinstance(controller_manager, dict):
        controller_manager["affinity"] = copy.deepcopy(system_affinity)
    mariadb_operator = values.setdefault("mariadb-operator", {})
    if isinstance(mariadb_operator, dict):
        mariadb_operator["affinity"] = copy.deepcopy(system_affinity)
        webhook = mariadb_operator.setdefault("webhook", {})
        if isinstance(webhook, dict):
            webhook["affinity"] = copy.deepcopy(system_affinity)
    soperator_checks = values.setdefault("soperator-checks", {})
    if isinstance(soperator_checks, dict):
        checks = soperator_checks.setdefault("checks", {})
        if isinstance(checks, dict):
            checks["affinity"] = copy.deepcopy(system_affinity)


def _patch_target_values_for_compute(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    checkpoint: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    values = dict(copy.deepcopy(to_plain_data(_target_soperator_values(payload, target_ref))))
    values.setdefault("nameOverride", "helm-soperator")
    _patch_target_slurm_runtime(values)
    _preserve_live_storage_sizes(values, live_snapshot=live_snapshot)
    rolling_state = _mapping(
        _mapping(checkpoint.get("phase_state")).get("rolling-compute-migration")
    )
    in_place_worker_groups = tuple(
        str(group or "").strip()
        for group in rolling_state.get("in_place_worker_node_groups", []) or []
        if str(group or "").strip()
    )
    target_groups = _mapping(rolling_state.get("target_node_groups"))
    service_node_groups = {
        role: str(_mapping(target_groups.get(role)).get("name", "") or "").strip()
        for role in _SOPERATOR_SERVICE_ROLES
    }
    values["nodeGroupMapping"] = _target_role_mapping_values(
        in_place_worker_groups,
        service_node_groups=service_node_groups,
    )
    values["k8sNodeFilters"] = _target_k8s_node_filters()
    _patch_target_operator_affinity(values)
    _patch_storage_mount_tolerations(values)
    if _has_live_storage_pvs(live_snapshot):
        _preserve_live_storage_pv_affinity(values, live_snapshot=live_snapshot)
    else:
        _patch_storage_role_affinity(values)
    _patch_accounting_mariadb_storage(values, live_snapshot=live_snapshot)
    worker_count = _positive_int(rolling_state.get("in_place_worker_node_count"), fallback=0)
    topology_by_nodeset = _mapping(rolling_state.get("worker_topology_by_nodeset"))
    source_worker_nodesets = _source_worker_nodeset_values(
        source_report,
        live_snapshot=live_snapshot,
        topology_by_nodeset=topology_by_nodeset,
    )
    if source_worker_nodesets:
        values["nodesets"] = source_worker_nodesets
        partition_config = _source_worker_partition_configuration(
            source_report,
            worker_names=tuple(
                str(item.get("name", "") or "").strip()
                for item in source_worker_nodesets
                if str(item.get("name", "") or "").strip()
            ),
        )
        if partition_config:
            values["partitionConfiguration"] = partition_config
    else:
        _patch_legacy_worker_nodeset_fallback(values, worker_count=worker_count)
    return values


def _source_worker_nodeset_values(
    source_report: Mapping[str, Any],
    *,
    live_snapshot: Mapping[str, Any] | None = None,
    topology_by_nodeset: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    source_snapshot, _report = _source_report_payload(source_report)
    source_groups_by_nodeset = _source_worker_node_groups_by_nodeset(source_report)
    result: list[dict[str, Any]] = []
    for resource in _sequence_of_mappings(source_snapshot.get("soperator_resources")):
        if str(resource.get("kind", "") or "").strip().lower() != "nodeset":
            continue
        metadata = _mapping(resource.get("metadata"))
        name = normalize_component_token(metadata.get("name"))
        if not name or not name.startswith(_SOURCE_WORKER_NODESET_PREFIX):
            continue
        spec = _mapping(resource.get("spec"))
        if not spec:
            continue
        item = dict(copy.deepcopy(to_plain_data(spec)))
        item["name"] = name
        _normalize_source_worker_nodeset_value(
            item,
            source_group=source_groups_by_nodeset.get(name),
            live_snapshot=live_snapshot,
            topology=_mapping((topology_by_nodeset or {}).get(name)),
        )
        result.append(item)
    partition_config = _source_worker_partition_configuration(
        source_report,
        worker_names=tuple(str(item.get("name", "") or "").strip() for item in result),
    )
    referenced_workers = _partition_worker_nodeset_refs(partition_config)
    if referenced_workers:
        result = [
            item
            for item in result
            if normalize_component_token(item.get("name")) in referenced_workers
        ]
    result.sort(key=lambda item: str(item.get("name", "") or ""))
    return result


def _source_worker_node_groups_by_nodeset(
    source_report: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    inventory = _source_node_group_inventory(source_report)
    groups: dict[str, Mapping[str, Any]] = {}
    for raw_group_name, group in inventory.items():
        group_name = normalize_component_token(raw_group_name)
        if not group_name or not isinstance(group, Mapping):
            continue
        if not _source_group_is_worker(group_name, group):
            continue
        nodeset_name = normalize_component_token(_source_group_nodeset(group))
        if nodeset_name:
            groups.setdefault(nodeset_name, group)
    return groups


def _nodeset_slurmd_resources(nodeset: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(_mapping(nodeset.get("slurmd")).get("resources"))


def _nodeset_gpu_count(
    nodeset: Mapping[str, Any], source_group: Mapping[str, Any] | None
) -> int | None:
    resources = _nodeset_slurmd_resources(nodeset)
    for value in (
        resources.get("gpu"),
        resources.get("nvidia.com/gpu"),
        _mapping((source_group or {}).get("allocatable")).get("nvidia.com/gpu"),
    ):
        parsed = _positive_int(value, fallback=0)
        if parsed:
            return parsed
    return None


def _snapshot_nodes_by_name(snapshot: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not snapshot:
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for node in _sequence_of_mappings(snapshot.get("nodes")):
        name = str(_mapping(node.get("metadata")).get("name", "") or "").strip()
        if name:
            result[name] = node
    return result


def _node_capacity_cpu_count(node: Mapping[str, Any]) -> int | None:
    capacity = _mapping(_mapping(node.get("status")).get("capacity"))
    return _ceil_cpu_quantity(capacity.get("cpu"))


def _source_group_cpu_count(
    source_group: Mapping[str, Any] | None,
    *,
    live_snapshot: Mapping[str, Any] | None = None,
) -> int | None:
    if not source_group:
        return None
    nodes_by_name = _snapshot_nodes_by_name(live_snapshot)
    for node_name in _sequence_of_scalars(source_group.get("nodes")):
        node = nodes_by_name.get(str(node_name))
        if not node:
            continue
        parsed = _node_capacity_cpu_count(node)
        if parsed:
            return parsed
    capacity = _mapping(source_group.get("capacity"))
    parsed = _ceil_cpu_quantity(capacity.get("cpu"))
    if parsed:
        return parsed
    allocatable = _mapping(source_group.get("allocatable"))
    parsed = _ceil_cpu_quantity(allocatable.get("cpu"))
    if parsed:
        return parsed
    preset = str(_mapping(source_group.get("labels")).get("nebius.com/resource-preset", "") or "")
    match = re.search(r"(?P<vcpu>[0-9]+)\s*vcpu", preset, flags=re.IGNORECASE)
    if match:
        return _positive_int(match.group("vcpu"), fallback=0) or None
    return None


def _nodeset_cpu_count(nodeset: Mapping[str, Any]) -> int | None:
    return _ceil_cpu_quantity(_nodeset_slurmd_resources(nodeset).get("cpu"))


def _source_worker_nodeset_names(source_report: Mapping[str, Any]) -> tuple[str, ...]:
    source_snapshot, _report = _source_report_payload(source_report)
    names: list[str] = []
    for resource in _sequence_of_mappings(source_snapshot.get("soperator_resources")):
        if str(resource.get("kind", "") or "").strip().lower() != "nodeset":
            continue
        name = normalize_component_token(_mapping(resource.get("metadata")).get("name"))
        if name and name.startswith(_SOURCE_WORKER_NODESET_PREFIX):
            names.append(name)
    return tuple(dict.fromkeys(names))


_SLURM_STATIC_NORMALIZED_KEYS = frozenset(
    {
        "boards",
        "corespersocket",
        "cpus",
        "gres",
        "port",
        "socketsperboard",
        "threadspercore",
    }
)
_TARGET_NODESET_RESERVED_CUSTOM_VOLUME_MOUNT_NAMES = frozenset(
    {
        "slurm-scripts",
        "slurm-scripts-jail",
    }
)


def _static_without_keys(static: str, keys: set[str] | frozenset[str]) -> list[str]:
    tokens: list[str] = []
    for token in static.split():
        key = token.split("=", 1)[0].strip().lower()
        if key in keys:
            continue
        tokens.append(token)
    return tokens


def _static_without_normalized_keys(static: str) -> list[str]:
    return _static_without_keys(static, _SLURM_STATIC_NORMALIZED_KEYS)


def _worker_topology_static_tokens(topology: Mapping[str, Any]) -> list[str]:
    sockets = _positive_int(topology.get("sockets"), fallback=0)
    cores_per_socket = _positive_int(topology.get("cores_per_socket"), fallback=0)
    threads_per_core = _positive_int(topology.get("threads_per_core"), fallback=0)
    if not sockets or not cores_per_socket or not threads_per_core:
        return []
    boards = _positive_int(topology.get("boards"), fallback=1)
    return [
        f"Boards={boards}",
        f"SocketsPerBoard={sockets}",
        f"CoresPerSocket={cores_per_socket}",
        f"ThreadsPerCore={threads_per_core}",
    ]


def _gpu_worker_static(
    cpu_count: int,
    gpu_count: int,
    existing_static: str,
    *,
    topology: Mapping[str, Any] | None = None,
) -> str:
    topology = _mapping(topology)
    topology_cpu_count = _positive_int(topology.get("cpus"), fallback=0)
    tokens = [
        f"CPUs={topology_cpu_count or cpu_count}",
        *_worker_topology_static_tokens(topology),
        f"Gres=gpu:{gpu_count}",
        *_static_without_normalized_keys(existing_static),
    ]
    return " ".join(dict.fromkeys(token for token in tokens if token))


def _normalize_source_worker_node_config(
    nodeset: dict[str, Any],
    *,
    source_group: Mapping[str, Any] | None,
    live_snapshot: Mapping[str, Any] | None,
    topology: Mapping[str, Any] | None,
) -> None:
    node_config = nodeset.setdefault("nodeConfig", {})
    if not isinstance(node_config, dict):
        node_config = {}
        nodeset["nodeConfig"] = node_config
    existing_static = str(node_config.get("static", "") or "").strip()
    gpu_count = _nodeset_gpu_count(nodeset, source_group)
    if gpu_count:
        cpu_count = _source_group_cpu_count(
            source_group,
            live_snapshot=live_snapshot,
        ) or _nodeset_cpu_count(nodeset)
        if cpu_count:
            node_config["static"] = _gpu_worker_static(
                cpu_count,
                gpu_count,
                existing_static,
                topology=topology,
            )
            return
    stripped = " ".join(_static_without_keys(existing_static, {"port"}))
    if stripped:
        node_config["static"] = stripped
    elif "static" in node_config:
        node_config.pop("static", None)


def _strip_reserved_nodeset_custom_volume_mounts(nodeset: dict[str, Any]) -> None:
    volumes = _mapping(_mapping(nodeset.get("slurmd")).get("volumes"))
    mounts = volumes.get("customVolumeMounts")
    if not isinstance(mounts, Sequence) or isinstance(mounts, (str, bytes, bytearray)):
        return
    kept: list[Any] = []
    changed = False
    for mount in mounts:
        name = str(_mapping(mount).get("name", "") or "").strip()
        if name in _TARGET_NODESET_RESERVED_CUSTOM_VOLUME_MOUNT_NAMES:
            changed = True
            continue
        kept.append(mount)
    if not changed:
        return
    if kept:
        volumes["customVolumeMounts"] = kept
    else:
        volumes.pop("customVolumeMounts", None)


def _default_worker_security_proc_mount(nodeset: dict[str, Any]) -> None:
    for component in ("slurmd", "munge"):
        raw_component = nodeset.get(component)
        if not isinstance(raw_component, dict):
            continue
        security = raw_component.setdefault("security", {})
        if not isinstance(security, dict):
            security = {}
            raw_component["security"] = security
        if not str(security.get("procMount", "") or "").strip():
            security["procMount"] = "Default"


def _lscpu_field_map(payload: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    entries = payload.get("lscpu")
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
        return result
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        field = str(item.get("field", "") or "").strip().rstrip(":").lower()
        data = str(item.get("data", "") or "").strip()
        if field and data:
            result[field] = data
    return result


def _parse_lscpu_topology(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    fields = _lscpu_field_map(payload)
    topology = {
        "cpus": _positive_int(fields.get("cpu(s)"), fallback=0),
        "boards": 1,
        "sockets": _positive_int(fields.get("socket(s)"), fallback=0),
        "cores_per_socket": _positive_int(fields.get("core(s) per socket"), fallback=0),
        "threads_per_core": _positive_int(fields.get("thread(s) per core"), fallback=0),
    }
    if not all(
        _positive_int(topology.get(key), fallback=0)
        for key in ("cpus", "sockets", "cores_per_socket", "threads_per_core")
    ):
        return {}
    return topology


def _running_worker_pod_for_nodeset(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    nodeset_name: str,
) -> str:
    names: list[str] = []
    seen: set[str] = set()
    for label_key in _SOPERATOR_NODESET_LABEL_KEYS:
        try:
            pods = _json_from_command(
                command_runner,
                [
                    "kubectl",
                    "--context",
                    kube_context,
                    "-n",
                    _SOPERATOR_NAMESPACE,
                    "get",
                    "pods",
                    "-l",
                    f"{label_key}={nodeset_name}",
                    "-o",
                    "json",
                    "--request-timeout=20s",
                ],
                timeout_seconds=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            continue
        for pod in _sequence_of_mappings(pods.get("items")):
            metadata = _mapping(pod.get("metadata"))
            labels = _mapping(metadata.get("labels"))
            label_nodeset = any(
                str(labels.get(candidate_key, "") or "").strip() == nodeset_name
                for candidate_key in _SOPERATOR_NODESET_LABEL_KEYS
            )
            if not label_nodeset:
                continue
            if str(_mapping(pod.get("status")).get("phase", "") or "") != "Running":
                continue
            name = str(metadata.get("name", "") or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return sorted(names)[0] if names else ""


def _discover_worker_nodeset_topology(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    nodeset_name: str,
) -> dict[str, Any]:
    pod_name = _running_worker_pod_for_nodeset(
        command_runner=command_runner,
        kube_context=kube_context,
        nodeset_name=nodeset_name,
    )
    if not pod_name:
        return {}
    result = command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "exec",
            pod_name,
            "-c",
            "slurmd",
            "--",
            "lscpu",
            "-J",
        ],
        timeout_seconds=120,
        check=False,
    )
    if result.returncode != 0:
        return {}
    topology = _parse_lscpu_topology(result.stdout)
    if topology:
        topology["source_pod"] = pod_name
    return topology


def _ensure_worker_nodeset_topology_checkpoint(
    *,
    checkpoint: dict[str, Any],
    source_report: Mapping[str, Any],
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> list[str]:
    phase = _phase_state(checkpoint, "rolling-compute-migration")
    raw_topology = phase.get("worker_topology_by_nodeset")
    topology_by_nodeset: dict[str, Any] = (
        dict(raw_topology) if isinstance(raw_topology, Mapping) else {}
    )
    lines: list[str] = []
    for nodeset_name in _source_worker_nodeset_names(source_report):
        if nodeset_name in topology_by_nodeset:
            continue
        topology = _discover_worker_nodeset_topology(
            command_runner=command_runner,
            kube_context=kube_context,
            nodeset_name=nodeset_name,
        )
        if not topology:
            continue
        topology_by_nodeset[nodeset_name] = topology
        lines.append(
            "Discovered worker topology for "
            f"{nodeset_name}: CPUs={topology['cpus']}, "
            f"sockets={topology['sockets']}, "
            f"cores/socket={topology['cores_per_socket']}, "
            f"threads/core={topology['threads_per_core']}."
        )
    if topology_by_nodeset:
        phase["worker_topology_by_nodeset"] = topology_by_nodeset
    return lines


def _normalize_source_worker_nodeset_value(
    nodeset: dict[str, Any],
    *,
    source_group: Mapping[str, Any] | None = None,
    live_snapshot: Mapping[str, Any] | None = None,
    topology: Mapping[str, Any] | None = None,
) -> None:
    _strip_nodeset_image_override(nodeset, "slurmd")
    _strip_nodeset_image_override(nodeset, "munge")
    _strip_nodeset_image_override(nodeset, "sssd")
    slurmd = nodeset.get("slurmd")
    if isinstance(slurmd, dict):
        resources = slurmd.get("resources")
        if isinstance(resources, dict):
            if "ephemeral-storage" in resources and "ephemeralStorage" not in resources:
                resources["ephemeralStorage"] = resources.pop("ephemeral-storage")
            gpu_count = _positive_int(
                resources.get("gpu", resources.get("nvidia.com/gpu")),
                fallback=0,
            )
            if gpu_count:
                resources["gpu"] = gpu_count
                resources.pop("nvidia.com/gpu", None)
                gpu = nodeset.setdefault("gpu", {})
                if isinstance(gpu, dict):
                    gpu["enabled"] = True
                    nvidia = gpu.setdefault("nvidia", {})
                    if isinstance(nvidia, dict):
                        nvidia.setdefault("gdrCopyEnabled", True)
    _normalize_source_worker_node_config(
        nodeset,
        source_group=source_group,
        live_snapshot=live_snapshot,
        topology=topology,
    )
    _strip_reserved_nodeset_custom_volume_mounts(nodeset)
    _default_worker_security_proc_mount(nodeset)
    munge = nodeset.get("munge")
    if isinstance(munge, dict):
        resources = munge.get("resources")
        if (
            isinstance(resources, dict)
            and "ephemeral-storage" in resources
            and "ephemeralStorage" not in resources
        ):
            resources["ephemeralStorage"] = resources.pop("ephemeral-storage")


def _source_worker_partition_configuration(
    source_report: Mapping[str, Any],
    *,
    worker_names: Sequence[str],
) -> dict[str, Any]:
    worker_name_set = {
        normalize_component_token(name) for name in worker_names if normalize_component_token(name)
    }
    if not worker_name_set:
        return {}
    source_snapshot, _report = _source_report_payload(source_report)
    ready_nodesets = {
        normalize_component_token(_mapping(resource.get("metadata")).get("name"))
        for resource in _sequence_of_mappings(source_snapshot.get("soperator_resources"))
        if str(resource.get("kind", "") or "").strip().lower() == "nodeset"
        if str(_mapping(resource.get("status")).get("phase", "") or "").strip().lower()
        == "ready"
    }
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for resource in _sequence_of_mappings(source_snapshot.get("soperator_resources")):
        if str(resource.get("kind", "") or "").strip().lower() != "slurmcluster":
            continue
        partition_config = _mapping(_mapping(resource.get("spec")).get("partitionConfiguration"))
        if not partition_config:
            continue
        plain = dict(copy.deepcopy(to_plain_data(partition_config)))
        partitions = plain.get("partitions")
        if not isinstance(partitions, Sequence) or isinstance(partitions, (str, bytes, bytearray)):
            continue
        referenced_workers = _partition_worker_nodeset_refs(plain)
        if not referenced_workers or not referenced_workers <= worker_name_set:
            continue
        status = _mapping(resource.get("status"))
        phase = str(status.get("phase", "") or "").strip().lower()
        score = len(referenced_workers & ready_nodesets)
        if phase == "available":
            score += 100
        elif phase == "pending":
            score -= 10
        for ready_key in ("readyLogin", "readySConfigController"):
            if _positive_int(status.get(ready_key), fallback=0) > 0:
                score += 1
        created_at = str(_mapping(resource.get("metadata")).get("creationTimestamp", "") or "")
        candidates.append((score, created_at, plain))
    if not candidates:
        return {}
    candidates.sort(key=lambda item: (-item[0], item[1] or "9999"))
    return candidates[0][2]


def _partition_worker_nodeset_refs(partition_config: Mapping[str, Any]) -> set[str]:
    partitions = partition_config.get("partitions")
    if not isinstance(partitions, Sequence) or isinstance(partitions, (str, bytes, bytearray)):
        return set()
    refs: set[str] = set()
    for partition in partitions:
        if not isinstance(partition, Mapping):
            continue
        for ref in _sequence_of_scalars(_mapping(partition).get("nodeSetRefs")):
            name = normalize_component_token(ref)
            if name.startswith(_SOURCE_WORKER_NODESET_PREFIX):
                refs.add(name)
    return refs


def _sequence_of_scalars(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if not isinstance(item, Mapping))


def _patch_legacy_worker_nodeset_fallback(
    values: dict[str, Any],
    *,
    worker_count: int,
) -> None:
    nodesets = values.get("nodesets")
    if isinstance(nodesets, Sequence) and not isinstance(nodesets, (str, bytes, bytearray)):
        patched_nodesets: list[Any] = []
        for nodeset in nodesets:
            if isinstance(nodeset, Mapping) and normalize_component_token(nodeset.get("name")) in {
                "",
                "worker",
            }:
                item = dict(copy.deepcopy(to_plain_data(nodeset)))
                if worker_count:
                    item["replicas"] = worker_count
                _strip_nodeset_image_override(item, "slurmd")
                _strip_nodeset_image_override(item, "munge")
                patched_nodesets.append(item)
            else:
                patched_nodesets.append(copy.deepcopy(to_plain_data(nodeset)))
        values["nodesets"] = patched_nodesets


def _patch_target_slurm_runtime(values: dict[str, Any]) -> None:
    raw_custom = str(values.get("customSlurmConfig") or "")
    lines = [line for line in raw_custom.splitlines() if not re.match(r"^\s*PluginDir\s*=", line)]
    lines.append(f"PluginDir={_TARGET_SLURM_PLUGIN_DIR}")
    values["customSlurmConfig"] = "\n".join(line for line in lines if line.strip())

    plug_stack = values.setdefault("plugStackConfig", {})
    if not isinstance(plug_stack, dict):
        return
    pyxis = plug_stack.setdefault("pyxis", {})
    if not isinstance(pyxis, dict):
        return
    pyxis["required"] = False
    pyxis["importerPath"] = ""


def _strip_nodeset_image_override(nodeset: dict[str, Any], component: str) -> None:
    raw_component = nodeset.get(component)
    if not isinstance(raw_component, dict):
        return
    raw_component.pop("image", None)


def _patch_storage_role_affinity(values: dict[str, Any]) -> None:
    storage = values.setdefault("storage", {})
    if not isinstance(storage, dict):
        return
    for role, key in (
        ("controller", "controllerSpool"),
        ("accounting", "accounting"),
    ):
        item = storage.setdefault(key, {})
        if not isinstance(item, dict):
            continue
        item["matchExpressions"] = [_role_match_expression(role)]
        item["tolerations"] = _role_tolerations(role)
    jail = storage.setdefault("jail", {})
    if isinstance(jail, dict):
        jail["matchExpressions"] = [
            {
                "key": "slurm.nebius.ai/nodeset-name",
                "operator": "Exists",
            }
        ]
        jail["tolerations"] = _jail_storage_tolerations()


def _preserve_live_storage_pv_affinity(
    values: dict[str, Any],
    *,
    live_snapshot: Mapping[str, Any],
) -> None:
    storage = values.setdefault("storage", {})
    if not isinstance(storage, dict):
        return
    for pv_name, value_key in (
        ("jail-pv", "jail"),
        ("controller-spool-pv", "controllerSpool"),
        ("accounting-pv", "accounting"),
    ):
        match_expressions = _pv_live_match_expressions(live_snapshot, pv_name)
        if not match_expressions:
            continue
        item = storage.setdefault(value_key, {})
        if isinstance(item, dict):
            item["matchExpressions"] = match_expressions


def _pv_live_match_expressions(snapshot: Mapping[str, Any], pv_name: str) -> list[dict[str, Any]]:
    for pv in _sequence_of_mappings(snapshot.get("pvs")):
        metadata = _mapping(pv.get("metadata"))
        if str(metadata.get("name", "") or "").strip() != pv_name:
            continue
        terms = _mapping(
            _mapping(_mapping(pv.get("spec")).get("nodeAffinity")).get("required")
        ).get("nodeSelectorTerms")
        if not isinstance(terms, Sequence) or isinstance(terms, (str, bytes, bytearray)):
            return []
        if len(terms) != 1 or not isinstance(terms[0], Mapping):
            return []
        expressions = terms[0].get("matchExpressions")
        if not isinstance(expressions, Sequence) or isinstance(
            expressions, (str, bytes, bytearray)
        ):
            return []
        result: list[dict[str, Any]] = []
        for expression in expressions:
            if isinstance(expression, Mapping):
                result.append(dict(copy.deepcopy(to_plain_data(expression))))
        return result
    return []


def _patch_storage_mount_tolerations(values: dict[str, Any]) -> None:
    storage = values.setdefault("storage", {})
    if not isinstance(storage, dict):
        return
    jail = storage.setdefault("jail", {})
    if isinstance(jail, dict):
        jail["tolerations"] = _jail_storage_tolerations()
    controller_spool = storage.setdefault("controllerSpool", {})
    if isinstance(controller_spool, dict):
        controller_spool["tolerations"] = _role_tolerations("controller")
    accounting = storage.setdefault("accounting", {})
    if isinstance(accounting, dict):
        accounting["tolerations"] = _role_tolerations("accounting")


def _jail_storage_tolerations() -> list[dict[str, str]]:
    return [
        {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"},
        *[
            toleration
            for role in ("controller", "login", "accounting")
            for toleration in _role_tolerations(role)
        ],
    ]


def _has_live_storage_pvs(snapshot: Mapping[str, Any]) -> bool:
    names = {
        str(_mapping(item.get("metadata")).get("name", "") or "").strip()
        for item in _sequence_of_mappings(snapshot.get("pvs"))
    }
    return bool(names & {"jail-pv", "controller-spool-pv", "accounting-pv"})


def _patch_accounting_mariadb_storage(
    values: dict[str, Any],
    *,
    live_snapshot: Mapping[str, Any],
) -> None:
    mariadb_size, mariadb_storage_class, mariadb_access_modes = _mariadb_storage_from_snapshot(
        live_snapshot
    )
    live_size = (
        _pvc_live_size(live_snapshot, "accounting-pvc")
        or _pv_live_size(
            live_snapshot,
            "accounting-pv",
        )
        or mariadb_size
    )
    if not live_size:
        live_size = "128Gi"
    if not mariadb_storage_class:
        mariadb_storage_class = "compute-csi-default-sc"
    if not mariadb_access_modes:
        mariadb_access_modes = ["ReadWriteOnce"]
    slurm_nodes = values.setdefault("slurmNodes", {})
    if not isinstance(slurm_nodes, dict):
        return
    accounting = slurm_nodes.setdefault("accounting", {})
    if not isinstance(accounting, dict):
        return
    mariadb = accounting.setdefault("mariadbOperator", {})
    if not isinstance(mariadb, dict):
        return
    storage = mariadb.setdefault("storage", {})
    if not isinstance(storage, dict):
        return
    storage["size"] = live_size
    storage["storageClassName"] = mariadb_storage_class
    storage["volumeClaimTemplate"] = {
        "accessModes": mariadb_access_modes,
        "resources": {"requests": {"storage": live_size}},
        "storageClassName": mariadb_storage_class,
    }


def _mariadb_storage_from_snapshot(
    snapshot: Mapping[str, Any],
) -> tuple[str, str, list[str]]:
    candidates: list[tuple[int, str, str, list[str]]] = []
    for pvc in _sequence_of_mappings(snapshot.get("pvcs")):
        metadata = _mapping(pvc.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        namespace = str(metadata.get("namespace", "") or "").strip()
        if not name.startswith("storage-") or not name.endswith("-acct-db-0"):
            continue
        if namespace and namespace != _SOPERATOR_NAMESPACE:
            continue
        spec = _mapping(pvc.get("spec"))
        status = _mapping(pvc.get("status"))
        storage_class = str(spec.get("storageClassName", "") or "").strip()
        raw_access_modes = spec.get("accessModes", [])
        access_modes = (
            [str(mode).strip() for mode in raw_access_modes if str(mode).strip()]
            if isinstance(raw_access_modes, list)
            else []
        )
        request_size = str(
            _mapping(_mapping(spec.get("resources")).get("requests")).get("storage", "")
            or ""
        ).strip()
        status_size = str(_mapping(status.get("capacity")).get("storage", "") or "").strip()
        size = _larger_storage_size(request_size, status_size)
        phase = str(status.get("phase", "") or "").strip()
        priority = 0
        if storage_class == "slurm-local-pv":
            priority += 10
        if phase and phase != "Bound":
            priority += 5
        candidates.append((priority, size, storage_class, access_modes))
    if not candidates:
        return "", "", []
    _priority, size, storage_class, access_modes = sorted(candidates, key=lambda item: item[0])[0]
    return size, storage_class, access_modes


def _storage_quantity_bytes(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"(?P<number>\d+(?:\.\d+)?)(?P<suffix>[KMGTPE]i?|)", text)
    if not match:
        return None
    multiplier_by_suffix = {
        "": 1,
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "Pi": 1024**5,
        "Ei": 1024**6,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
        "P": 1000**5,
        "E": 1000**6,
    }
    multiplier = multiplier_by_suffix.get(match.group("suffix") or "")
    if multiplier is None:
        return None
    return int(float(match.group("number")) * multiplier)


def _larger_storage_size(*values: str) -> str:
    parsed: list[tuple[int, str]] = []
    fallback = ""
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if not fallback:
            fallback = text
        size_bytes = _storage_quantity_bytes(text)
        if size_bytes is not None:
            parsed.append((size_bytes, text))
    if parsed:
        return max(parsed, key=lambda item: item[0])[1]
    return fallback


def _pvc_live_size(snapshot: Mapping[str, Any], pvc_name: str) -> str:
    for pvc in _sequence_of_mappings(snapshot.get("pvcs")):
        metadata = _mapping(pvc.get("metadata"))
        if str(metadata.get("name", "") or "").strip() != pvc_name:
            continue
        status_size = str(
            _mapping(_mapping(pvc.get("status")).get("capacity")).get("storage", "") or ""
        ).strip()
        if status_size:
            return status_size
        request_size = str(
            _mapping(_mapping(_mapping(pvc.get("spec")).get("resources")).get("requests")).get(
                "storage", ""
            )
            or ""
        ).strip()
        if request_size:
            return request_size
    return ""


def _pv_live_size(snapshot: Mapping[str, Any], pv_name: str) -> str:
    for pv in _sequence_of_mappings(snapshot.get("pvs")):
        metadata = _mapping(pv.get("metadata"))
        if str(metadata.get("name", "") or "").strip() != pv_name:
            continue
        return str(
            _mapping(_mapping(pv.get("spec")).get("capacity")).get("storage", "") or ""
        ).strip()
    return ""


def _preserve_live_storage_sizes(
    values: dict[str, Any], *, live_snapshot: Mapping[str, Any]
) -> None:
    volume = values.setdefault("volume", {})
    if not isinstance(volume, dict):
        return
    for value_key, pvc_name in (
        ("jail", "jail-pvc"),
        ("controllerSpool", "controller-spool-pvc"),
        ("accounting", "accounting-pvc"),
    ):
        live_size = _pvc_live_size(live_snapshot, pvc_name)
        if not live_size:
            continue
        item = volume.setdefault(value_key, {})
        if isinstance(item, dict):
            item["size"] = live_size


def _helm_upgrade_target_app_chart(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    row: Mapping[str, Any],
) -> Mapping[str, str]:
    component_id = normalize_component_token(row.get("id"))
    chart_ref = _app_chart_ref(row)
    release_name = str(row.get("release-name", "") or row.get("release_name", "") or "").strip()
    if not release_name:
        release_name = component_id
    namespace = str(row.get("namespace", "") or "").strip()
    version = str(row.get("version", "") or "").strip()
    if not component_id:
        raise RuntimeError("Target GPU stack app row is missing id.")
    if not chart_ref:
        raise RuntimeError(f"Target GPU stack app row '{component_id}' is missing repo/chart.")
    if not release_name:
        raise RuntimeError(f"Target GPU stack app row '{component_id}' is missing release-name.")
    if not namespace:
        raise RuntimeError(f"Target GPU stack app row '{component_id}' is missing namespace.")
    command = [
        "helm",
        "--kube-context",
        kube_context,
        "upgrade",
        "--install",
        release_name,
        chart_ref,
        "-n",
        namespace,
        "--create-namespace",
        "--no-hooks",
        "-f",
        "-",
        "--wait",
        "--timeout",
        "45m",
    ]
    if version:
        command.extend(["--version", version])
    values_text = json.dumps(to_plain_data(_mapping(row.get("values"))), sort_keys=True)
    pending_operation_cleared = False
    timeout_recovered = False
    timeout_value = ""
    while True:
        try:
            command_runner(command, input_text=values_text, timeout_seconds=3000)
            readiness = verify_helm_chart_ready(
                command_runner=command_runner,
                kube_context=kube_context,
                release_name=release_name,
                namespace=namespace,
                expected_version=version,
            )
            break
        except subprocess.TimeoutExpired as exc:
            try:
                readiness = verify_helm_chart_ready(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    release_name=release_name,
                    namespace=namespace,
                    expected_version=version,
                )
                timeout_recovered = True
                timeout_value = str(exc.timeout or "").strip()
                break
            except (RuntimeError, subprocess.TimeoutExpired) as readiness_exc:
                timeout_value = str(exc.timeout or "").strip()
                if (
                    not pending_operation_cleared
                    and _clear_pending_helm_release_operation(
                        command_runner=command_runner,
                        kube_context=kube_context,
                        release_name=release_name,
                        namespace=namespace,
                    )
                ):
                    pending_operation_cleared = True
                    continue
                raise RuntimeError(
                    f"Helm upgrade for target GPU stack chart {namespace}/{release_name} "
                    f"timed out after {exc.timeout} seconds and the live release is not ready yet. "
                    "Rerun the same `nebius-cxcli ext-soperator migrate ... --execute --approve` "
                    "command; cxcli will retry from live state."
                ) from readiness_exc
        except RuntimeError as exc:
            error_text = str(exc)
            if (
                not pending_operation_cleared
                and "another operation" in error_text.lower()
                and _clear_pending_helm_release_operation(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    release_name=release_name,
                    namespace=namespace,
                )
            ):
                pending_operation_cleared = True
                continue
            raise
    return {
        "id": component_id,
        "release_name": release_name,
        "namespace": namespace,
        "chart_ref": chart_ref,
        "version": version,
        "readiness": readiness.summary(),
        "applied_at": _utc_now(),
        **({"timeout_recovered": "true"} if timeout_recovered else {}),
        **({"timeout_seconds": timeout_value} if timeout_recovered and timeout_value else {}),
    }


def _target_gpu_stack_post_render_patches(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    app_id: str,
) -> tuple[Mapping[str, Any], ...]:
    patch_map = mk8s_gpu_flux_release_post_render_patches(
        payload,
        release_entry_ids={app_id},
    )
    return tuple(_mapping(item) for item in patch_map.get((target_ref, app_id), ()) or ())


def _post_render_patch_target_namespace(patch: Mapping[str, Any]) -> str:
    return str(_mapping(patch.get("target")).get("namespace", "") or "").strip()


def _post_render_patch_text(patch: Mapping[str, Any]) -> str:
    return str(patch.get("patch", "") or "").strip()


def _kubectl_command_with_optional_namespace(
    *,
    kube_context: str,
    namespace: str,
    verb_args: Sequence[str],
) -> list[str]:
    command = ["kubectl", "--context", kube_context]
    if namespace:
        command.extend(["-n", namespace])
    command.extend(verb_args)
    return command


def _apply_target_gpu_stack_post_render_patches(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    payload: Mapping[str, Any],
    target_ref: str,
    app_id: str,
) -> list[dict[str, str]]:
    applied: list[dict[str, str]] = []
    for patch in _target_gpu_stack_post_render_patches(
        payload=payload,
        target_ref=target_ref,
        app_id=app_id,
    ):
        patch_text = _post_render_patch_text(patch)
        if not patch_text:
            continue
        namespace = _post_render_patch_target_namespace(patch)
        command_runner(
            _kubectl_command_with_optional_namespace(
                kube_context=kube_context,
                namespace=namespace,
                verb_args=["apply", "-f", "-"],
            ),
            input_text=patch_text,
            timeout_seconds=300,
        )
        target = _mapping(patch.get("target"))
        applied.append(
            {
                "app_id": app_id,
                "kind": str(target.get("kind", "") or ""),
                "name": str(target.get("name", "") or ""),
                "namespace": namespace,
                "applied_at": _utc_now(),
            }
        )
    return applied


def _mapping_contains_subset(actual: Mapping[str, Any], desired: Mapping[str, Any]) -> bool:
    for key, desired_value in desired.items():
        if key not in actual:
            return False
        actual_value = actual.get(key)
        if isinstance(desired_value, Mapping):
            if not isinstance(actual_value, Mapping):
                return False
            if not _mapping_contains_subset(
                _mapping(actual_value),
                _mapping(desired_value),
            ):
                return False
            continue
        if actual_value != desired_value and not _json_string_values_equal(
            actual_value,
            desired_value,
        ):
            return False
    return True


def _json_string_values_equal(actual: Any, desired: Any) -> bool:
    if not isinstance(actual, str) or not isinstance(desired, str):
        return False
    try:
        return json.loads(actual) == json.loads(desired)
    except json.JSONDecodeError:
        return False


def _non_empty_mapping_values(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if item in (None, "", (), [], {}):
            continue
        result[str(key)] = item
    return result


def _kubectl_get_resource_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if str(payload.get("kind", "") or "") == "List":
        items = payload.get("items")
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes, bytearray)):
            first = next((item for item in items if isinstance(item, Mapping)), None)
            return _mapping(first)
    return payload


def _target_gpu_stack_post_render_patch_satisfied(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    patch: Mapping[str, Any],
) -> bool:
    patch_text = _post_render_patch_text(patch)
    if not patch_text:
        return True
    try:
        desired = yaml.safe_load(patch_text)
    except yaml.YAMLError:
        return False
    if not isinstance(desired, Mapping):
        return False
    desired_spec = _mapping(desired.get("spec"))
    if not desired_spec:
        return True
    namespace = _post_render_patch_target_namespace(patch)
    result = command_runner(
        _kubectl_command_with_optional_namespace(
            kube_context=kube_context,
            namespace=namespace,
            verb_args=["get", "-f", "-", "-o", "json", "--request-timeout=20s"],
        ),
        input_text=patch_text,
        timeout_seconds=120,
        check=False,
    )
    if result.returncode != 0:
        return False
    try:
        live = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(live, Mapping):
        return False
    live = _kubectl_get_resource_payload(live)
    return _mapping_contains_subset(_mapping(live.get("spec")), desired_spec)


def _adopt_helm_ownership_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    kind: str,
    name: str,
    namespace: str,
    release_namespace: str,
    check: bool,
) -> None:
    kind = str(kind or "").strip()
    name = str(name or "").strip()
    namespace = str(namespace or "").strip()
    if not kind or not name or kind == "Namespace":
        return
    resource_type = _KUBECTL_RESOURCE_BY_KIND.get(kind, kind)
    resource_ref = f"{resource_type}/{name}"
    namespace_args = ["-n", namespace] if namespace else []
    for verb, values in (
        ("label", ("app.kubernetes.io/managed-by=Helm",)),
        (
            "annotate",
            (
                f"meta.helm.sh/release-name={_SOPERATOR_TARGET_RELEASE_NAME}",
                f"meta.helm.sh/release-namespace={release_namespace}",
            ),
        ),
    ):
        try:
            command_runner(
                [
                    "kubectl",
                    "--context",
                    kube_context,
                    *namespace_args,
                    verb,
                    resource_ref,
                    *values,
                    "--overwrite",
                    "--request-timeout=20s",
                ],
                timeout_seconds=30,
                check=check,
            )
        except subprocess.TimeoutExpired:
            if check:
                raise


def _helm_upgrade_target_soperator(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    values: Mapping[str, Any],
    expected_version: str = "",
    wait: bool = True,
) -> None:
    chart_path = _target_soperator_chart_path()
    _ensure_soperator_chart_dependencies(command_runner=command_runner, chart_path=chart_path)
    _apply_soperator_crds(
        command_runner=command_runner,
        kube_context=kube_context,
        chart_path=chart_path,
    )
    with tempfile.TemporaryDirectory(prefix="nebius-cxcli-soperator-chart-") as temp_dir:
        staged_chart_path = Path(temp_dir) / chart_path.name
        shutil.copytree(
            chart_path,
            staged_chart_path,
            ignore=shutil.ignore_patterns("crds"),
        )
        command = [
            "helm",
            "--kube-context",
            kube_context,
            "upgrade",
            "--install",
            "soperator",
            str(staged_chart_path),
            "-n",
            _SOPERATOR_NAMESPACE,
            "--create-namespace",
            "--skip-crds",
            "--force-conflicts",
            "--take-ownership",
            "-f",
            "-",
        ]
        if wait:
            command.extend(["--wait", "--timeout", "45m"])
            timeout_seconds = 3000
        else:
            command.extend(["--no-hooks", "--timeout", "30m"])
            timeout_seconds = 2100
        values_text = json.dumps(to_plain_data(values), sort_keys=True)
        adoption_attempts: dict[tuple[str, str, str], int] = {}
        pending_operation_cleared = False
        webhook_startup_retries = 0
        while True:
            try:
                command_runner(command, input_text=values_text, timeout_seconds=timeout_seconds)
                if wait:
                    verify_helm_chart_ready(
                        command_runner=command_runner,
                        kube_context=kube_context,
                        release_name=_SOPERATOR_TARGET_RELEASE_NAME,
                        namespace=_SOPERATOR_NAMESPACE,
                        expected_version=expected_version,
                    )
                return
            except RuntimeError as exc:
                error_text = str(exc)
                if (
                    webhook_startup_retries < 6
                    and "failed calling webhook" in error_text.lower()
                    and (
                        "connection refused" in error_text.lower()
                        or "no endpoints available" in error_text.lower()
                    )
                ):
                    webhook_startup_retries += 1
                    time.sleep(10)
                    continue
                if (
                    not pending_operation_cleared
                    and "another operation" in error_text.lower()
                    and _clear_pending_helm_release_operation(
                        command_runner=command_runner,
                        kube_context=kube_context,
                    )
                ):
                    pending_operation_cleared = True
                    continue
                adopted_key = _adopt_helm_ownership_conflict(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    error_text=error_text,
                    adoption_attempts=adoption_attempts,
                )
                if adopted_key is None:
                    raise
                adoption_attempts[adopted_key] = adoption_attempts.get(adopted_key, 0) + 1


def _clear_pending_helm_release_operation(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    release_name: str = _SOPERATOR_TARGET_RELEASE_NAME,
    namespace: str = _SOPERATOR_NAMESPACE,
) -> bool:
    result = command_runner(
        [
            "helm",
            "--kube-context",
            kube_context,
            "history",
            release_name,
            "-n",
            namespace,
            "--max",
            "20",
            "-o",
            "json",
        ],
        timeout_seconds=120,
        check=False,
    )
    if result.returncode != 0:
        return False
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, list):
        return False
    latest_revision = 0
    latest_status = ""
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        revision = _positive_int(item.get("revision"), fallback=0)
        if revision <= latest_revision:
            continue
        latest_revision = revision
        latest_status = str(item.get("status", "") or "").strip().lower()
    if latest_revision <= 0 or not latest_status.startswith("pending-"):
        return False
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            namespace,
            "delete",
            "secret",
            f"sh.helm.release.v1.{release_name}.v{latest_revision}",
            "--ignore-not-found",
        ],
        timeout_seconds=300,
    )
    return True


def _target_worker_nodeset_names(values: Mapping[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    nodesets = values.get("nodesets")
    if isinstance(nodesets, Sequence) and not isinstance(nodesets, (str, bytes, bytearray)):
        for item in nodesets:
            if not isinstance(item, Mapping):
                continue
            name = normalize_component_token(item.get("name")) or "worker"
            if name:
                names.append(name)
    return tuple(dict.fromkeys(names or ["worker"]))


def _target_worker_nodesets_by_name(values: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    nodesets = values.get("nodesets")
    if not isinstance(nodesets, Sequence) or isinstance(nodesets, (str, bytes, bytearray)):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for item in nodesets:
        if not isinstance(item, Mapping):
            continue
        name = normalize_component_token(item.get("name")) or "worker"
        if name:
            result[name] = item
    return result


def _clear_worker_nodeset_ephemeral_storage_aliases(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    values: Mapping[str, Any],
) -> None:
    patch_text = json.dumps(
        {
            "spec": {
                "slurmd": {"resources": {"ephemeralStorage": None}},
                "munge": {"resources": {"ephemeralStorage": None}},
            }
        },
        sort_keys=True,
    )
    for name in _target_worker_nodeset_names(values):
        result = command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "patch",
                "nodeset",
                name,
                "--type=merge",
                "-p",
                patch_text,
            ],
            timeout_seconds=300,
            check=False,
        )
        if result.returncode == 0:
            continue
        detail = f"{result.stderr}\n{result.stdout}".lower()
        if "not found" in detail or "no resources found" in detail:
            continue
        raise RuntimeError(
            f"{_command_text(result.args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )


def _recreate_target_worker_statefulsets(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    values: Mapping[str, Any],
) -> None:
    for name in _target_worker_nodeset_names(values):
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "delete",
                "statefulset.apps.kruise.io",
                name,
                "--ignore-not-found",
                "--cascade=foreground",
                "--wait=true",
                "--timeout=10m",
            ],
            timeout_seconds=720,
            check=False,
        )


def _nodeset_pods_ready(status: Mapping[str, Any]) -> bool:
    for condition in _sequence_of_mappings(status.get("conditions")):
        if (
            str(condition.get("type", "") or "").strip() == "PodsReady"
            and str(condition.get("status", "") or "").strip() == "True"
        ):
            return True
    return False


def _worker_nodeset_ready_state(nodeset: Mapping[str, Any]) -> tuple[bool, str]:
    spec = _mapping(nodeset.get("spec"))
    status = _mapping(nodeset.get("status"))
    desired = _nonnegative_int(spec.get("replicas"), fallback=0)
    ready = _nonnegative_int(
        status.get("replicas", status.get("readyReplicas")),
        fallback=0,
    )
    phase = str(status.get("phase", "") or "").strip()
    pods_ready = _nodeset_pods_ready(status)
    if phase == "Ready" and (desired == 0 or ready >= desired or pods_ready):
        return True, f"phase={phase}, ready={ready}/{desired}"
    if desired > 0 and ready >= desired and pods_ready:
        return True, f"phase={phase or 'unknown'}, ready={ready}/{desired}"
    return False, f"phase={phase or 'unknown'}, ready={ready}/{desired}"


def _wait_for_target_worker_nodesets_ready(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    values: Mapping[str, Any],
    timeout_seconds: int,
) -> None:
    names = _target_worker_nodeset_names(values)
    if not names:
        return
    deadline = time.monotonic() + timeout_seconds
    last_state: dict[str, str] = {}
    while True:
        pending: list[str] = []
        for name in names:
            result = _json_from_command(
                command_runner,
                [
                    "kubectl",
                    "--context",
                    kube_context,
                    "-n",
                    _SOPERATOR_NAMESPACE,
                    "get",
                    "nodeset",
                    name,
                    "-o",
                    "json",
                ],
                timeout_seconds=120,
                check=False,
            )
            if not _mapping(result.get("metadata")):
                last_state[name] = "missing"
                pending.append(name)
                continue
            ready, detail = _worker_nodeset_ready_state(result)
            last_state[name] = detail
            if not ready:
                pending.append(name)
        if not pending:
            return
        if time.monotonic() >= deadline:
            details = ", ".join(f"{name} ({last_state.get(name, 'unknown')})" for name in pending)
            raise SoperatorMigrationPhasePending(
                "target worker NodeSet(s) did not become Ready within "
                f"{timeout_seconds}s: {details}."
            )
        time.sleep(10)


def _delete_pending_accounting_pvcs(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
) -> None:
    result = _json_from_command(
        command_runner,
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "get",
            "pvc",
            "-o",
            "json",
        ],
        timeout_seconds=120,
        check=False,
    )
    prefix = f"storage-{target_ref}-acct-db-"
    stale_statefulset_deleted = False
    for item in _sequence_of_mappings(result.get("items")):
        metadata = _mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        phase = str(_mapping(item.get("status")).get("phase", "") or "").strip()
        if not name.startswith(prefix) or phase != "Pending":
            continue
        access_modes = {
            str(mode).strip() for mode in _mapping(item.get("spec")).get("accessModes", [])
        }
        if "ReadWriteMany" not in access_modes and not stale_statefulset_deleted:
            command_runner(
                [
                    "kubectl",
                    "--context",
                    kube_context,
                    "-n",
                    _SOPERATOR_NAMESPACE,
                    "delete",
                    "statefulset.apps",
                    f"{target_ref}-acct-db",
                    "--ignore-not-found",
                    "--cascade=foreground",
                    "--wait=true",
                    "--timeout=5m",
                ],
                timeout_seconds=420,
            )
            stale_statefulset_deleted = True
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "delete",
                "pvc",
                name,
                "--ignore-not-found",
            ],
            timeout_seconds=300,
        )


def _reconcile_target_node_storage_labels(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    source_report: Mapping[str, Any] | None = None,
    worker_node_groups: Sequence[str] = (),
) -> None:
    seen_nodes: set[str] = set()
    for role in _SOPERATOR_SERVICE_ROLES:
        storage_group = _SOPERATOR_ROLE_SOURCE_KIND[role]
        for label_key in _SOPERATOR_NODESET_LABEL_KEYS:
            selector = f"{label_key}={role}"
            try:
                result = command_runner(
                    [
                        "kubectl",
                        "--context",
                        kube_context,
                        "get",
                        "nodes",
                        "-l",
                        selector,
                        "-o",
                        "json",
                        "--request-timeout=20s",
                    ],
                    timeout_seconds=30,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                continue
            if result.returncode != 0:
                detail = f"{result.stderr}\n{result.stdout}".lower()
                if "no resources found" not in detail and "not found" not in detail:
                    raise RuntimeError(
                        f"{_command_text(result.args)} failed: "
                        f"{result.stderr.strip() or result.stdout.strip()}"
                    )
                continue
            try:
                payload = json.loads(result.stdout or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"{_command_text(result.args)} returned invalid JSON: {exc}"
                ) from exc
            items = payload.get("items") if isinstance(payload, Mapping) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                metadata = _mapping(item.get("metadata"))
                labels = _mapping(metadata.get("labels"))
                if str(labels.get(label_key, "") or "") != role:
                    continue
                name = str(metadata.get("name", "") or "").strip()
                if not name or name in seen_nodes:
                    continue
                try:
                    label_result = command_runner(
                        [
                            "kubectl",
                            "--context",
                            kube_context,
                            "label",
                            "node",
                            name,
                            f"slurm.nebius.ai/nodeset-name={role}",
                            f"nebius.com/node-group={storage_group}",
                            "--overwrite",
                            "--request-timeout=20s",
                        ],
                        timeout_seconds=30,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    continue
                if label_result.returncode != 0:
                    detail = f"{label_result.stderr}\n{label_result.stdout}".lower()
                    if "not found" not in detail:
                        raise RuntimeError(
                            f"{_command_text(label_result.args)} failed: "
                            f"{label_result.stderr.strip() or label_result.stdout.strip()}"
                        )
                seen_nodes.add(name)
    _reconcile_target_worker_node_labels(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report or {},
        worker_node_groups=worker_node_groups,
        seen_nodes=seen_nodes,
    )


def _reconcile_target_worker_node_labels(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
    seen_nodes: set[str],
) -> None:
    inventory = _source_node_group_inventory(source_report)
    if not inventory:
        return
    alias_map, ambiguous_aliases = _source_group_alias_map(inventory)
    if worker_node_groups:
        group_names = tuple(
            dict.fromkeys(
                alias_map.get(group, group)
                for group in (
                    normalize_component_token(item) for item in worker_node_groups
                )
                if group and group not in ambiguous_aliases
            )
        )
    else:
        group_names = _infer_worker_node_groups(source_report)
    for group_name in group_names:
        raw_group = inventory.get(group_name)
        if not isinstance(raw_group, Mapping):
            continue
        nodeset_name = normalize_component_token(_source_group_nodeset(raw_group))
        if not nodeset_name:
            continue
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            continue
        node_group_name = _source_group_node_group_name(raw_group) or group_name
        try:
            result = command_runner(
                [
                    "kubectl",
                    "--context",
                    kube_context,
                    "get",
                    "nodes",
                    "-l",
                    f"nebius.com/node-group-id={node_group_id}",
                    "-o",
                    "json",
                    "--request-timeout=20s",
                ],
                timeout_seconds=30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            continue
        if result.returncode != 0:
            detail = f"{result.stderr}\n{result.stdout}".lower()
            if "no resources found" not in detail and "not found" not in detail:
                raise RuntimeError(
                    f"{_command_text(result.args)} failed: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
            continue
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{_command_text(result.args)} returned invalid JSON: {exc}") from exc
        items = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            labels = _node_metadata_labels(item)
            if str(labels.get("nebius.com/node-group-id", "") or "").strip() != node_group_id:
                continue
            name = str(_mapping(item.get("metadata")).get("name", "") or "").strip()
            if not name or name in seen_nodes:
                continue
            try:
                label_result = command_runner(
                    [
                        "kubectl",
                        "--context",
                        kube_context,
                        "label",
                        "node",
                        name,
                        f"slurm.nebius.ai/nodeset-name={nodeset_name}",
                        f"nebius.com/node-group={node_group_name}",
                        "--overwrite",
                        "--request-timeout=20s",
                    ],
                    timeout_seconds=30,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                continue
            if label_result.returncode != 0:
                detail = f"{label_result.stderr}\n{label_result.stdout}".lower()
                if "not found" not in detail:
                    raise RuntimeError(
                        f"{_command_text(label_result.args)} failed: "
                        f"{label_result.stderr.strip() or label_result.stdout.strip()}"
                    )
            seen_nodes.add(name)


def _adopt_helm_ownership_conflict(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    error_text: str,
    adoption_attempts: Mapping[tuple[str, str, str], int],
) -> tuple[str, str, str] | None:
    if "invalid ownership metadata" not in error_text:
        return None
    match = _HELM_OWNERSHIP_CONFLICT_RE.search(error_text)
    if not match:
        return None
    kind = str(match.group("kind") or "").strip()
    name = str(match.group("name") or "").strip()
    namespace = str(match.group("namespace") or "").strip()
    if not kind or not name:
        return None
    adopted_key = (kind, namespace, name)
    if int(adoption_attempts.get(adopted_key, 0) or 0) >= 2:
        return None
    resource_type = _KUBECTL_RESOURCE_BY_KIND.get(kind, kind)
    resource_ref = f"{resource_type}/{name}"
    namespace_args = ["-n", namespace] if namespace else []
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            *namespace_args,
            "label",
            resource_ref,
            "app.kubernetes.io/managed-by=Helm",
            "--overwrite",
            "--request-timeout=20s",
        ],
        timeout_seconds=30,
    )
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            *namespace_args,
            "annotate",
            resource_ref,
            "meta.helm.sh/release-name=soperator",
            f"meta.helm.sh/release-namespace={_SOPERATOR_NAMESPACE}",
            "--overwrite",
            "--request-timeout=20s",
        ],
        timeout_seconds=30,
    )
    return adopted_key


def _legacy_flux_helmrelease_name(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    return normalized in {
        "soperator-fluxcd",
        "flux-system-soperator-fluxcd",
    } or any(
        normalized.startswith(prefix)
        for prefix in _soperator_source_release_selectors()[2]
    )


def _suspend_legacy_flux_helmreleases(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    phase: dict[str, Any],
) -> list[str]:
    try:
        result = command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                "flux-system",
                "get",
                "helmreleases",
                "-o",
                "json",
                "--request-timeout=20s",
            ],
            timeout_seconds=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [
            "Skipped legacy Flux HelmRelease suspension because the Kubernetes API "
            "did not answer within 30s."
        ]
    if result.returncode != 0:
        detail = f"{result.stderr}\n{result.stdout}".lower()
        if "notfound" in detail or "not found" in detail:
            return []
        raise RuntimeError(
            f"{_command_text(result.args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_command_text(result.args)} returned invalid JSON: {exc}") from exc
    suspended = phase.setdefault("suspended_flux_helmreleases", [])
    if not isinstance(suspended, list):
        raise RuntimeError(
            "Soperator migration checkpoint rolling-compute-migration."
            "suspended_flux_helmreleases must be a list."
        )
    suspended_set = {str(item) for item in suspended}
    items = parsed.get("items", []) if isinstance(parsed, Mapping) else []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return []
    changed: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        metadata = _mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        if not _legacy_flux_helmrelease_name(name):
            continue
        spec = _mapping(item.get("spec"))
        if _bool_value(spec.get("suspend"), fallback=False):
            if name not in suspended_set:
                suspended.append(name)
                suspended_set.add(name)
            continue
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                "flux-system",
                "patch",
                "helmrelease",
                name,
                "--type",
                "merge",
                "-p",
                '{"spec":{"suspend":true}}',
            ],
            timeout_seconds=120,
        )
        if name not in suspended_set:
            suspended.append(name)
            suspended_set.add(name)
        changed.append(name)
    if changed:
        return [
            "Suspended legacy Flux Soperator HelmReleases before target chart takeover: "
            + ", ".join(changed)
            + "."
        ]
    if suspended:
        return ["Legacy Flux Soperator HelmReleases already suspended."]
    return []


def _source_report_migration_profile_group(source_report: Mapping[str, Any]) -> Mapping[str, Any]:
    profile_id = str(_mapping(source_report.get("report")).get("migration_profile_id", "") or "")
    return soperator_migration_profile_group(profile_id)


def _source_controller_quiesce_deployment_selectors(
    profile_group: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    execution_contract = _mapping(profile_group.get("execution_contract"))
    quiesce = _mapping(execution_contract.get("source_controller_quiesce"))
    if not _bool_value(
        quiesce.get("required_before_target_compute_reconcile"),
        fallback=False,
    ):
        return ()
    deployments = quiesce.get("deployments")
    if not isinstance(deployments, Sequence) or isinstance(
        deployments,
        (str, bytes, bytearray),
    ):
        return ()
    return tuple(item for item in deployments if isinstance(item, Mapping))


def _source_controller_quiesce_admission_webhooks(
    profile_group: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    execution_contract = _mapping(profile_group.get("execution_contract"))
    quiesce = _mapping(execution_contract.get("source_controller_quiesce"))
    if not _bool_value(
        quiesce.get("required_before_target_compute_reconcile"),
        fallback=False,
    ):
        return ()
    webhooks = quiesce.get("admission_webhooks")
    if not isinstance(webhooks, Sequence) or isinstance(webhooks, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in webhooks if isinstance(item, Mapping))


def _delete_source_controller_admission_webhooks(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    phase: dict[str, Any],
    profile_group: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    webhook_selectors = _source_controller_quiesce_admission_webhooks(profile_group)
    if not webhook_selectors:
        return False, []
    deleted = phase.setdefault("deleted_source_controller_admission_webhooks", [])
    if not isinstance(deleted, list):
        raise RuntimeError(
            "Soperator migration checkpoint rolling-compute-migration."
            "deleted_source_controller_admission_webhooks must be a list."
        )
    deleted_set = {str(item) for item in deleted}
    changed: list[str] = []
    for selector in webhook_selectors:
        kind = str(selector.get("kind", "") or "").strip()
        name = str(selector.get("name", "") or "").strip()
        if kind not in {"MutatingWebhookConfiguration", "ValidatingWebhookConfiguration"}:
            continue
        if not name:
            continue
        resource_type = (
            "mutatingwebhookconfiguration"
            if kind == "MutatingWebhookConfiguration"
            else "validatingwebhookconfiguration"
        )
        ref = f"{kind}/{name}"
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "delete",
                resource_type,
                name,
                "--ignore-not-found",
                "--request-timeout=20s",
            ],
            timeout_seconds=60,
        )
        if ref not in deleted_set:
            deleted.append(ref)
            deleted_set.add(ref)
            changed.append(ref)
    if changed:
        return True, [
            "Deleted old source Soperator admission webhooks before target takeover: "
            + ", ".join(changed)
            + "."
        ]
    return False, []


def _source_soperator_controller_deployment_matches(
    item: Mapping[str, Any],
    selector: Mapping[str, Any],
    *,
    target_version: str,
) -> bool:
    metadata = _mapping(item.get("metadata"))
    namespace = str(metadata.get("namespace", "") or "").strip()
    expected_namespace = str(selector.get("namespace", "") or "").strip()
    if expected_namespace and namespace != expected_namespace:
        return False
    name = str(metadata.get("name", "") or "").strip()
    expected_name = str(selector.get("name", "") or "").strip()
    if expected_name and name != expected_name:
        return False
    labels = _mapping(metadata.get("labels"))
    annotations = _mapping(metadata.get("annotations"))
    chart = str(labels.get("helm.sh/chart", "") or "").strip().lower()
    release_name = str(
        annotations.get("meta.helm.sh/release-name", "")
        or labels.get("app.kubernetes.io/instance", "")
        or ""
    ).strip()
    expected_release = str(selector.get("release_name", "") or "").strip()
    if expected_release and release_name != expected_release:
        return False
    chart_prefix = str(selector.get("chart_prefix", "") or "").strip().lower()
    if chart_prefix and chart != chart_prefix and not chart.startswith(f"{chart_prefix}-"):
        return False
    app_version = normalize_soperator_release_version(
        str(labels.get("app.kubernetes.io/version", "") or "")
    )
    return app_version not in _target_resume_versions(target_version)


def _scale_down_legacy_soperator_controllers(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    phase: dict[str, Any],
    target_version: str,
    profile_group: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    selectors = _source_controller_quiesce_deployment_selectors(profile_group)
    if not selectors:
        return False, []
    webhook_changed, webhook_lines = _delete_source_controller_admission_webhooks(
        command_runner=command_runner,
        kube_context=kube_context,
        phase=phase,
        profile_group=profile_group,
    )
    try:
        result = command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "get",
                "deployment",
                "-A",
                "-o",
                "json",
                "--request-timeout=20s",
            ],
            timeout_seconds=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Timed out listing deployments while looking for old source Soperator "
            "controllers. cxcli cannot safely continue while a source controller "
            "might still reconcile Slurm resources."
        ) from exc
    if result.returncode != 0:
        detail = f"{result.stderr}\n{result.stdout}".lower()
        if "notfound" in detail or "not found" in detail:
            return webhook_changed, webhook_lines
        raise RuntimeError(
            f"{_command_text(result.args)} failed while looking for old source "
            f"Soperator controllers: {result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{_command_text(result.args)} returned invalid deployment JSON: {exc}"
        ) from exc
    scaled = phase.setdefault("scaled_source_controller_deployments", [])
    if not isinstance(scaled, list):
        raise RuntimeError(
            "Soperator migration checkpoint rolling-compute-migration."
            "scaled_source_controller_deployments must be a list."
        )
    scaled_set = {str(item) for item in scaled}
    items = parsed.get("items", []) if isinstance(parsed, Mapping) else []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return webhook_changed, webhook_lines
    changed: list[str] = []
    known: list[str] = []
    for item in items:
        if not isinstance(item, Mapping) or not any(
            _source_soperator_controller_deployment_matches(
                item,
                selector,
                target_version=target_version,
            )
            for selector in selectors
        ):
            continue
        metadata = _mapping(item.get("metadata"))
        namespace = str(metadata.get("namespace", "") or "").strip()
        name = str(metadata.get("name", "") or "").strip()
        if not namespace or not name:
            continue
        ref = f"{namespace}/{name}"
        known.append(ref)
        replicas = _positive_int(_mapping(item.get("spec")).get("replicas"), fallback=1)
        if replicas > 0:
            command_runner(
                [
                    "kubectl",
                    "--context",
                    kube_context,
                    "-n",
                    namespace,
                    "scale",
                    "deployment",
                    name,
                    "--replicas=0",
                    "--request-timeout=20s",
                ],
                timeout_seconds=60,
            )
            changed.append(ref)
        if ref not in scaled_set:
            scaled.append(ref)
            scaled_set.add(ref)
    if changed:
        return True, [
            *webhook_lines,
            "Scaled down old source Soperator controller deployments before target "
            "takeover: "
            + ", ".join(changed)
            + "."
        ]
    if known:
        return webhook_changed, [
            *webhook_lines,
            "Old source Soperator controller deployments already scaled down: "
            + ", ".join(sorted(dict.fromkeys(known)))
            + "."
        ]
    return webhook_changed, webhook_lines


def _resume_slurm_partitions(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> None:
    _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("scontrol", "update", "PartitionName=ALL", "State=UP"),
        check=False,
        timeout_seconds=120,
    )


def _resume_drained_slurm_nodes(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> list[str]:
    result = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("sinfo", "-h", "-o", "%N %T"),
        check=False,
        timeout_seconds=120,
    )
    if result.returncode != 0:
        return []
    drained: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        node_expr, state = parts[0], parts[1].lower()
        if "drain" in state:
            drained.append(node_expr)
    if not drained:
        return []
    _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("scontrol", "update", f"NodeName={','.join(drained)}", "State=RESUME"),
        check=False,
        timeout_seconds=120,
    )
    return drained


def _resume_slurm_after_cutover(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> list[str]:
    _resume_slurm_partitions(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    resumed_nodes = _resume_drained_slurm_nodes(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    lines = ["Slurm partitions resumed after target Soperator cutover."]
    if resumed_nodes:
        lines.append(
            "Slurm nodes resumed after target Soperator cutover: " + ", ".join(resumed_nodes) + "."
        )
    return lines


def _scale_node_group(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    node_group_id: str,
    count: int,
) -> None:
    result = command_runner(
        [
            "nebius",
            "mk8s",
            "node-group",
            "update",
            node_group_id,
            "--fixed-node-count",
            str(count),
            "--format",
            "json",
            "--timeout",
            "45m",
        ],
        timeout_seconds=3000,
        check=False,
    )
    if result.returncode != 0 and not _command_not_found(result):
        raise RuntimeError(
            f"{_command_text(result.args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )


def _delete_node_group(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    node_group_id: str,
) -> None:
    result = command_runner(
        [
            "nebius",
            "mk8s",
            "node-group",
            "delete",
            node_group_id,
            "--timeout",
            "45m",
        ],
        timeout_seconds=3000,
        check=False,
    )
    if result.returncode != 0 and not _command_not_found(result):
        raise RuntimeError(
            f"{_command_text(result.args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )


def _command_not_found(result: SoperatorMigrationCommandResult) -> bool:
    detail = f"{result.stderr}\n{result.stdout}".lower()
    return "notfound" in detail or "not found" in detail or "resource not found" in detail


def _ensure_live_nodes_ready(snapshot: Mapping[str, Any]) -> None:
    groups = _mapping(snapshot.get("node_groups"))
    if not groups:
        raise RuntimeError("Soperator migration validation found no Kubernetes node groups.")
    empty = [
        str(name)
        for name, group in groups.items()
        if isinstance(group, Mapping) and int(group.get("node_count", 0) or 0) <= 0
    ]
    if empty:
        raise RuntimeError(
            "Soperator migration validation found empty node groups: " + ", ".join(empty)
        )


def _execute_external_node_template_upgrade_phase(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
    checkpoint_writer: Callable[[], None] | None = None,
    rollout: SoperatorExternalNodeTemplateRollout,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, _EXTERNAL_NODE_TEMPLATE_PHASE_ID)
    onboarding = _target_onboarding(payload, target_ref)
    target = _external_node_template_target(onboarding)
    target_payload = {
        "k8s_version": target.k8s_version,
        "os": target.os,
        "gpu_stack_preset": target.gpu_stack_preset,
    }
    phase["target"] = dict(target_payload)
    cluster_id = _external_migration_cluster_id(
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        command_runner=command_runner,
    )
    phase["cluster_id"] = cluster_id
    mutation_performed = False
    lines: list[str] = []

    control_plane = phase.setdefault("control_plane", {})
    if not isinstance(control_plane, dict):
        raise RuntimeError(
            "Soperator migration checkpoint external-node-template-upgrade.control_plane "
            "must be a mapping."
        )
    cluster = _cluster_payload_by_id(command_runner=command_runner, cluster_id=cluster_id)
    current_version = _minor_version_text_or_empty(_cluster_control_plane_version(cluster))
    if not current_version:
        raise SoperatorMigrationPhasePending(
            "external-node-template-upgrade could not detect the live MK8s control-plane "
            "version. Rerun onboarding after confirming `nebius mk8s cluster get` works."
        )
    control_plane["current_version"] = current_version
    control_plane["target_version"] = target.k8s_version
    _ensure_external_node_template_k8s_not_downgrade(current_version, target.k8s_version)
    hops = minor_version_hops(current_version, target.k8s_version)
    hop_state = control_plane.setdefault("hops", {})
    if not isinstance(hop_state, dict):
        raise RuntimeError(
            "Soperator migration checkpoint external-node-template-upgrade.control_plane.hops "
            "must be a mapping."
        )
    if not hops:
        control_plane["status"] = "already-current"
        lines.append(f"External MK8s control plane already at Kubernetes {target.k8s_version}.")
    for hop in hops:
        state = hop_state.setdefault(hop.to_version, {})
        if not isinstance(state, dict):
            raise RuntimeError(
                "Soperator migration checkpoint external-node-template-upgrade "
                f"control-plane hop {hop.to_version} must be a mapping."
            )
        if (
            state.get("status") == "completed"
            and state.get("target_version") == hop.to_version
            and _minor_version_at_least(current_version, hop.to_version)
        ):
            lines.append(f"External MK8s control-plane hop already completed: {hop.to_version}.")
            current_version = hop.to_version
            continue
        state.update(
            {
                "from_version": hop.from_version,
                "target_version": hop.to_version,
                "status": "updating",
                "started_at": state.get("started_at") or _utc_now(),
            }
        )
        if checkpoint_writer is not None:
            checkpoint_writer()
        try:
            cluster = _update_cluster_control_plane(
                command_runner=command_runner,
                cluster_id=cluster_id,
                control_plane_version=hop.to_version,
            )
        except Exception as exc:
            state["status"] = "failed"
            state["error"] = str(exc)
            if checkpoint_writer is not None:
                checkpoint_writer()
            raise
        mutation_performed = True
        state["status"] = "completed"
        state["completed_at"] = _utc_now()
        state["observed_version"] = (
            _minor_version_text_or_empty(_cluster_control_plane_version(cluster)) or hop.to_version
        )
        control_plane["current_version"] = state["observed_version"]
        if checkpoint_writer is not None:
            checkpoint_writer()
        lines.append(
            f"External MK8s control-plane upgraded: {hop.from_version} -> {hop.to_version}."
        )

    service_groups, worker_groups = _split_external_node_template_upgrade_groups(
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    groups = tuple([*service_groups, *worker_groups])
    if not groups:
        raise SoperatorMigrationPhasePending(
            "external-node-template-upgrade requires source node-group inventory before "
            "it can upgrade node templates. Rerun `nebius-cxcli ext-soperator onboard`."
        )
    group_states = phase.setdefault("node_groups", {})
    if not isinstance(group_states, dict):
        raise RuntimeError(
            "Soperator migration checkpoint external-node-template-upgrade.node_groups "
            "must be a mapping."
        )
    phase["rollout"] = rollout.to_manifest_dict()
    phase["service_groups"] = [name for name, _raw_group in service_groups]
    phase["worker_groups"] = [name for name, _raw_group in worker_groups]
    phase["worker_budget"] = _worker_rollout_budget(
        rollout,
        worker_group_count=len(worker_groups),
    )

    def _prepare_group(
        group_name: str,
        raw_group: Mapping[str, Any],
        *,
        worker_group: bool,
    ) -> dict[str, Any] | None:
        nonlocal mutation_performed
        group_state = group_states.setdefault(group_name, {})
        if not isinstance(group_state, dict):
            raise RuntimeError(
                "Soperator migration checkpoint external-node-template-upgrade "
                f"node group {group_name} must be a mapping."
            )
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            raise SoperatorMigrationPhasePending(
                "external-node-template-upgrade requires a Nebius node group id for "
                f"source group '{group_name}'. Rerun `nebius-cxcli ext-soperator onboard` "
                "against a Nebius MK8s target."
            )
        node_group = _node_group_payload_by_id(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
        update_args = _external_node_template_update_args(
            node_group=node_group,
            source_group=raw_group,
            target=target,
        )
        clear_template_gpu_settings = _external_node_template_clears_cpu_gpu_settings(
            node_group=node_group,
            source_group=raw_group,
        )
        strategy_args, strategy_label = _external_node_template_strategy_cli_args(
            rollout,
            worker_group=worker_group,
        )
        live_original_strategy_args = _node_group_strategy_cli_args(
            _node_group_strategy(node_group),
            default_max_surge_count=1,
            default_max_unavailable_count=0,
            default_drain_timeout="0s",
        )
        stored_original_strategy_args = group_state.get("original_strategy_args")
        if stored_original_strategy_args is None:
            original_strategy_args = tuple(live_original_strategy_args)
            group_state["original_strategy_args"] = list(original_strategy_args)
            group_state["strategy_restore_required"] = (
                tuple(original_strategy_args) != tuple(str(item) for item in strategy_args)
            )
        elif isinstance(stored_original_strategy_args, Sequence) and not isinstance(
            stored_original_strategy_args,
            str,
        ):
            original_strategy_args = tuple(str(item) for item in stored_original_strategy_args)
        else:
            raise RuntimeError(
                "Soperator migration checkpoint external-node-template-upgrade "
                f"node group {group_name}.original_strategy_args must be a list."
            )
        timeout_seconds = _node_group_rollout_timeout_seconds(node_group)
        timeout = _node_group_rollout_timeout_text(timeout_seconds)
        group_state.update(
            {
                "node_group_id": node_group_id,
                "node_group_name": _node_group_name(node_group)
                or _source_group_node_group_name(raw_group)
                or group_name,
                "source_group": group_name,
                "target": dict(target_payload),
                "current": {
                    "k8s_version": _minor_version_text_or_empty(_node_group_version(node_group)),
                    "os": _node_group_template_os(node_group),
                    "gpu_stack_preset": _node_group_template_gpu_drivers_preset(node_group),
                },
                "gpu": _source_group_is_gpu(raw_group),
                "clear_template_gpu_settings": clear_template_gpu_settings,
                "strategy": strategy_label,
                "rollout": rollout.to_manifest_dict() if worker_group else {"strategy": strategy_label},
                "update_args": list(update_args),
                "timeout": timeout,
            }
        )

        def _restore_original_strategy_if_required(
            current_node_group: Mapping[str, Any],
        ) -> bool:
            nonlocal mutation_performed
            if not bool(group_state.get("strategy_restore_required")):
                return False
            if _node_group_strategy_matches_args(current_node_group, original_strategy_args):
                return False
            try:
                _json_from_command(
                    command_runner,
                    _node_group_update_command(
                        node_group_id,
                        update_args=(),
                        strategy_args=original_strategy_args,
                        timeout=timeout,
                    ),
                    timeout_seconds=timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                _reconcile_node_group_update_timeout(
                    command_runner,
                    node_group_id=node_group_id,
                    update_args=(),
                    strategy_args=original_strategy_args,
                    clear_template_gpu_settings=False,
                    action="strategy restore",
                    timeout=exc,
                )
            mutation_performed = True
            return True

        resume_waiting_rollout = group_state.get("status") == "waiting-rollout"
        if resume_waiting_rollout and not update_args and not clear_template_gpu_settings:
            ready, readiness_summary = _node_group_readiness_summary(node_group)
            if not ready:
                group_state["pending_reason"] = _node_group_update_timeout_message(
                    node_group_id=node_group_id,
                    action="node-template update",
                    readiness_summary=readiness_summary,
                )
                if checkpoint_writer is not None:
                    checkpoint_writer()
                raise SoperatorMigrationPhasePending(str(group_state["pending_reason"]))
            strategy_restored = _restore_original_strategy_if_required(node_group)
            group_state["status"] = "completed"
            group_state["completed_at"] = _utc_now()
            group_state["strategy_restored"] = True
            group_state.pop("error", None)
            group_state.pop("pending_reason", None)
            if checkpoint_writer is not None:
                checkpoint_writer()
            lines.append(
                "External node-template rollout completed after resume: "
                f"{group_name}; "
                + (
                    "original strategy restored."
                    if strategy_restored
                    else "original strategy already in place."
                )
            )
            return None
        if (
            group_state.get("status") in {"completed", "already-current"}
            and group_state.get("target") == target_payload
            and not update_args
            and not clear_template_gpu_settings
        ):
            group_state.pop("error", None)
            group_state.pop("pending_reason", None)
            lines.append(f"External node-template already handled: {group_name}.")
            return None
        if not update_args and not clear_template_gpu_settings:
            ready, readiness_summary = _node_group_readiness_summary(node_group)
            if not ready:
                group_state["status"] = "waiting-rollout"
                group_state["pending_reason"] = _node_group_update_timeout_message(
                    node_group_id=node_group_id,
                    action="node-template update",
                    readiness_summary=readiness_summary,
                )
                if checkpoint_writer is not None:
                    checkpoint_writer()
                raise SoperatorMigrationPhasePending(str(group_state["pending_reason"]))
            previous_status = str(group_state.get("status", "") or "")
            strategy_restored = _restore_original_strategy_if_required(node_group)
            resume_after_started = previous_status in {"failed", "updating", "waiting-rollout"}
            group_state["status"] = "completed" if resume_after_started else "already-current"
            group_state["completed_at"] = _utc_now()
            group_state["strategy_restored"] = (
                bool(group_state.get("strategy_restore_required"))
                if resume_after_started
                else strategy_restored
            )
            group_state.pop("error", None)
            group_state.pop("pending_reason", None)
            if checkpoint_writer is not None:
                checkpoint_writer()
            if resume_after_started:
                lines.append(
                    "External node-template rollout completed after live-state "
                    f"reconciliation: {group_name}; "
                    + (
                        "original strategy restored."
                        if strategy_restored
                        else "original strategy already in place."
                    )
                )
            else:
                lines.append(f"External node-template already current: {group_name}.")
            return None
        group_state["status"] = "updating"
        group_state["started_at"] = group_state.get("started_at") or _utc_now()
        return {
            "group_name": group_name,
            "raw_group": raw_group,
            "group_state": group_state,
            "node_group_id": node_group_id,
            "node_group": node_group,
            "update_args": update_args,
            "clear_template_gpu_settings": clear_template_gpu_settings,
            "strategy_args": strategy_args,
            "strategy_label": strategy_label,
            "timeout": timeout,
            "timeout_seconds": timeout_seconds,
            "worker_group": worker_group,
        }

    def _run_prepared_group(
        work: Mapping[str, Any],
        *,
        allow_service_quiesce: bool,
        write_progress: bool,
    ) -> tuple[bool, list[str]]:
        group_name = str(work["group_name"])
        raw_group = _mapping(work["raw_group"])
        group_state = work["group_state"]
        if not isinstance(group_state, dict):
            raise RuntimeError(
                "Soperator migration checkpoint external-node-template-upgrade "
                f"node group {group_name} must be a mapping."
            )
        work_lines: list[str] = []
        service_role = _source_group_service_quiesce_role(group_name, raw_group)
        service_quiesce_state: dict[str, Any] | None = None
        if (
            allow_service_quiesce
            and service_role
            and _positive_int(raw_group.get("node_count"), fallback=1) <= 1
        ):
            raw_quiesce_state = group_state.setdefault("service_quiesce", {})
            if not isinstance(raw_quiesce_state, dict):
                raise RuntimeError(
                    "Soperator migration checkpoint external-node-template-upgrade "
                    f"node group {group_name}.service_quiesce must be a mapping."
                )
            service_quiesce_state = raw_quiesce_state
            work_lines.extend(
                _quiesce_external_service_role(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    role=service_role,
                    state=service_quiesce_state,
                )
            )
            if write_progress and checkpoint_writer is not None:
                checkpoint_writer()
        if write_progress and checkpoint_writer is not None:
            checkpoint_writer()
        try:
            _update_node_group_with_temporary_strategy(
                command_runner=command_runner,
                node_group_id=str(work["node_group_id"]),
                update_args=work["update_args"],
                strategy_args=work["strategy_args"],
                original_node_group=work["node_group"],
                clear_template_gpu_settings=bool(work["clear_template_gpu_settings"]),
                timeout=str(work["timeout"]),
                timeout_seconds=int(work["timeout_seconds"]),
            )
        except SoperatorMigrationPhasePending as exc:
            group_state["status"] = "waiting-rollout"
            group_state["pending_reason"] = str(exc)
            if service_quiesce_state is not None:
                try:
                    work_lines.extend(
                        _restore_external_service_role(
                            command_runner=command_runner,
                            kube_context=kube_context,
                            state=service_quiesce_state,
                        )
                    )
                except Exception as restore_exc:
                    group_state["restore_error"] = str(restore_exc)
                    if checkpoint_writer is not None:
                        checkpoint_writer()
                    raise RuntimeError(
                        f"External node-template update is pending for {group_name}; "
                        "additionally, cxcli could not restore quiesced Soperator "
                        f"{service_role} workloads."
                    ) from exc
            if write_progress and checkpoint_writer is not None:
                checkpoint_writer()
            raise
        except Exception as exc:
            group_state["status"] = "failed"
            group_state["error"] = str(exc)
            if service_quiesce_state is not None:
                try:
                    work_lines.extend(
                        _restore_external_service_role(
                            command_runner=command_runner,
                            kube_context=kube_context,
                            state=service_quiesce_state,
                        )
                    )
                except Exception as restore_exc:
                    group_state["restore_error"] = str(restore_exc)
                    if checkpoint_writer is not None:
                        checkpoint_writer()
                    raise RuntimeError(
                        f"External node-template update failed for {group_name}; "
                        "additionally, cxcli could not restore quiesced Soperator "
                        f"{service_role} workloads."
                    ) from exc
            if write_progress and checkpoint_writer is not None:
                checkpoint_writer()
            raise
        if service_quiesce_state is not None:
            work_lines.extend(
                _restore_external_service_role(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    state=service_quiesce_state,
                )
            )
            if write_progress and checkpoint_writer is not None:
                checkpoint_writer()
        group_state["status"] = "completed"
        group_state["completed_at"] = _utc_now()
        group_state["strategy_restored"] = True
        work_lines.append(
            "External node-template upgraded: "
            f"{group_name} -> Kubernetes {target.k8s_version}, OS {target.os}"
            + (f", GPU stack {target.gpu_stack_preset}" if _source_group_is_gpu(raw_group) else "")
            + f" using {work['strategy_label']}."
        )
        return True, work_lines

    for group_name, raw_group in service_groups:
        work = _prepare_group(group_name, raw_group, worker_group=False)
        if work is None:
            continue
        phase["active_worker_wave"] = 0
        if checkpoint_writer is not None:
            checkpoint_writer()
        phase_mutation, work_lines = _run_prepared_group(
            work,
            allow_service_quiesce=True,
            write_progress=True,
        )
        mutation_performed = mutation_performed or phase_mutation
        lines.extend(work_lines)

    worker_work: list[dict[str, Any]] = []
    for group_name, raw_group in worker_groups:
        work = _prepare_group(group_name, raw_group, worker_group=True)
        if work is not None:
            worker_work.append(work)
    worker_budget = _worker_rollout_budget(rollout, worker_group_count=len(worker_groups))
    worker_waves = tuple(
        tuple(worker_work[index : index + worker_budget])
        for index in range(0, len(worker_work), max(1, worker_budget))
    )
    phase["worker_waves"] = [
        [str(work["group_name"]) for work in wave] for wave in worker_waves
    ]
    for wave_index, wave in enumerate(worker_waves, start=1):
        phase["active_worker_wave"] = wave_index
        for work in wave:
            group_state = work["group_state"]
            if isinstance(group_state, dict):
                group_state["status"] = "updating"
                group_state["worker_wave"] = wave_index
                group_state["started_at"] = group_state.get("started_at") or _utc_now()
        if checkpoint_writer is not None:
            checkpoint_writer()
        lines.append(
            f"External worker node-template wave {wave_index}: "
            + ", ".join(str(work["group_name"]) for work in wave)
            + "."
        )
        results: list[tuple[bool, list[str]]] = []
        if (
            rollout.strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE
            and len(wave) > 1
        ):
            worker_exception: BaseException | None = None
            with ThreadPoolExecutor(max_workers=len(wave)) as executor:
                future_by_group = {
                    executor.submit(
                        _run_prepared_group,
                        work,
                        allow_service_quiesce=False,
                        write_progress=False,
                    ): str(work["group_name"])
                    for work in wave
                }
                for future in as_completed(future_by_group):
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        worker_exception = exc
                        for pending in future_by_group:
                            pending.cancel()
                        break
            if worker_exception is not None:
                if checkpoint_writer is not None:
                    checkpoint_writer()
                raise worker_exception
        else:
            for work in wave:
                try:
                    results.append(
                        _run_prepared_group(
                            work,
                            allow_service_quiesce=False,
                            write_progress=False,
                        )
                    )
                except Exception:
                    if checkpoint_writer is not None:
                        checkpoint_writer()
                    raise
        for phase_mutation, work_lines in results:
            mutation_performed = mutation_performed or phase_mutation
            lines.extend(work_lines)
        if checkpoint_writer is not None:
            checkpoint_writer()
    lines.append(
        "External node-template strategy: service-role groups use serial zero-surge "
        "(max_surge=0, max_unavailable=1, drain_timeout=30m); worker groups use "
        f"{rollout.strategy} with "
        + _worker_rollout_budget_label(rollout, worker_group_count=len(worker_groups))
        + " and "
        + _effective_worker_group_strategy_label(rollout)
        + "."
    )
    phase["completed_at"] = _utc_now()
    return mutation_performed, lines


def _execute_target_gpu_stack_remediation_phase(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, _TARGET_GPU_STACK_PHASE_ID)
    rows = _target_gpu_stack_app_rows(payload, target_ref)
    if not rows:
        phase["missing_app_rows"] = list(_TARGET_GPU_STACK_APP_ORDER)
        raise SoperatorMigrationPhasePending(
            "target GPU stack reconciliation requires target-scoped GPU Operator or "
            "Network Operator app rows. Rerun `nebius-cxcli ext-soperator onboard` so "
            "the accepted config carries the reconciliation app rows before executing migration."
        )
    applied: list[Mapping[str, str]] = []
    lines: list[str] = []
    for row in rows:
        result = _helm_upgrade_target_app_chart(
            command_runner=command_runner,
            kube_context=kube_context,
            row=row,
        )
        applied.append(result)
        post_render_patches = _apply_target_gpu_stack_post_render_patches(
            command_runner=command_runner,
            kube_context=kube_context,
            payload=payload,
            target_ref=target_ref,
            app_id=result["id"],
        )
        if post_render_patches:
            result["post_render_patches"] = json.dumps(
                post_render_patches,
                sort_keys=True,
            )
        version = f"@{result['version']}" if result.get("version") else ""
        lines.append(
            "Applied target GPU stack chart: "
            f"{result['id']}={result['release_name']} "
            f"({result['namespace']}, {result['chart_ref']}{version})"
        )
        if result.get("timeout_recovered") == "true":
            timeout_suffix = (
                f" after {result['timeout_seconds']}s"
                if result.get("timeout_seconds")
                else ""
            )
            lines.append(
                "Accepted target GPU stack chart after Helm client timeout"
                f"{timeout_suffix}: {result['id']} live release is ready."
            )
        for patch in post_render_patches:
            patch_name = patch.get("name") or patch.get("kind") or "resource"
            lines.append(
                f"Applied target GPU stack post-render patch: {result['id']} -> {patch_name}"
            )
    phase["charts"] = [dict(item) for item in applied]
    phase["completed_at"] = _utc_now()
    return True, lines


def _execute_create_aligned_sfs_phase(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    target_ref: str,
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    project_id = _nebius_project_id(payload)
    specs = _aligned_filesystem_specs(payload=payload, target_ref=target_ref)
    specs_by_key = {spec.key: spec for spec in specs}
    phase = _phase_state(checkpoint, "create-aligned-sfs")
    filesystems = phase.setdefault("filesystems", {})
    if not isinstance(filesystems, dict):
        raise RuntimeError(
            "Soperator migration checkpoint create-aligned-sfs.filesystems must be a mapping."
        )
    mutation_performed = False
    lines: list[str] = []
    filesystem_ids_by_key: dict[str, str] = {}
    for spec in specs:
        existing = _get_filesystem_by_name(
            command_runner=command_runner,
            project_id=project_id,
            name=spec.name,
        )
        created = False
        filesystem = existing
        if not _filesystem_id(filesystem):
            filesystem = _create_filesystem(
                command_runner=command_runner,
                project_id=project_id,
                spec=spec,
            )
            created = True
            mutation_performed = True
        else:
            _validate_existing_filesystem(spec, filesystem)
        filesystem_id = _filesystem_id(filesystem)
        if not filesystem_id:
            raise RuntimeError(
                f"Aligned SFS filesystem '{spec.name}' did not return a filesystem id."
            )
        filesystem_ids_by_key[spec.key] = filesystem_id
        filesystems[spec.key] = {
            "id": filesystem_id,
            "name": spec.name,
            "mount_tag": spec.mount_tag,
            "size_gib": spec.size_gib,
            "block_size_kib": spec.block_size_kib,
            "type": spec.filesystem_type,
            "created": created,
        }
        lines.append(
            f"Aligned SFS {spec.key}: {'created' if created else 'reused'} {spec.name} ({filesystem_id})"
        )
    attached, attachments = _attach_filesystems_to_source_node_groups(
        command_runner=command_runner,
        source_report=source_report,
        attachment_keys_by_group=_approved_role_attachment_keys(
            payload=payload,
            target_ref=target_ref,
            worker_node_groups=worker_node_groups,
            source_report=source_report,
        ),
        filesystem_ids_by_key=filesystem_ids_by_key,
        specs_by_key=specs_by_key,
    )
    mutation_performed = mutation_performed or attached
    phase["node_group_attachments"] = attachments
    lines.append(
        "Aligned SFS node-group attachments: "
        + ", ".join(
            f"{item['source_group']}={'updated' if item['updated'] else 'already-attached'}"
            for item in attachments
        )
    )
    if attachments:
        lines.append(
            "Aligned SFS node-group update strategy: zero-surge "
            "(max_surge=0, max_unavailable=1, drain_timeout=30m); preserved worker "
            "groups do not need additional worker quota, but active group capacity "
            "may be reduced by one node during rollout."
        )
    return mutation_performed, lines


def _execute_online_bulk_data_sync_phase(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "online-bulk-data-sync")
    jobs = phase.setdefault("jobs", {})
    if not isinstance(jobs, dict):
        raise RuntimeError(
            "Soperator migration checkpoint online-bulk-data-sync.jobs must be a mapping."
        )
    storage = _snapshot_storage(source_report)
    if not storage:
        phase["skipped_reason"] = "source discovery did not detect old Soperator storage"
        return False, ["Data sync skipped: no old Soperator storage was detected."]
    mutation_performed = False
    lines: list[str] = []
    manifests: list[dict[str, Any]] = []
    live_pvcs = _snapshot_pvc_names(live_snapshot)
    missing_pvcs: list[str] = []
    for key in _SOPERATOR_STORAGE_KEYS:
        source_pvc = _source_pvc_name_for_storage_key(source_report, key)
        target_pvc = _target_pvc_name_for_storage_key(payload, target_ref, key)
        if not source_pvc:
            continue
        for pvc_name, role in ((source_pvc, "source"), (target_pvc, "target")):
            if pvc_name not in live_pvcs:
                missing_pvcs.append(f"{role}:{key}:{pvc_name}")
        if source_pvc == target_pvc:
            jobs[key] = {"source_pvc": source_pvc, "target_pvc": target_pvc, "skipped": True}
            lines.append(
                f"Data sync {key}: skipped because source and target PVC are {source_pvc}."
            )
            continue
        manifests.append(_copy_job_manifest(key=key, source_pvc=source_pvc, target_pvc=target_pvc))
        jobs[key] = {"source_pvc": source_pvc, "target_pvc": target_pvc}
    if missing_pvcs:
        phase["missing_pvcs"] = missing_pvcs
        raise SoperatorMigrationPhasePending(
            "online-bulk-data-sync requires existing source and target PVCs before copy Jobs run. "
            "Missing PVCs: " + ", ".join(missing_pvcs) + "."
        )
    if not manifests:
        phase["skipped_reason"] = "no source PVC to target PVC copy pairs were detected"
        return False, lines or ["Data sync skipped: no PVC copy pairs were detected."]
    _kubectl_apply_objects(
        command_runner=command_runner,
        kube_context=kube_context,
        objects=manifests,
        timeout_seconds=300,
    )
    mutation_performed = True
    for manifest in manifests:
        name = str(_mapping(manifest.get("metadata")).get("name", "") or "").strip()
        if not name:
            continue
        _kubectl_wait(
            command_runner=command_runner,
            kube_context=kube_context,
            namespace=_SOPERATOR_NAMESPACE,
            resource=f"job/{name}",
            condition="condition=complete",
            timeout="60m",
            timeout_seconds=3900,
        )
        lines.append(f"Data sync job completed: {name}")
    return mutation_performed, lines


def _execute_rolling_compute_migration_phase(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
    checkpoint_writer: Callable[[], None] | None = None,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "rolling-compute-migration")
    nodes = _nodes_for_worker_groups(
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    phase["worker_nodes"] = list(nodes)
    source_snapshot = _mapping(source_report.get("snapshot"))
    if not _has_soperator_custom_resources(source_snapshot) and not _has_soperator_custom_resources(
        live_snapshot
    ):
        phase["skipped_reason"] = "no Soperator Slurm custom resources were detected"
        return False, [
            "Compute migration skipped: no Slurm custom resources were detected on the source cluster."
        ]
    mutation_performed, lines = _create_or_reuse_target_node_groups(
        checkpoint=checkpoint,
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        worker_node_groups=worker_node_groups,
        command_runner=command_runner,
    )
    if checkpoint_writer is not None:
        checkpoint_writer()
    lines.extend(
        _suspend_legacy_flux_helmreleases(
            command_runner=command_runner,
            kube_context=kube_context,
            phase=phase,
        )
    )
    scaled_source_controller, scale_lines = _scale_down_legacy_soperator_controllers(
        command_runner=command_runner,
        kube_context=kube_context,
        phase=phase,
        target_version=str(_mapping(source_report.get("report")).get("target_version", "") or ""),
        profile_group=_source_report_migration_profile_group(source_report),
    )
    mutation_performed = mutation_performed or scaled_source_controller
    lines.extend(scale_lines)
    if checkpoint_writer is not None:
        checkpoint_writer()
    live_source_slurmcluster_present = _live_source_slurmcluster_present(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        target_ref=target_ref,
    )
    quiet_lines = _ensure_slurm_quiet(
        command_runner=command_runner,
        kube_context=kube_context,
        allow_missing_login_recovery=not live_source_slurmcluster_present,
    )
    lines.extend(
        _ensure_worker_nodeset_topology_checkpoint(
            checkpoint=checkpoint,
            source_report=source_report,
            kube_context=kube_context,
            command_runner=command_runner,
        )
    )
    if checkpoint_writer is not None:
        checkpoint_writer()
    _delete_conflicting_source_slurm_resources(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        target_ref=target_ref,
    )
    values = _patch_target_values_for_compute(
        payload=payload,
        target_ref=target_ref,
        checkpoint=checkpoint,
        source_report=source_report,
        live_snapshot=live_snapshot,
    )
    _delete_pending_accounting_pvcs(
        command_runner=command_runner,
        kube_context=kube_context,
        target_ref=target_ref,
    )
    checkpoint_worker_groups = tuple(
        str(group or "") for group in checkpoint.get("worker_node_groups", []) or []
    )
    _reconcile_target_node_storage_labels(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        worker_node_groups=checkpoint_worker_groups,
    )
    try:
        _helm_upgrade_target_soperator(
            command_runner=command_runner,
            kube_context=kube_context,
            values=values,
            expected_version=str(
                _mapping(source_report.get("report")).get("target_version", "") or ""
            ),
            wait=False,
        )
        _clear_worker_nodeset_ephemeral_storage_aliases(
            command_runner=command_runner,
            kube_context=kube_context,
            values=values,
        )
        _recreate_target_worker_statefulsets(
            command_runner=command_runner,
            kube_context=kube_context,
            values=values,
        )
        _wait_for_target_worker_nodesets_ready(
            command_runner=command_runner,
            kube_context=kube_context,
            values=values,
            timeout_seconds=1800,
        )
    except Exception:
        _resume_slurm_partitions(
            command_runner=command_runner,
            kube_context=kube_context,
        )
        raise
    _kubectl_rollout_status(
        command_runner=command_runner,
        kube_context=kube_context,
        namespace=_SOPERATOR_NAMESPACE,
        resource="deployment/soperator-manager",
        timeout="15m",
    )
    phase["preserved_worker_nodes"] = list(nodes)
    resume_lines = _resume_slurm_after_cutover(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    mutation_performed = True
    phase["target_values_revision"] = _ROLLING_COMPUTE_VALUES_REVISION
    phase["target_values_applied_at"] = _utc_now()
    phase["slurm_quiet_window"] = "verified"
    return mutation_performed, [
        *lines,
        *quiet_lines,
        *resume_lines,
        "Target Soperator chart values applied to aligned service-role groups and preserved worker groups.",
        "Worker node groups preserved in place; no parallel worker group was created.",
    ]


def _rolling_compute_values_revision(checkpoint: Mapping[str, Any]) -> int:
    rolling = _mapping(_mapping(checkpoint.get("phase_state")).get("rolling-compute-migration"))
    try:
        return int(rolling.get("target_values_revision", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _reapply_stale_rolling_compute_values(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    if _rolling_compute_values_revision(checkpoint) >= _ROLLING_COMPUTE_VALUES_REVISION:
        return False, []
    phase = _phase_state(checkpoint, "rolling-compute-migration")
    lines = _ensure_worker_nodeset_topology_checkpoint(
        checkpoint=checkpoint,
        source_report=source_report,
        kube_context=kube_context,
        command_runner=command_runner,
    )
    values = _patch_target_values_for_compute(
        payload=payload,
        target_ref=target_ref,
        checkpoint=checkpoint,
        source_report=source_report,
        live_snapshot=live_snapshot,
    )
    _delete_conflicting_source_slurm_resources(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        target_ref=target_ref,
    )
    _delete_pending_accounting_pvcs(
        command_runner=command_runner,
        kube_context=kube_context,
        target_ref=target_ref,
    )
    checkpoint_worker_groups = tuple(
        str(group or "") for group in checkpoint.get("worker_node_groups", []) or []
    )
    _reconcile_target_node_storage_labels(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        worker_node_groups=checkpoint_worker_groups,
    )
    _suspend_legacy_flux_helmreleases(
        command_runner=command_runner,
        kube_context=kube_context,
        phase=phase,
    )
    _scale_down_legacy_soperator_controllers(
        command_runner=command_runner,
        kube_context=kube_context,
        phase=phase,
        target_version=str(_mapping(source_report.get("report")).get("target_version", "") or ""),
        profile_group=_source_report_migration_profile_group(source_report),
    )
    _helm_upgrade_target_soperator(
        command_runner=command_runner,
        kube_context=kube_context,
        values=values,
        expected_version=str(_mapping(source_report.get("report")).get("target_version", "") or ""),
        wait=False,
    )
    _clear_worker_nodeset_ephemeral_storage_aliases(
        command_runner=command_runner,
        kube_context=kube_context,
        values=values,
    )
    _recreate_target_worker_statefulsets(
        command_runner=command_runner,
        kube_context=kube_context,
        values=values,
    )
    _wait_for_target_worker_nodesets_ready(
        command_runner=command_runner,
        kube_context=kube_context,
        values=values,
        timeout_seconds=1800,
    )
    _kubectl_rollout_status(
        command_runner=command_runner,
        kube_context=kube_context,
        namespace=_SOPERATOR_NAMESPACE,
        resource="deployment/soperator-manager",
        timeout="15m",
    )
    lines.extend(
        _resume_slurm_after_cutover(
            command_runner=command_runner,
            kube_context=kube_context,
        )
    )
    phase["target_values_revision"] = _ROLLING_COMPUTE_VALUES_REVISION
    phase["target_values_reapplied_at"] = _utc_now()
    lines.append(
        "Target Soperator chart values reapplied for the current compute migration contract."
    )
    return True, lines


def _reconcile_completed_compute_cutover(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    lines: list[str] = []
    mutation_performed = False
    if _rolling_compute_values_revision(checkpoint) < _ROLLING_COMPUTE_VALUES_REVISION:
        phase_mutation, phase_lines = _reapply_stale_rolling_compute_values(
            checkpoint=checkpoint,
            payload=payload,
            source_report=source_report,
            live_snapshot=live_snapshot,
            target_ref=target_ref,
            kube_context=kube_context,
            command_runner=command_runner,
        )
        mutation_performed = mutation_performed or phase_mutation
        lines.extend(phase_lines)
    else:
        values = _patch_target_values_for_compute(
            payload=payload,
            target_ref=target_ref,
            checkpoint=checkpoint,
            source_report=source_report,
            live_snapshot=live_snapshot,
        )
        _wait_for_target_worker_nodesets_ready(
            command_runner=command_runner,
            kube_context=kube_context,
            values=values,
            timeout_seconds=1800,
        )
    source_cleanup = _delete_conflicting_source_slurm_resources(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        target_ref=target_ref,
    )
    if source_cleanup:
        mutation_performed = True
        lines.append("Conflicting source Slurm resources removed for target cutover.")
    phase = _phase_state(checkpoint, "rolling-compute-migration")
    if not phase.get("controller_spool_clustername_cleared_at"):
        _clear_controller_spool_clustername(
            command_runner=command_runner,
            kube_context=kube_context,
        )
        phase["controller_spool_clustername_cleared_at"] = _utc_now()
        mutation_performed = True
        lines.append("Controller spool Slurm cluster-name guard cleared for target cutover.")
    if source_cleanup or mutation_performed:
        _wait_for_target_slurmcluster_available(
            command_runner=command_runner,
            kube_context=kube_context,
            target_ref=target_ref,
            timeout_seconds=900,
        )
    return mutation_performed, lines


def _clear_controller_spool_clustername(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> None:
    job_name = "cxcli-soperator-clear-clustername"
    _kubectl_apply_objects(
        command_runner=command_runner,
        kube_context=kube_context,
        objects=[
            {
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {
                    "name": job_name,
                    "namespace": _SOPERATOR_NAMESPACE,
                    "labels": {
                        "app.kubernetes.io/managed-by": "nebius-cxcli",
                        "nebius-cxcli.io/soperator-migration": "true",
                    },
                },
                "spec": {
                    "backoffLimit": 0,
                    "template": {
                        "spec": {
                            "restartPolicy": "Never",
                            "nodeSelector": {"slurm.nebius.ai/nodeset-name": "controller"},
                            "tolerations": _role_tolerations("controller"),
                            "containers": [
                                {
                                    "name": "clear",
                                    "image": "ubuntu:24.04",
                                    "command": [
                                        "/bin/sh",
                                        "-ceu",
                                        "rm -f /controller-spool/clustername",
                                    ],
                                    "volumeMounts": [
                                        {
                                            "name": "controller-spool",
                                            "mountPath": "/controller-spool",
                                        }
                                    ],
                                }
                            ],
                            "volumes": [
                                {
                                    "name": "controller-spool",
                                    "persistentVolumeClaim": {"claimName": "controller-spool-pvc"},
                                }
                            ],
                        }
                    },
                },
            }
        ],
        timeout_seconds=300,
    )
    _kubectl_wait(
        command_runner=command_runner,
        kube_context=kube_context,
        namespace=_SOPERATOR_NAMESPACE,
        resource=f"job/{job_name}",
        condition="condition=complete",
        timeout="10m",
        timeout_seconds=720,
    )
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "delete",
            "pod",
            "controller-0",
            "--ignore-not-found",
        ],
        timeout_seconds=300,
    )


def _delete_conflicting_source_slurm_resources(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    source_report: Mapping[str, Any],
    target_ref: str,
) -> bool:
    source_names = set(_source_slurmcluster_names(source_report, target_ref=target_ref))
    if not source_names:
        return False
    try:
        result = _json_from_command(
            command_runner,
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "get",
                "slurmclusters",
                "-o",
                "json",
                "--request-timeout=20s",
            ],
            timeout_seconds=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result = {}
    mutation_performed = False
    for item in _sequence_of_mappings(result.get("items")):
        metadata = _mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        if not name or name == target_ref or name not in source_names:
            continue
        mutation_performed = True
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "delete",
                "slurmcluster",
                name,
                "--ignore-not-found",
                "--wait=false",
            ],
            timeout_seconds=300,
        )
    for source_name in sorted(source_names):
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "delete",
                "deployment.apps,statefulset.apps,daemonset.apps,service",
                "-l",
                f"app.kubernetes.io/name=slurmcluster,app.kubernetes.io/instance={source_name}",
                "--ignore-not-found",
                "--wait=true",
                "--timeout=5m",
            ],
            timeout_seconds=420,
            check=False,
        )
    for nodeset_name in _source_worker_nodeset_names(source_report):
        live_nodeset = _json_from_command(
            command_runner,
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "get",
                "nodeset",
                nodeset_name,
                "-o",
                "json",
            ],
            timeout_seconds=120,
            check=False,
        )
        metadata = _mapping(live_nodeset.get("metadata"))
        if not metadata:
            continue
        annotations = _mapping(metadata.get("annotations"))
        release_name = str(annotations.get("meta.helm.sh/release-name", "") or "").strip()
        if release_name == _SOPERATOR_TARGET_RELEASE_NAME:
            continue
        mutation_performed = True
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "delete",
                "nodeset",
                nodeset_name,
                "--ignore-not-found",
                "--wait=true",
                "--timeout=5m",
            ],
            timeout_seconds=420,
            check=False,
        )
    return mutation_performed


def _wait_for_target_slurmcluster_available(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_phase = ""
    while True:
        result = _json_from_command(
            command_runner,
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "get",
                "slurmcluster",
                target_ref,
                "-o",
                "json",
            ],
            timeout_seconds=120,
            check=False,
        )
        if not _mapping(result.get("metadata")):
            last_phase = "missing"
        else:
            status = _mapping(result.get("status"))
            last_phase = str(status.get("phase", "") or "").strip()
            if last_phase == "Available":
                return
        if time.monotonic() >= deadline:
            raise SoperatorMigrationPhasePending(
                f"target SlurmCluster '{target_ref}' did not become Available "
                f"within {timeout_seconds}s; last phase: {last_phase or 'unknown'}."
            )
        time.sleep(10)


def _resource_output_contains(names: Sequence[str], kind: str, name: str) -> bool:
    kind_lower = kind.lower()
    return any(kind_lower in item.lower() and item.rsplit("/", 1)[-1] == name for item in names)


def _expected_cutover_nodesets(checkpoint: Mapping[str, Any]) -> tuple[str, ...]:
    rolling = _mapping(_mapping(checkpoint.get("phase_state")).get("rolling-compute-migration"))
    target_groups = _mapping(rolling.get("target_node_groups"))
    if "worker" in target_groups:
        return ("worker",)
    return ()


def _execute_final_cutover_phase(
    *,
    checkpoint: dict[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "final-control-plane-cutover")
    if not _has_soperator_custom_resources(live_snapshot):
        phase["skipped_reason"] = "no Soperator Slurm custom resources were detected"
        _kubectl_rollout_status(
            command_runner=command_runner,
            kube_context=kube_context,
            namespace=_SOPERATOR_NAMESPACE,
            resource="deployment/soperator-manager",
            timeout="10m",
        )
        return False, [
            "Final cutover skipped: no Slurm custom resources were detected; Soperator manager rollout is healthy."
        ]
    _kubectl_rollout_status(
        command_runner=command_runner,
        kube_context=kube_context,
        namespace=_SOPERATOR_NAMESPACE,
        resource="deployment/soperator-manager",
        timeout="15m",
    )
    _wait_for_target_slurmcluster_available(
        command_runner=command_runner,
        kube_context=kube_context,
        target_ref=target_ref,
        timeout_seconds=900,
    )
    resources = command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "get",
            "slurmclusters,nodesets",
            "-o",
            "name",
        ],
        timeout_seconds=300,
    )
    names = tuple(line.strip() for line in resources.stdout.splitlines() if line.strip())
    if not _resource_output_contains(names, "slurmcluster", target_ref):
        raise SoperatorMigrationPhasePending(
            f"final-control-plane-cutover expected target SlurmCluster '{target_ref}' "
            "after target chart apply, but it was not found."
        )
    missing_nodesets = [
        name
        for name in _expected_cutover_nodesets(checkpoint)
        if not _resource_output_contains(names, "nodeset", name)
    ]
    if missing_nodesets:
        raise SoperatorMigrationPhasePending(
            "final-control-plane-cutover expected target NodeSet resources after "
            "target chart apply, but these were not found: " + ", ".join(missing_nodesets) + "."
        )
    phase["validated_resources"] = list(names)
    phase["cutover_at"] = _utc_now()
    return False, ["Final cutover validated: target Slurm custom resources are present."]


def _target_mk8s_gpu_validation_specs(
    payload: Mapping[str, Any],
    *,
    target_ref: str,
) -> list[dict[str, Any]]:
    normalized_target = normalize_component_token(target_ref) or target_ref
    return [
        dict(spec)
        for spec in mk8s_gpu_validation_specs(payload)
        if normalize_component_token(spec.get("target_ref")) == normalized_target
    ]


def _migration_validation_reports_dir(config_path: Path) -> Path:
    return config_path.resolve().parent / "generated" / "reports"


def _migrate_report_path(config_path: Path) -> Path:
    return _migration_validation_reports_dir(config_path) / MIGRATE_REPORT_FILENAME


def _target_soperator_cluster_validation_specs(
    payload: Mapping[str, Any],
    *,
    target_ref: str,
    kube_context: str,
) -> list[dict[str, Any]]:
    normalized_target = normalize_component_token(target_ref) or target_ref
    specs: list[dict[str, Any]] = []
    for spec in soperator_cluster_validation_specs(payload):
        if normalize_component_token(spec.get("target_ref")) != normalized_target:
            continue
        item = dict(spec)
        item["kube_context"] = kube_context
        if not str(item.get("cluster_name", "") or "").strip():
            item["cluster_name"] = target_ref
        specs.append(item)
    return specs


def _migration_validation_command_runner(
    command_runner: SoperatorMigrationCommandRunner,
) -> Callable[..., SoperatorValidationCommandResult]:
    def _run(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SoperatorValidationCommandResult:
        result = command_runner(
            args,
            input_text=input_text,
            timeout_seconds=timeout_seconds,
            check=check,
        )
        return SoperatorValidationCommandResult(
            args=result.args,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    return _run


def _run_migration_soperator_cluster_validation(
    *,
    config_path: Path,
    payload: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
    phase: dict[str, Any],
) -> list[str]:
    validations = _target_soperator_cluster_validation_specs(
        payload,
        target_ref=target_ref,
        kube_context=kube_context,
    )
    phase["soperator_cluster_validation_count"] = len(validations)
    if not validations:
        phase["soperator_cluster_validations"] = []
        return [
            "Soperator cluster smoke validation skipped: no Soperator app row is configured for this target."
        ]
    reports_dir = _migration_validation_reports_dir(config_path)
    reports_dir.mkdir(parents=True, exist_ok=True)
    messages: list[str] = []
    try:
        reports = run_soperator_cluster_validations(
            validations,
            reports_dir=reports_dir,
            emit=messages.append,
            command_runner=_migration_validation_command_runner(command_runner),
        )
    except Exception as exc:
        phase["soperator_cluster_validations"] = [
            {
                "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
                "name": str(spec.get("name", "") or ""),
                "report_file": str(spec.get("report_file", "") or ""),
            }
            for spec in validations
        ]
        phase["soperator_cluster_validation_reports"] = [
            str(
                reports_dir
                / str(spec.get("report_file") or "soperator-cluster-validation-report.json")
            )
            for spec in validations
        ]
        raise SoperatorMigrationPhasePending(
            "Soperator cluster smoke validation failed during validation-and-rollback-hold: "
            + str(exc)
        ) from exc
    phase["soperator_cluster_validations"] = [
        {
            "kind": SOPERATOR_CLUSTER_VALIDATION_KIND,
            "name": str(spec.get("name", "") or ""),
            "report_file": str(spec.get("report_file", "") or ""),
        }
        for spec in validations
    ]
    phase["soperator_cluster_validation_reports"] = [str(path) for path in reports]
    return [
        *messages,
        f"Soperator cluster smoke validation completed: {len(reports)}/{len(validations)} report(s) written.",
    ]


def _write_validation_only_deploy_report(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    validations: Sequence[Mapping[str, Any]],
    reports_dir: Path,
) -> Path:
    report_path = reports_dir / DEPLOY_REPORT_FILENAME
    report = build_deploy_validation_report(
        validations,
        reports_dir=reports_dir,
        markdown_path=report_path,
    )
    project_id = str(
        _mapping(_mapping(payload.get("client_info")).get("nebius")).get("project_id", "") or ""
    ).strip()
    heading = f"Deploy Report: {project_id}" if project_id else "Deploy Report"
    lines = [
        f"# {heading}",
        "",
        f"- Soperator migration target: `{target_ref}`",
        "",
        *validation_section_lines(report),
    ]
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report_path


def _refresh_migration_deploy_report(
    *,
    config_path: Path,
    payload: Mapping[str, Any],
    target_ref: str,
    validations: Sequence[Mapping[str, Any]],
    reports_dir: Path,
) -> Path:
    try:
        paths = resolve_project_paths(config_path)
    except ValueError:
        return _write_validation_only_deploy_report(
            payload=payload,
            target_ref=target_ref,
            validations=validations,
            reports_dir=reports_dir,
        )
    artifacts = write_inventory(payload, paths, validations=validations)
    return artifacts.markdown


def _run_migration_mk8s_gpu_validations(
    *,
    config_path: Path,
    payload: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    phase: dict[str, Any],
) -> list[str]:
    validations = _target_mk8s_gpu_validation_specs(payload, target_ref=target_ref)
    phase["mk8s_gpu_validation_count"] = len(validations)
    if not validations:
        phase["mk8s_gpu_validations"] = []
        return ["MK8s GPU validations skipped: no enabled target-scoped checks were configured."]
    reports_dir = _migration_validation_reports_dir(config_path)
    reports_dir.mkdir(parents=True, exist_ok=True)
    clear_deploy_validation_artifacts(validations, reports_dir=reports_dir)
    messages: list[str] = []
    try:
        reports = run_mk8s_gpu_validations(
            validations,
            reports_dir=reports_dir,
            extra_env={
                "KUBECTL_CONTEXT": kube_context,
                "HELM_KUBECONTEXT": kube_context,
            },
            emit=messages.append,
        )
    except Exception:
        report_path = _refresh_migration_deploy_report(
            config_path=config_path,
            payload=payload,
            target_ref=target_ref,
            validations=validations,
            reports_dir=reports_dir,
        )
        phase["deploy_report"] = str(report_path)
        raise
    report_path = _refresh_migration_deploy_report(
        config_path=config_path,
        payload=payload,
        target_ref=target_ref,
        validations=validations,
        reports_dir=reports_dir,
    )
    phase["mk8s_gpu_validations"] = [
        {
            "kind": str(spec.get("kind", "") or ""),
            "name": str(spec.get("name", "") or ""),
            "report_file": str(spec.get("report_file", "") or ""),
        }
        for spec in validations
    ]
    phase["mk8s_gpu_validation_reports"] = [str(path) for path in reports]
    phase["deploy_report"] = str(report_path)
    return [
        *messages,
        f"MK8s GPU validations completed: {len(reports)}/{len(validations)} report(s) written.",
        "Migrate report will include the MK8s GPU validation rollup.",
        f"Deploy-compatible validation report refreshed: {report_path}",
    ]


def _validation_hold_revision(checkpoint: Mapping[str, Any]) -> int:
    phase = _mapping(_mapping(checkpoint.get("phase_state")).get("validation-and-rollback-hold"))
    return _positive_int(phase.get("validation_contract_revision"), fallback=0)


def _execute_validation_hold_phase(
    *,
    config_path: Path,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    target_version: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "validation-and-rollback-hold")
    _ensure_live_nodes_ready(live_snapshot)
    _kubectl_rollout_status(
        command_runner=command_runner,
        kube_context=kube_context,
        namespace=_SOPERATOR_NAMESPACE,
        resource="deployment/soperator-manager",
        timeout="10m",
    )
    helm_state_lines = _verify_completed_soperator_migration_helm_state(
        command_runner=command_runner,
        kube_context=kube_context,
        target_version=target_version,
    )
    validation_lines = _run_migration_mk8s_gpu_validations(
        config_path=config_path,
        payload=payload,
        target_ref=target_ref,
        kube_context=kube_context,
        phase=phase,
    )
    soperator_validation_lines = _run_migration_soperator_cluster_validation(
        config_path=config_path,
        payload=payload,
        target_ref=target_ref,
        kube_context=kube_context,
        command_runner=command_runner,
        phase=phase,
    )
    phase["validation_contract_revision"] = _VALIDATION_HOLD_REVISION
    phase["validated_at"] = _utc_now()
    return bool(phase.get("mk8s_gpu_validations") or phase.get("soperator_cluster_validations")), [
        "Validation hold passed: nodes are present and Soperator manager rollout is healthy.",
        *helm_state_lines,
        *validation_lines,
        *soperator_validation_lines,
    ]


def _validation_hold_needs_reconcile(
    checkpoint: Mapping[str, Any],
    completed_phases: set[str],
) -> bool:
    return (
        "validation-and-rollback-hold" in completed_phases
        and _validation_hold_revision(checkpoint) < _VALIDATION_HOLD_REVISION
    )


def _execute_retire_old_resources_phase(
    *,
    checkpoint: dict[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "retire-old-resources")
    rolling = _mapping(_mapping(checkpoint.get("phase_state")).get("rolling-compute-migration"))
    old_groups_raw = _mapping(rolling.get("old_node_groups"))
    old_group_names = tuple(str(name) for name in old_groups_raw if str(name).strip())
    if not old_group_names:
        if _snapshot_storage(source_report):
            raise SoperatorMigrationPhasePending(
                "retire-old-resources requires explicit confirmation after validating old storage "
                "and replaced service-role compute references are no longer active."
            )
        phase["skipped_reason"] = "in-place worker node groups preserved"
        return False, ["Retire old resources skipped: in-place worker node groups are preserved."]

    old_nodes = _nodes_for_source_groups(
        source_report=source_report,
        source_groups=old_group_names,
    )
    if old_nodes:
        _uncordon_or_drain_nodes(
            command_runner=command_runner,
            kube_context=kube_context,
            nodes=old_nodes,
            action="cordon",
        )
        _uncordon_or_drain_nodes(
            command_runner=command_runner,
            kube_context=kube_context,
            nodes=old_nodes,
            action="drain",
        )
    retired: list[dict[str, str]] = []
    for group_name, item in old_groups_raw.items():
        node_group_id = str(_mapping(item).get("id", "") or "").strip()
        if not node_group_id:
            continue
        _scale_node_group(
            command_runner=command_runner,
            node_group_id=node_group_id,
            count=0,
        )
        _delete_node_group(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
        retired.append({"source_group": str(group_name), "node_group_id": node_group_id})
    if not retired:
        raise SoperatorMigrationPhasePending(
            "retire-old-resources found replaced service-role groups in the checkpoint, but no "
            "Nebius node group ids were recorded."
        )
    phase["retired_node_groups"] = retired
    if _snapshot_storage(source_report):
        phase["storage_retirement"] = "held"
    return True, [
        "Retired replaced service-role node groups: "
        + ", ".join(f"{item['source_group']} ({item['node_group_id']})" for item in retired)
    ]


def _load_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Soperator migration checkpoint is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Soperator migration checkpoint must be a JSON object: {path}")
    if payload.get("schema") != SOPERATOR_MIGRATION_EXECUTION_SCHEMA:
        raise RuntimeError(f"Unsupported Soperator migration checkpoint schema in {path}.")
    return payload


def _write_text_atomic(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode(encoding)
    file_mode = _replacement_text_file_mode(path)
    fd = -1
    temp_path: Path | None = None
    try:
        fd, raw_temp_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temp_path = Path(raw_temp_path)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            os.fchmod(handle.fileno(), file_mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _replacement_text_file_mode(path: Path) -> int:
    with suppress(OSError):
        return stat.S_IMODE(path.stat().st_mode)
    return _default_text_file_mode(path.parent, path.name)


def _default_text_file_mode(parent: Path, name: str) -> int:
    for _attempt in range(100):
        probe_path = parent / f".{name}.{uuid4().hex}.mode"
        fd = -1
        try:
            fd = os.open(probe_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            return stat.S_IMODE(os.fstat(fd).st_mode)
        except FileExistsError:
            continue
        finally:
            if fd >= 0:
                os.close(fd)
                probe_path.unlink(missing_ok=True)
    return 0o644


def _write_checkpoint(path: Path, checkpoint: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(to_plain_data(checkpoint), indent=2, sort_keys=True) + "\n")


def _clear_completed_pending_phase(checkpoint: dict[str, Any], phase_id: str) -> bool:
    if str(checkpoint.get("pending_phase", "") or "") != phase_id:
        return False
    checkpoint["pending_phase"] = "none"
    checkpoint["pending_reason"] = ""
    _append_event(checkpoint, "execute-pending-cleared", phase=phase_id)
    return True


def _phase_report_status(
    phase_id: str,
    *,
    completed_phases: set[str],
    pending_phase: str,
) -> str:
    if phase_id in completed_phases:
        return "passed"
    if pending_phase == phase_id:
        return "pending"
    return "not_run"


def _phase_report_summary(phase_id: str, phase: Mapping[str, Any]) -> str:
    if not phase:
        return "No phase state recorded."
    if phase_id == _EXTERNAL_NODE_TEMPLATE_PHASE_ID:
        updates = _sequence_of_mappings(phase.get("node_group_updates"))
        return f"external node-template updates recorded: {len(updates)}."
    if phase_id == _TARGET_GPU_STACK_PHASE_ID:
        apps = _sequence_of_mappings(phase.get("apps"))
        return f"target GPU stack app rows applied or verified: {len(apps)}."
    if phase_id == "create-aligned-sfs":
        filesystems = _mapping(phase.get("filesystems"))
        return (
            f"aligned SFS filesystems recorded: {len(filesystems)}/{len(_SOPERATOR_STORAGE_KEYS)}."
        )
    if phase_id == "online-bulk-data-sync":
        jobs = _mapping(phase.get("jobs"))
        return f"data-copy jobs recorded: {len(jobs)}."
    if phase_id == "rolling-compute-migration":
        target_groups = _mapping(phase.get("target_node_groups"))
        workers = phase.get("in_place_worker_node_groups")
        worker_count = (
            len(workers)
            if isinstance(workers, Sequence) and not isinstance(workers, (str, bytes, bytearray))
            else 0
        )
        return (
            f"service-role target groups recorded: {len(target_groups)}; "
            f"in-place worker groups preserved: {worker_count}."
        )
    if phase_id == "final-control-plane-cutover":
        target = str(
            phase.get("target_slurmcluster", "") or phase.get("target_ref", "") or ""
        ).strip()
        return f"target Soperator chart and SlurmCluster cutover validated{f' for {target}' if target else ''}."
    if phase_id == "validation-and-rollback-hold":
        gpu_count = _positive_int(phase.get("mk8s_gpu_validation_count"), fallback=0)
        soperator_count = _positive_int(phase.get("soperator_cluster_validation_count"), fallback=0)
        return (
            f"validation checks recorded: MK8s GPU={gpu_count}, Soperator/Slurm={soperator_count}."
        )
    if phase_id == "retire-old-resources":
        retired = _sequence_of_mappings(phase.get("retired_node_groups"))
        skipped = str(phase.get("skipped_reason", "") or "").strip()
        if skipped:
            return f"retirement skipped: {skipped}."
        return f"retired replaced node groups: {len(retired)}."
    return "Phase state recorded."


def _read_validation_report_summary(path: Path) -> tuple[str, str, tuple[Mapping[str, Any], ...]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return "failed", f"Report could not be loaded: {exc}", ()
    if not isinstance(payload, Mapping):
        return "failed", "Report is not a JSON object.", ()
    checks = _sequence_of_mappings(payload.get("checks"))
    return (
        str(payload.get("status", "") or "not_run"),
        str(payload.get("summary", "") or ""),
        checks,
    )


def _validation_report_lines(
    *,
    title: str,
    report_paths: Sequence[str],
) -> list[str]:
    lines = [f"### {title}", ""]
    if not report_paths:
        lines.extend(["- No reports recorded.", ""])
        return lines
    for raw_path in report_paths:
        path = Path(raw_path)
        status, summary, checks = _read_validation_report_summary(path)
        lines.extend(
            [
                f"- `{path.name}`: `{status_label(status)}` - {summary or 'No summary recorded.'}",
            ]
        )
        for check in checks:
            check_name = str(check.get("name", "") or "check")
            check_status = str(check.get("status", "") or "not_run")
            check_summary = str(check.get("summary", "") or "")
            lines.append(f"  - `{status_label(check_status)}` {check_name}: {check_summary}")
    lines.append("")
    return lines


def _mk8s_gpu_validation_report_lines(
    *,
    validations: Sequence[Mapping[str, Any]],
    reports_dir: Path,
    fallback_report_paths: Sequence[str],
) -> list[str]:
    if not validations:
        return _validation_report_lines(
            title="MK8s GPU",
            report_paths=fallback_report_paths,
        )
    report = build_deploy_validation_report(validations, reports_dir=reports_dir)
    lines = ["### MK8s GPU", ""]
    for item in report.results:
        detail_name = item.report_path.name if item.report_exists else "n/a"
        lines.append(
            f"- `{detail_name}`: `{status_label(item.status)}` - "
            f"{item.summary or 'No summary recorded.'}"
        )
        for check in item.checks:
            lines.append(f"  - `{status_label(check.status)}` {check.name}: {check.summary}")
    lines.append("")
    return lines


def _write_soperator_migrate_report(
    *,
    config_path: Path,
    checkpoint_path: Path,
    checkpoint: Mapping[str, Any],
    phase_ids: Sequence[str],
    completed_phases: set[str],
    target_ref: str,
    source_version: str,
    target_version: str,
    pending_phase: str,
    pending_reason: str,
    mutation_performed: bool,
) -> Path:
    report_path = _migrate_report_path(config_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    phase_state = _mapping(checkpoint.get("phase_state"))
    lines = [
        "# Soperator Migration Report",
        "",
        f"- Target: `{target_ref}`",
        f"- Source version: `{source_version or 'unknown'}`",
        f"- Target version: `{target_version or 'unknown'}`",
        f"- Generated at: `{_utc_now()}`",
        f"- Checkpoint: `{checkpoint_path}`",
        f"- Pending phase: `{pending_phase or 'none'}`",
        f"- Pending reason: `{pending_reason or 'none'}`",
        "- Migration performed: `" + ("yes" if mutation_performed else "no") + "`",
        "",
        "## Migration Steps",
        "",
    ]
    for phase_id in phase_ids:
        status = _phase_report_status(
            phase_id,
            completed_phases=completed_phases,
            pending_phase=pending_phase,
        )
        phase = _mapping(phase_state.get(phase_id))
        lines.extend(
            [
                f"### {phase_id}",
                "",
                f"- Status: `{status_label(status)}`",
                f"- Summary: {_phase_report_summary(phase_id, phase)}",
                "",
            ]
        )
    validation_phase = _mapping(phase_state.get("validation-and-rollback-hold"))
    lines.extend(["## Validations", ""])
    lines.extend(
        _validation_report_lines(
            title="Soperator and Slurm smoke",
            report_paths=[
                str(path)
                for path in validation_phase.get("soperator_cluster_validation_reports", []) or []
                if str(path).strip()
            ],
        )
    )
    lines.extend(
        _mk8s_gpu_validation_report_lines(
            validations=[
                dict(item)
                for item in validation_phase.get("mk8s_gpu_validations", []) or []
                if isinstance(item, Mapping)
            ],
            reports_dir=report_path.parent,
            fallback_report_paths=[
                str(path)
                for path in validation_phase.get("mk8s_gpu_validation_reports", []) or []
                if str(path).strip()
            ],
        )
    )
    events = _sequence_of_mappings(checkpoint.get("events"))
    lines.extend(["## Event Log", ""])
    if events:
        for event in events:
            timestamp = str(event.get("at", "") or "")
            name = str(event.get("event", "") or "event")
            phase = str(event.get("phase", "") or event.get("pending_phase", "") or "")
            suffix = f" ({phase})" if phase else ""
            lines.append(f"- `{timestamp}` {name}{suffix}")
    else:
        lines.append("- No checkpoint events recorded.")
    lines.append("")
    _write_text_atomic(report_path, "\n".join(lines).rstrip() + "\n")
    return report_path


def _checkpoint_for_run(
    *,
    existing: Mapping[str, Any] | None,
    target_ref: str,
    source_report_fingerprint: str,
    source_version: str,
    target_version: str,
    phase_ids: Sequence[str],
    allow_source_report_refresh: bool = False,
) -> dict[str, Any]:
    source_report_refreshed = False
    previous_source_report_fingerprint = ""
    if existing is not None:
        if str(existing.get("target_ref", "") or "") != target_ref:
            raise RuntimeError("Soperator migration checkpoint belongs to a different target.")
        existing_source_version = str(existing.get("source_version", "") or "").strip()
        existing_target_version = str(existing.get("target_version", "") or "").strip()
        existing_planned_phases = tuple(
            str(phase or "").strip()
            for phase in existing.get("planned_phases", []) or []
            if str(phase or "").strip()
        )
        if str(existing.get("source_report_fingerprint", "") or "") != source_report_fingerprint:
            same_resume_contract = (
                allow_source_report_refresh
                and existing_planned_phases == tuple(phase_ids)
                and (not existing_source_version or existing_source_version == source_version)
                and (not existing_target_version or existing_target_version == target_version)
            )
            if not same_resume_contract:
                raise RuntimeError(
                    "Soperator migration checkpoint is stale because the source discovery report changed. "
                    "Review the new report and remove the old checkpoint before executing."
                )
            previous_source_report_fingerprint = str(
                existing.get("source_report_fingerprint", "") or ""
            )
            source_report_refreshed = True
        completed = {
            str(phase or "").strip()
            for phase in existing.get("completed_phases", []) or []
            if str(phase or "").strip()
        }
        unsupported_completed = sorted(completed - _SUPPORTED_EXECUTE_PHASE_IDS)
        if unsupported_completed:
            raise RuntimeError(
                "Soperator migration checkpoint contains completed phase(s) that this "
                "executor cannot resume safely: "
                + ", ".join(unsupported_completed)
                + ". Review or remove the checkpoint before executing."
            )
        checkpoint = dict(existing)
        if source_report_refreshed:
            checkpoint["source_report_fingerprint"] = source_report_fingerprint
    else:
        checkpoint = {
            "schema": SOPERATOR_MIGRATION_EXECUTION_SCHEMA,
            "target_ref": target_ref,
            "source_report_fingerprint": source_report_fingerprint,
            "source_version": source_version,
            "target_version": target_version,
            "created_at": _utc_now(),
            "completed_phases": [],
            "events": [],
        }
    checkpoint["updated_at"] = _utc_now()
    checkpoint["planned_phases"] = list(phase_ids)
    if source_report_refreshed:
        events = list(checkpoint.get("events", []) or [])
        events.append(
            {
                "at": _utc_now(),
                "event": "execute-checkpoint-source-report-refreshed",
                "previous_source_report_fingerprint": previous_source_report_fingerprint,
                "source_report_fingerprint": source_report_fingerprint,
            }
        )
        checkpoint["events"] = events
    return checkpoint


def _checkpoint_has_mutating_progress(checkpoint: Mapping[str, Any] | None) -> bool:
    phase_state = _mapping((checkpoint or {}).get("phase_state"))
    for phase_id in _MUTATING_PHASE_IDS:
        state = phase_state.get(phase_id)
        if isinstance(state, Mapping) and bool(state):
            return True
    return False


def _target_resume_versions(target_version: str) -> set[str]:
    versions: set[str] = set()
    normalized = normalize_soperator_release_version(target_version)
    if normalized:
        versions.add(normalized)
    for marker in ("-ps.", "+"):
        base = str(target_version or "").split(marker, 1)[0]
        normalized_base = normalize_soperator_release_version(base)
        if normalized_base:
            versions.add(normalized_base)
    return versions


def _profile_source_selector_rows() -> tuple[Mapping[str, Any], ...]:
    with suppress(Exception):
        payload = yaml.safe_load(SOPERATOR_MIGRATION_PROFILE_DATA_FILE.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        return ()
    releases = payload.get("releases")
    if not isinstance(releases, Sequence) or isinstance(releases, (str, bytes, bytearray)):
        return ()
    rows: list[Mapping[str, Any]] = []
    for release in releases:
        if not isinstance(release, Mapping):
            continue
        rows.append(release)
        components = release.get("component_contracts")
        if not isinstance(components, Sequence) or isinstance(
            components,
            (str, bytes, bytearray),
        ):
            continue
        rows.extend(component for component in components if isinstance(component, Mapping))
    return tuple(rows)


def _source_release_aliases_from_chart_name(chart_name: str) -> tuple[str, ...]:
    name = str(chart_name or "").strip().lower()
    if not name:
        return ()
    aliases = {name}
    if name.startswith("helm-"):
        aliases.add(name.removeprefix("helm-"))
    return tuple(sorted(aliases))


def _source_fluxcd_template_release_aliases(row: Mapping[str, Any]) -> tuple[str, ...]:
    chart_path = str(row.get("chart_path", "") or "").strip()
    if chart_path != "helm/soperator-fluxcd":
        return ()
    templates = _mapping(row.get("templates")).get("files")
    if not isinstance(templates, Sequence) or isinstance(templates, (str, bytes, bytearray)):
        return ()
    aliases: set[str] = set()
    for item in templates:
        template = str(item or "").strip()
        if not template.startswith("templates/") or not template.endswith((".yaml", ".yml")):
            continue
        stem = Path(template).stem.strip().lower()
        if not stem or stem.startswith("_"):
            continue
        aliases.add(f"flux-system-soperator-fluxcd-{stem}")
        if stem == "namespaces":
            aliases.add("flux-system-soperator-fluxcd-ns")
    return tuple(sorted(aliases))


@lru_cache(maxsize=1)
def _soperator_source_release_selectors() -> tuple[frozenset[str], frozenset[str], tuple[str, ...]]:
    release_names = {name.lower() for name in _SOPERATOR_SOURCE_RELEASE_NAMES}
    chart_prefixes = {prefix.lower() for prefix in _SOPERATOR_SOURCE_CHART_PREFIXES}
    release_prefixes = tuple(prefix.lower() for prefix in _SOPERATOR_SOURCE_RELEASE_NAME_PREFIXES)
    for row in _profile_source_selector_rows():
        component_id = str(row.get("id", "") or "").strip().lower()
        if component_id:
            release_names.add(component_id)
        chart_name = str(row.get("chart_name", "") or "").strip().lower()
        if chart_name:
            chart_prefixes.add(chart_name)
            release_names.update(_source_release_aliases_from_chart_name(chart_name))
        release_names.update(_source_fluxcd_template_release_aliases(row))
    return (
        frozenset(item for item in release_names if item),
        frozenset(item for item in chart_prefixes if item),
        release_prefixes,
    )


def _source_chart_matches_profile(chart: str) -> bool:
    chart_text = str(chart or "").strip().lower()
    if not chart_text:
        return False
    _release_names, chart_prefixes, _release_prefixes = _soperator_source_release_selectors()
    return any(
        chart_text == prefix or chart_text.startswith(f"{prefix}-")
        for prefix in chart_prefixes
    )


def _source_release_name_matches_profile(name: str) -> bool:
    name_text = str(name or "").strip().lower()
    if not name_text:
        return False
    release_names, _chart_prefixes, release_prefixes = _soperator_source_release_selectors()
    return name_text in release_names or any(
        name_text.startswith(prefix) for prefix in release_prefixes
    )


def _soperator_target_release_record(release: Any, *, target_versions: set[str]) -> bool:
    name = str(getattr(release, "name", "") or "").strip().lower()
    namespace = str(getattr(release, "namespace", "") or "").strip()
    if name != _SOPERATOR_TARGET_RELEASE_NAME or namespace != _SOPERATOR_NAMESPACE:
        return False
    chart = str(getattr(release, "chart", "") or "").strip().lower()
    if not chart.startswith(f"{_SOPERATOR_TARGET_RELEASE_NAME}-"):
        return False
    release_versions = _soperator_release_versions(release)
    return not release_versions or release_versions.issubset(target_versions)


def _helm_release_deployed(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    release_name: str,
    namespace: str,
) -> bool:
    result = command_runner(
        [
            "helm",
            "--kube-context",
            kube_context,
            "status",
            release_name,
            "-n",
            namespace,
            "-o",
            "json",
        ],
        timeout_seconds=120,
        check=False,
    )
    if result.returncode != 0:
        return False
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, Mapping):
        return False
    status = (
        str(_mapping(payload.get("info")).get("status") or payload.get("status") or "")
        .strip()
        .lower()
    )
    return status == "deployed"


def _soperator_source_release_family(release: Any) -> bool:
    name = str(getattr(release, "name", "") or "").strip().lower()
    chart = str(getattr(release, "chart", "") or "").strip().lower()
    source_chart = _source_chart_matches_profile(chart)
    if (
        name == _SOPERATOR_TARGET_RELEASE_NAME
        and str(getattr(release, "namespace", "") or "").strip() == _SOPERATOR_NAMESPACE
    ):
        return source_chart
    if _source_release_name_matches_profile(name):
        return True
    return source_chart


def _soperator_release_versions(release: Any) -> set[str]:
    versions: set[str] = set()
    for value in (
        getattr(release, "chart", ""),
        getattr(release, "chart_version", ""),
        getattr(release, "app_version", ""),
    ):
        normalized = normalize_soperator_release_version(str(value or ""))
        if normalized:
            versions.add(normalized)
    return versions


def _deployed_stale_source_soperator_releases(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_version: str,
) -> tuple[Any, ...]:
    target_versions = _target_resume_versions(target_version)
    stale: list[Any] = []
    releases: list[Any] = []
    for namespace in _SOPERATOR_SOURCE_RELEASE_NAMESPACES:
        try:
            releases.extend(
                list_helm_releases(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    namespace=namespace,
                    timeout_seconds=30,
                )
            )
        except subprocess.TimeoutExpired:
            continue
    for release in releases:
        if str(getattr(release, "status", "") or "").strip().lower() != "deployed":
            continue
        if _soperator_target_release_record(release, target_versions=target_versions):
            continue
        if not _soperator_source_release_family(release):
            continue
        stale.append(release)
    return tuple(stale)


def _release_key(namespace: Any, name: Any) -> tuple[str, str]:
    return (str(namespace or "").strip(), str(name or "").strip())


def _release_sort_key(release: Any) -> tuple[int, str, str]:
    name = str(getattr(release, "name", "") or "").strip()
    try:
        order = _SOPERATOR_SOURCE_RELEASE_RETIRE_ORDER.index(name)
    except ValueError:
        order = len(_SOPERATOR_SOURCE_RELEASE_RETIRE_ORDER)
    namespace = str(getattr(release, "namespace", "") or "").strip()
    return (order, namespace, name)


def _kubectl_json_or_empty(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    command: Sequence[str],
    description: str,
    timeout_seconds: int = 30,
    request_timeout: str = "20s",
) -> Mapping[str, Any]:
    bounded_command = list(command)
    if (
        bounded_command
        and bounded_command[0] == "kubectl"
        and request_timeout
        and not any(str(item).startswith("--request-timeout=") for item in bounded_command)
    ):
        bounded_command.append(f"--request-timeout={request_timeout}")
    try:
        result = command_runner(bounded_command, timeout_seconds=timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        return {}
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{description} returned invalid JSON: {exc}") from exc
    return payload if isinstance(payload, Mapping) else {}


def _source_flux_helmrelease_matches_release(
    item: Mapping[str, Any],
    release_keys: set[tuple[str, str]],
) -> bool:
    if not release_keys:
        return False
    metadata = _mapping(item.get("metadata"))
    spec = _mapping(item.get("spec"))
    status = _mapping(item.get("status"))
    target_namespace = str(spec.get("targetNamespace", "") or metadata.get("namespace", "") or "")
    release_name = str(spec.get("releaseName", "") or metadata.get("name", "") or "")
    if _release_key(target_namespace, release_name) in release_keys:
        return True
    for history in _sequence_of_mappings(status.get("history")):
        if _release_key(history.get("namespace"), history.get("name")) in release_keys:
            return True
    return False


def _source_flux_helmrelease_chart_family(
    item: Mapping[str, Any],
    *,
    target_version: str,
) -> bool:
    spec = _mapping(item.get("spec"))
    chart_spec = _mapping(_mapping(spec.get("chart")).get("spec"))
    chart_name = str(chart_spec.get("chart", "") or "").strip().lower()
    if not _source_chart_matches_profile(chart_name):
        return False
    target_versions = _target_resume_versions(target_version)
    release_versions: set[str] = set()
    for value in (chart_spec.get("version"), chart_spec.get("appVersion")):
        normalized = normalize_soperator_release_version(str(value or ""))
        if normalized:
            release_versions.add(normalized)
    for history in _sequence_of_mappings(_mapping(item.get("status")).get("history")):
        for value in (history.get("chartVersion"), history.get("appVersion")):
            normalized = normalize_soperator_release_version(str(value or ""))
            if normalized:
                release_versions.add(normalized)
    return not release_versions or not release_versions.issubset(target_versions)


def _source_flux_helmrelease_candidates(
    *,
    payload: Mapping[str, Any],
    stale_releases: Sequence[Any],
    target_version: str,
    include_suspended: bool,
) -> tuple[Mapping[str, Any], ...]:
    release_keys = {
        _release_key(getattr(release, "namespace", ""), getattr(release, "name", ""))
        for release in stale_releases
    }
    candidates: list[Mapping[str, Any]] = []
    for item in _sequence_of_mappings(payload.get("items")):
        metadata = _mapping(item.get("metadata"))
        spec = _mapping(item.get("spec"))
        if not include_suspended and bool(spec.get("suspend")):
            continue
        name = str(metadata.get("name", "") or "").strip()
        if (
            _legacy_flux_helmrelease_name(name)
            or _source_flux_helmrelease_matches_release(
                item,
                release_keys,
            )
            or _source_flux_helmrelease_chart_family(item, target_version=target_version)
        ):
            candidates.append(item)
    return tuple(candidates)


def _source_flux_helmrelease_suspend_candidates(
    *,
    payload: Mapping[str, Any],
    stale_releases: Sequence[Any],
    target_version: str,
) -> tuple[Mapping[str, Any], ...]:
    return _source_flux_helmrelease_candidates(
        payload=payload,
        stale_releases=stale_releases,
        target_version=target_version,
        include_suspended=False,
    )


def _source_flux_helmrelease_retire_candidates(
    *,
    payload: Mapping[str, Any],
    stale_releases: Sequence[Any],
    target_version: str,
) -> tuple[Mapping[str, Any], ...]:
    return _source_flux_helmrelease_candidates(
        payload=payload,
        stale_releases=stale_releases,
        target_version=target_version,
        include_suspended=True,
    )


def _kubectl_patch_ignore_not_found(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    command: Sequence[str],
    timeout_seconds: int = 120,
) -> bool:
    result = command_runner(command, timeout_seconds=timeout_seconds, check=False)
    if result.returncode == 0:
        return True
    if _command_not_found(result):
        return False
    raise RuntimeError(f"{_command_text(result.args)} failed: {_command_detail(result)}")


def _delete_source_flux_helmreleases(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    stale_releases: Sequence[Any],
    target_version: str,
) -> tuple[str, ...]:
    payload = _kubectl_json_or_empty(
        command_runner=command_runner,
        command=[
            "kubectl",
            "--context",
            kube_context,
            "get",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-A",
            "-o",
            "json",
        ],
        description="kubectl get HelmRelease",
    )
    candidates = _source_flux_helmrelease_retire_candidates(
        payload=payload,
        stale_releases=stale_releases,
        target_version=target_version,
    )
    deleted: list[str] = []
    for item in candidates:
        metadata = _mapping(item.get("metadata"))
        namespace = str(metadata.get("namespace", "") or "").strip()
        name = str(metadata.get("name", "") or "").strip()
        if not namespace or not name:
            continue
        spec = _mapping(item.get("spec"))
        if not _bool_value(spec.get("suspend"), fallback=False):
            _kubectl_patch_ignore_not_found(
                command_runner=command_runner,
                command=[
                    "kubectl",
                    "--context",
                    kube_context,
                    "-n",
                    namespace,
                    "patch",
                    "helmrelease.helm.toolkit.fluxcd.io",
                    name,
                    "--type=merge",
                    "-p",
                    json.dumps({"spec": {"suspend": True}}, separators=(",", ":")),
                ],
            )
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                namespace,
                "delete",
                "helmrelease.helm.toolkit.fluxcd.io",
                name,
                "--ignore-not-found=true",
                "--wait=false",
            ],
            timeout_seconds=60,
        )
        deleted.append(f"{namespace}/{name}")
    return tuple(sorted(dict.fromkeys(deleted)))


def _source_flux_helmrelease_kustomization_refs(
    items: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, str], ...]:
    refs: list[tuple[str, str]] = []
    for item in items:
        labels = _mapping(_mapping(item.get("metadata")).get("labels"))
        name = str(labels.get("kustomize.toolkit.fluxcd.io/name", "") or "").strip()
        namespace = str(
            labels.get("kustomize.toolkit.fluxcd.io/namespace", "") or ""
        ).strip()
        if name and namespace:
            refs.append((namespace, name))
    return tuple(sorted(dict.fromkeys(refs)))


def _suspend_source_flux_kustomizations(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    items: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    suspended: list[str] = []
    for namespace, name in _source_flux_helmrelease_kustomization_refs(items):
        patched = _kubectl_patch_ignore_not_found(
            command_runner=command_runner,
            command=[
                "kubectl",
                "--context",
                kube_context,
                "-n",
                namespace,
                "patch",
                "kustomization.kustomize.toolkit.fluxcd.io",
                name,
                "--type=merge",
                "-p",
                json.dumps({"spec": {"suspend": True}}, separators=(",", ":")),
            ],
        )
        if patched:
            suspended.append(f"{namespace}/{name}")
    return tuple(suspended)


def _suspend_source_flux_helmreleases(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    stale_releases: Sequence[Any],
    target_version: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    payload = _kubectl_json_or_empty(
        command_runner=command_runner,
        command=[
            "kubectl",
            "--context",
            kube_context,
            "get",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-A",
            "-o",
            "json",
        ],
        description="kubectl get HelmRelease",
    )
    retire_candidates = _source_flux_helmrelease_retire_candidates(
        payload=payload,
        stale_releases=stale_releases,
        target_version=target_version,
    )
    suspended_kustomizations = _suspend_source_flux_kustomizations(
        command_runner=command_runner,
        kube_context=kube_context,
        items=retire_candidates,
    )
    candidates = _source_flux_helmrelease_suspend_candidates(
        payload=payload,
        stale_releases=stale_releases,
        target_version=target_version,
    )
    suspended: list[str] = []
    for item in candidates:
        metadata = _mapping(item.get("metadata"))
        namespace = str(metadata.get("namespace", "") or "").strip()
        name = str(metadata.get("name", "") or "").strip()
        if not namespace or not name:
            continue
        patched = _kubectl_patch_ignore_not_found(
            command_runner=command_runner,
            command=[
                "kubectl",
                "--context",
                kube_context,
                "-n",
                namespace,
                "patch",
                "helmrelease.helm.toolkit.fluxcd.io",
                name,
                "--type=merge",
                "-p",
                json.dumps({"spec": {"suspend": True}}, separators=(",", ":")),
            ],
        )
        if patched:
            suspended.append(f"{namespace}/{name}")
    return (
        tuple(sorted(dict.fromkeys(suspended))),
        tuple(sorted(dict.fromkeys(suspended_kustomizations))),
    )


def _active_source_flux_helmreleases(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_version: str,
) -> tuple[Mapping[str, Any], ...]:
    payload = _kubectl_json_or_empty(
        command_runner=command_runner,
        command=[
            "kubectl",
            "--context",
            kube_context,
            "get",
            "helmreleases.helm.toolkit.fluxcd.io",
            "-A",
            "-o",
            "json",
        ],
        description="kubectl get HelmRelease",
    )
    return _source_flux_helmrelease_suspend_candidates(
        payload=payload,
        stale_releases=(),
        target_version=target_version,
    )


def _resource_annotation_release_name(item: Mapping[str, Any]) -> str:
    metadata = _mapping(item.get("metadata"))
    annotations = _mapping(metadata.get("annotations"))
    return str(annotations.get("meta.helm.sh/release-name", "") or "").strip()


def _delete_kubernetes_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource_type: str,
    name: str,
    namespace: str = "",
) -> None:
    command = ["kubectl", "--context", kube_context]
    if namespace:
        command.extend(["-n", namespace])
    command.extend(
        [
            "delete",
            resource_type,
            name,
            "--ignore-not-found=true",
            "--wait=false",
        ]
    )
    command_runner(command, timeout_seconds=60)


def _prune_safe_source_soperator_resources(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    stale_releases: Sequence[Any],
) -> tuple[str, ...]:
    safe_names = {
        str(getattr(release, "name", "") or "").strip()
        for release in stale_releases
        if str(getattr(release, "name", "") or "").strip()
        in _SOPERATOR_SOURCE_SAFE_PRUNE_RELEASE_NAMES
    }
    if not safe_names:
        return ()
    deleted: list[str] = []
    for resource_type in _SOPERATOR_SOURCE_SAFE_NAMESPACED_RESOURCE_TYPES:
        payload = _kubectl_json_or_empty(
            command_runner=command_runner,
            command=[
                "kubectl",
                "--context",
                kube_context,
                "get",
                resource_type,
                "-A",
                "-o",
                "json",
            ],
            description=f"kubectl get {resource_type}",
        )
        for item in _sequence_of_mappings(payload.get("items")):
            release_name = _resource_annotation_release_name(item)
            if release_name not in safe_names:
                continue
            metadata = _mapping(item.get("metadata"))
            namespace = str(metadata.get("namespace", "") or "").strip()
            name = str(metadata.get("name", "") or "").strip()
            if not namespace or not name:
                continue
            _delete_kubernetes_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource_type=resource_type,
                namespace=namespace,
                name=name,
            )
            deleted.append(f"{resource_type}/{namespace}/{name}")
    for resource_type in _SOPERATOR_SOURCE_SAFE_CLUSTER_RESOURCE_TYPES:
        payload = _kubectl_json_or_empty(
            command_runner=command_runner,
            command=[
                "kubectl",
                "--context",
                kube_context,
                "get",
                resource_type,
                "-o",
                "json",
            ],
            description=f"kubectl get {resource_type}",
        )
        for item in _sequence_of_mappings(payload.get("items")):
            release_name = _resource_annotation_release_name(item)
            if release_name not in safe_names:
                continue
            metadata = _mapping(item.get("metadata"))
            name = str(metadata.get("name", "") or "").strip()
            if not name:
                continue
            _delete_kubernetes_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource_type=resource_type,
                name=name,
            )
            deleted.append(f"{resource_type}/{name}")
    return tuple(sorted(dict.fromkeys(deleted)))


def _delete_stale_helm_storage_records(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    stale_releases: Sequence[Any],
) -> tuple[str, ...]:
    exact_stale_revisions = {
        (
            str(getattr(release, "name", "") or "").strip(),
            str(getattr(release, "revision", "") or "").strip(),
        )
        for release in stale_releases
        if str(getattr(release, "name", "") or "").strip()
        and str(getattr(release, "revision", "") or "").strip()
    }
    stale_names_without_revision = {
        str(getattr(release, "name", "") or "").strip()
        for release in stale_releases
        if str(getattr(release, "name", "") or "").strip()
        and not str(getattr(release, "revision", "") or "").strip()
        and str(getattr(release, "name", "") or "").strip() != _SOPERATOR_TARGET_RELEASE_NAME
    }
    if not exact_stale_revisions and not stale_names_without_revision:
        return ()
    payload = _kubectl_json_or_empty(
        command_runner=command_runner,
        command=[
            "kubectl",
            "--context",
            kube_context,
            "get",
            "secrets",
            "-A",
            "-l",
            "owner=helm",
            "-o",
            "json",
        ],
        description="kubectl get Helm storage secrets",
    )
    deleted: list[str] = []
    for item in _sequence_of_mappings(payload.get("items")):
        metadata = _mapping(item.get("metadata"))
        labels = _mapping(metadata.get("labels"))
        release_name = str(labels.get("name", "") or "").strip()
        revision = str(labels.get("version", "") or "").strip()
        if (
            (release_name, revision) not in exact_stale_revisions
            and release_name not in stale_names_without_revision
        ):
            continue
        namespace = str(metadata.get("namespace", "") or "").strip()
        name = str(metadata.get("name", "") or "").strip()
        if not namespace or not name:
            continue
        command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                namespace,
                "delete",
                "secret",
                name,
                "--ignore-not-found=true",
                "--wait=false",
            ],
            timeout_seconds=120,
        )
        deleted.append(f"{namespace}/{name}")
    return tuple(sorted(dict.fromkeys(deleted)))


def _retire_stale_source_soperator_helm_releases(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_version: str,
) -> tuple[str, ...]:
    stale = tuple(
        sorted(
            _deployed_stale_source_soperator_releases(
                command_runner=command_runner,
                kube_context=kube_context,
                target_version=target_version,
            ),
            key=_release_sort_key,
        )
    )
    suspended, suspended_kustomizations = _suspend_source_flux_helmreleases(
        command_runner=command_runner,
        kube_context=kube_context,
        stale_releases=stale,
        target_version=target_version,
    )
    deleted_flux_helmreleases = _delete_source_flux_helmreleases(
        command_runner=command_runner,
        kube_context=kube_context,
        stale_releases=stale,
        target_version=target_version,
    )
    pruned = _prune_safe_source_soperator_resources(
        command_runner=command_runner,
        kube_context=kube_context,
        stale_releases=stale,
    )
    records = _delete_stale_helm_storage_records(
        command_runner=command_runner,
        kube_context=kube_context,
        stale_releases=stale,
    )
    lines: list[str] = []
    if stale:
        retired_names = ", ".join(
            f"{getattr(release, 'namespace', '')}/{getattr(release, 'name', '')}"
            for release in stale
        )
        lines.append("Retired stale source Soperator Helm release records: " + retired_names + ".")
    elif suspended or suspended_kustomizations or deleted_flux_helmreleases:
        lines.append("No stale source Soperator Helm release records remained.")
    if suspended_kustomizations:
        lines.append(
            "Suspended old Flux Kustomization desired state: "
            + ", ".join(suspended_kustomizations)
            + "."
        )
    if suspended:
        lines.append("Suspended old Flux HelmRelease desired state: " + ", ".join(suspended) + ".")
    if deleted_flux_helmreleases:
        lines.append(
            "Deleted old Flux HelmRelease desired state records: "
            + ", ".join(deleted_flux_helmreleases)
            + "."
        )
    if pruned:
        lines.append(f"Pruned {len(pruned)} old source operational resource(s).")
    protected = [
        str(getattr(release, "name", "") or "").strip()
        for release in stale
        if str(getattr(release, "name", "") or "").strip()
        not in _SOPERATOR_SOURCE_SAFE_PRUNE_RELEASE_NAMES
    ]
    if protected:
        lines.append(
            "Preserved shared/storage/custom resources for source release(s): "
            + ", ".join(sorted(dict.fromkeys(protected)))
            + "."
        )
    if records:
        lines.append(f"Deleted {len(records)} stale Helm storage record(s).")
    elif stale:
        lines.append("No stale Helm storage records were found to delete.")
    return tuple(lines)


def _verify_completed_soperator_migration_helm_state(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_version: str,
) -> tuple[str, ...]:
    retirement_lines = _retire_stale_source_soperator_helm_releases(
        command_runner=command_runner,
        kube_context=kube_context,
        target_version=target_version,
    )
    target_readiness = verify_helm_chart_ready(
        command_runner=command_runner,
        kube_context=kube_context,
        release_name=_SOPERATOR_TARGET_RELEASE_NAME,
        namespace=_SOPERATOR_NAMESPACE,
        expected_version=target_version,
    )
    target_line = "Verified target Soperator Helm chart readiness: " + target_readiness.summary()
    stale = _deployed_stale_source_soperator_releases(
        command_runner=command_runner,
        kube_context=kube_context,
        target_version=target_version,
    )
    active_source_helmreleases = _active_source_flux_helmreleases(
        command_runner=command_runner,
        kube_context=kube_context,
        target_version=target_version,
    )
    if stale:
        stale_lines = [
            (
                f"- {release.namespace}/{release.name}: chart={release.chart or 'unknown'} "
                f"app={release.app_version or 'unknown'} status={release.status or 'unknown'}"
            )
            for release in stale
        ]
        raise RuntimeError(
            target_line
            + ("\n" + "\n".join(retirement_lines) if retirement_lines else "")
            + "\nStale source Soperator Helm releases remain after migration:\n"
            + "\n".join(stale_lines)
            + "\nRetire or remove the remaining old source releases before considering "
            "the migration complete."
        )
    if active_source_helmreleases:
        active_lines = []
        for item in active_source_helmreleases:
            metadata = _mapping(item.get("metadata"))
            spec = _mapping(item.get("spec"))
            chart_spec = _mapping(_mapping(spec.get("chart")).get("spec"))
            namespace = str(metadata.get("namespace", "") or "").strip()
            name = str(metadata.get("name", "") or "").strip()
            active_lines.append(
                "- "
                + f"{namespace}/{name}: chart={chart_spec.get('chart') or 'unknown'} "
                + f"version={chart_spec.get('version') or 'unknown'}"
            )
        raise RuntimeError(
            target_line
            + ("\n" + "\n".join(retirement_lines) if retirement_lines else "")
            + "\nActive old source Flux HelmReleases remain after migration:\n"
            + "\n".join(active_lines)
            + "\nSuspend or remove the remaining old source desired state before considering "
            "the migration complete."
        )
    return (target_line, *retirement_lines)


def _helm_state_lines_include_retirement_mutation(lines: Sequence[str]) -> bool:
    mutating_prefixes = (
        "Retired stale source Soperator Helm release records:",
        "Suspended old Flux Kustomization desired state:",
        "Suspended old Flux HelmRelease desired state:",
        "Deleted old Flux HelmRelease desired state records:",
        "Pruned ",
        "Deleted ",
    )
    return any(str(line).startswith(mutating_prefixes) for line in lines)


def _target_gpu_stack_remediation_satisfied(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> bool:
    rows = _target_gpu_stack_app_rows(payload, target_ref)
    if not rows:
        return False
    for row in rows:
        release_name = str(
            row.get("release-name", "") or row.get("release_name", "") or row.get("id") or ""
        ).strip()
        namespace = str(row.get("namespace", "") or "").strip()
        if not release_name or not namespace:
            return False
        if not _helm_release_deployed(
            command_runner=command_runner,
            kube_context=kube_context,
            release_name=release_name,
            namespace=namespace,
        ):
            return False
        app_id = str(row.get("id", "") or "").strip()
        for patch in _target_gpu_stack_post_render_patches(
            payload=payload,
            target_ref=target_ref,
            app_id=app_id,
        ):
            if not _target_gpu_stack_post_render_patch_satisfied(
                command_runner=command_runner,
                kube_context=kube_context,
                patch=patch,
            ):
                return False
    return True


def _external_node_template_upgrade_satisfied(
    *,
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    target_ref: str,
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
) -> bool:
    onboarding = _target_onboarding(payload, target_ref)
    target = _external_node_template_target(onboarding)
    cluster_id = _external_migration_cluster_id(
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        command_runner=command_runner,
    )
    cluster = _cluster_payload_by_id(command_runner=command_runner, cluster_id=cluster_id)
    current_version = _minor_version_text_or_empty(_cluster_control_plane_version(cluster))
    if not current_version:
        return False
    _ensure_external_node_template_k8s_not_downgrade(current_version, target.k8s_version)
    if not _minor_version_at_least(current_version, target.k8s_version):
        return False
    groups = _external_node_template_upgrade_groups(
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    if not groups:
        return False
    for _group_name, raw_group in groups:
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            return False
        node_group = _node_group_payload_by_id(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
        update_args = _external_node_template_update_args(
            node_group=node_group,
            source_group=raw_group,
            target=target,
        )
        clear_template_gpu_settings = _external_node_template_clears_cpu_gpu_settings(
            node_group=node_group,
            source_group=raw_group,
        )
        if update_args or clear_template_gpu_settings:
            return False
    return True


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _first_mapping_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _node_group_readiness_summary(node_group: Mapping[str, Any]) -> tuple[bool, str]:
    status = _mapping(node_group.get("status"))
    name = _node_group_name(node_group) or _node_group_id(node_group) or "unknown"
    if not status:
        return (
            False,
            f"{name}: status not returned by Nebius CLI; rollout readiness cannot be verified",
        )
    ready = _int_or_none(_first_mapping_value(status, "ready_node_count", "readyNodeCount"))
    target = _int_or_none(_first_mapping_value(status, "target_node_count", "targetNodeCount"))
    node_count = _int_or_none(_first_mapping_value(status, "node_count", "nodeCount"))
    outdated = _int_or_none(
        _first_mapping_value(status, "outdated_node_count", "outdatedNodeCount")
    )
    reconciling = bool(status.get("reconciling", False))
    summary = (
        f"{name}: ready={ready}/{target}, nodes={node_count}, "
        f"outdated={outdated}, reconciling={reconciling}"
    )
    missing = [
        field
        for field, value in (
            ("ready_node_count", ready),
            ("target_node_count", target),
            ("node_count", node_count),
        )
        if value is None
    ]
    if missing:
        return (
            False,
            f"{summary}; status missing {', '.join(missing)}, "
            "rollout readiness cannot be verified",
        )
    if ready is not None and target is not None and ready < target:
        return False, summary
    if node_count is not None and target is not None and node_count != target:
        return False, summary
    if outdated is not None and outdated > 0:
        return False, summary
    if reconciling:
        return False, summary
    return True, summary


def _verify_completed_soperator_migration_mk8s_state(
    *,
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    target_ref: str,
    worker_node_groups: Sequence[str],
    phase_ids: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
) -> list[str]:
    cluster_id = _external_migration_cluster_id(
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        command_runner=command_runner,
    )
    cluster = _cluster_payload_by_id(command_runner=command_runner, cluster_id=cluster_id)
    current_version = _minor_version_text_or_empty(_cluster_control_plane_version(cluster))
    errors: list[str] = []
    if not current_version:
        errors.append("control plane did not report a Kubernetes version")
    if _EXTERNAL_NODE_TEMPLATE_PHASE_ID in phase_ids:
        return _verify_completed_soperator_migration_node_template_state(
            payload=payload,
            source_report=source_report,
            target_ref=target_ref,
            worker_node_groups=worker_node_groups,
            command_runner=command_runner,
            current_version=current_version,
            errors=errors,
        )
    groups = _source_inventory_node_groups_with_ids(source_report)
    if not groups:
        errors.append("no source node-group inventory was available for MK8s verification")
    verified_groups = 0
    for group_name, raw_group in groups:
        node_group_id = _source_group_node_group_id(raw_group)
        node_group = _node_group_payload_by_id(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
        ready, readiness_summary = _node_group_readiness_summary(node_group)
        if not ready:
            errors.append(f"{group_name}: node group rollout is not ready: {readiness_summary}")
            continue
        verified_groups += 1
    if errors:
        raise RuntimeError(
            "Soperator migration MK8s verification failed after execute:\n"
            + "\n".join(f"- {item}" for item in errors)
        )
    return [
        "External MK8s cluster verified: "
        f"control plane Kubernetes {current_version}; "
        f"node groups {verified_groups}/{len(groups)} ready."
    ]


def _verify_completed_soperator_migration_node_template_state(
    *,
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    target_ref: str,
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
    current_version: str,
    errors: list[str],
) -> list[str]:
    onboarding = _target_onboarding(payload, target_ref)
    target = _external_node_template_target(onboarding)
    downgrade_error = _external_node_template_k8s_downgrade_error(
        current_version,
        target.k8s_version,
    )
    if downgrade_error:
        errors.append(downgrade_error)
    elif not current_version or not _minor_version_at_least(current_version, target.k8s_version):
        errors.append(
            f"control plane reports Kubernetes {current_version or 'unknown'}, "
            f"expected at least {target.k8s_version}"
        )
    groups = _external_node_template_upgrade_groups(
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    if not groups:
        errors.append("no source node-group inventory was available for node-template verification")
    verified_groups = 0
    for group_name, raw_group in groups:
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            errors.append(f"{group_name}: missing Nebius node group id")
            continue
        node_group = _node_group_payload_by_id(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
        update_args = _external_node_template_update_args(
            node_group=node_group,
            source_group=raw_group,
            target=target,
        )
        clear_template_gpu_settings = _external_node_template_clears_cpu_gpu_settings(
            node_group=node_group,
            source_group=raw_group,
        )
        ready, readiness_summary = _node_group_readiness_summary(node_group)
        if update_args or clear_template_gpu_settings:
            current = (
                f"Kubernetes {_minor_version_text_or_empty(_node_group_version(node_group)) or 'unknown'}, "
                f"OS {_node_group_template_os(node_group) or 'unknown'}, "
                f"GPU stack {_node_group_template_gpu_drivers_preset(node_group) or 'driverless'}"
            )
            errors.append(
                f"{group_name}: live node template still needs update args "
                f"{' '.join(update_args) or 'clear CPU GPU settings'}; current {current}"
            )
            continue
        if not ready:
            errors.append(f"{group_name}: node group rollout is not ready: {readiness_summary}")
            continue
        verified_groups += 1
    if errors:
        raise RuntimeError(
            "Soperator migration MK8s node-template verification failed after execute:\n"
            + "\n".join(f"- {item}" for item in errors)
        )
    return [
        "External MK8s node-template verified: "
        f"control plane Kubernetes {current_version}; "
        f"node groups {verified_groups}/{len(groups)} match Kubernetes {target.k8s_version}, "
        f"OS {target.os}, and GPU stack {target.gpu_stack_preset} for GPU groups."
    ]


def _source_inventory_node_groups_with_ids(
    source_report: Mapping[str, Any],
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    groups: list[tuple[str, Mapping[str, Any]]] = []
    seen_ids: set[str] = set()
    for raw_group_name, raw_group in sorted(_source_node_group_inventory(source_report).items()):
        group_name = normalize_component_token(raw_group_name)
        if not group_name or not isinstance(raw_group, Mapping):
            continue
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id or node_group_id in seen_ids:
            continue
        seen_ids.add(node_group_id)
        groups.append((group_name, raw_group))
    return tuple(groups)


def _create_aligned_sfs_satisfied(
    *,
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    target_ref: str,
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
) -> bool:
    project_id = _nebius_project_id(payload)
    specs = _aligned_filesystem_specs(payload=payload, target_ref=target_ref)
    specs_by_key = {spec.key: spec for spec in specs}
    filesystem_ids_by_key: dict[str, str] = {}
    for spec in specs:
        filesystem = _get_filesystem_by_name(
            command_runner=command_runner,
            project_id=project_id,
            name=spec.name,
        )
        filesystem_id = _filesystem_id(filesystem)
        if not filesystem_id:
            return False
        _validate_existing_filesystem(spec, filesystem)
        filesystem_ids_by_key[spec.key] = filesystem_id
    attachment_keys_by_group = _approved_role_attachment_keys(
        payload=payload,
        target_ref=target_ref,
        worker_node_groups=worker_node_groups,
        source_report=source_report,
    )
    inventory = _source_node_group_inventory(source_report)
    for raw_group_name, raw_group in sorted(inventory.items()):
        if not isinstance(raw_group, Mapping):
            continue
        group_name = normalize_component_token(raw_group_name)
        if not group_name:
            continue
        desired_keys = tuple(
            key
            for key in attachment_keys_by_group.get(group_name, ())
            if key in filesystem_ids_by_key and key in specs_by_key
        )
        if not desired_keys:
            continue
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            return False
        node_group = _node_group_payload_by_id(
            command_runner=command_runner,
            node_group_id=node_group_id,
        )
        existing = _filesystem_identity_set(_node_group_template_filesystems(node_group))
        desired = _filesystem_identity_set(
            [
                _filesystem_attachment(specs_by_key[key], filesystem_ids_by_key[key])
                for key in desired_keys
            ]
        )
        if not desired <= existing:
            return False
    return True


def _final_cutover_satisfied(
    *,
    checkpoint: Mapping[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> bool:
    cluster = _json_from_command(
        command_runner,
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "get",
            "slurmcluster",
            target_ref,
            "-o",
            "json",
        ],
        timeout_seconds=120,
        check=False,
    )
    if not _mapping(cluster.get("metadata")):
        return False
    if str(_mapping(cluster.get("status")).get("phase", "") or "").strip() != "Available":
        return False
    expected_values = _patch_target_values_for_compute(
        checkpoint=checkpoint,
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        live_snapshot=live_snapshot,
    )
    expected_nodesets = _target_worker_nodesets_by_name(expected_values)
    expected_names = tuple(
        dict.fromkeys((*_expected_cutover_nodesets(checkpoint), *expected_nodesets))
    )
    for name in expected_names:
        nodeset = _json_from_command(
            command_runner,
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "get",
                "nodeset",
                name,
                "-o",
                "json",
            ],
            timeout_seconds=120,
            check=False,
        )
        if not _mapping(nodeset.get("metadata")):
            return False
        expected_node_config = _non_empty_mapping_values(
            _mapping(expected_nodesets.get(name, {}).get("nodeConfig"))
        )
        if expected_node_config and not _mapping_contains_subset(
            _mapping(_mapping(nodeset.get("spec")).get("nodeConfig")),
            expected_node_config,
        ):
            return False
    return True


def _reconcile_completed_action_phases(
    *,
    checkpoint: dict[str, Any],
    completed_phases: set[str],
    phase_ids: Sequence[str],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
) -> list[str]:
    checks: Mapping[str, Callable[[], bool]] = {
        _EXTERNAL_NODE_TEMPLATE_PHASE_ID: lambda: _external_node_template_upgrade_satisfied(
            payload=payload,
            source_report=source_report,
            target_ref=target_ref,
            worker_node_groups=worker_node_groups,
            command_runner=command_runner,
        ),
        _TARGET_GPU_STACK_PHASE_ID: lambda: _target_gpu_stack_remediation_satisfied(
            payload=payload,
            target_ref=target_ref,
            kube_context=kube_context,
            command_runner=command_runner,
        ),
        "create-aligned-sfs": lambda: _create_aligned_sfs_satisfied(
            payload=payload,
            source_report=source_report,
            target_ref=target_ref,
            worker_node_groups=worker_node_groups,
            command_runner=command_runner,
        ),
        "final-control-plane-cutover": lambda: _final_cutover_satisfied(
            checkpoint=checkpoint,
            payload=payload,
            source_report=source_report,
            live_snapshot=live_snapshot,
            target_ref=target_ref,
            kube_context=kube_context,
            command_runner=command_runner,
        ),
    }
    lines: list[str] = []
    demoted_action = False
    for phase_id in phase_ids:
        if phase_id not in completed_phases:
            continue
        check = checks.get(phase_id)
        if check is None:
            continue
        if check():
            lines.append(f"{phase_id}: live state already satisfies completed action.")
            continue
        completed_phases.discard(phase_id)
        if (
            phase_id == "final-control-plane-cutover"
            and "rolling-compute-migration" in completed_phases
        ):
            completed_phases.discard("rolling-compute-migration")
            rolling = _phase_state(checkpoint, "rolling-compute-migration")
            rolling["target_values_revision"] = 0
            _append_event(
                checkpoint,
                "execute-phase-reconcile-required",
                phase="rolling-compute-migration",
                reason="final-control-plane-cutover no longer satisfies live state",
            )
            lines.append(
                "rolling-compute-migration: target Soperator values will be reapplied "
                "because final-control-plane-cutover drifted."
            )
        demoted_action = True
        _append_event(
            checkpoint,
            "execute-phase-reconcile-required",
            phase=phase_id,
            reason="completed checkpoint no longer satisfies live state",
        )
        lines.append(f"{phase_id}: live state no longer satisfies completed action; retrying.")
    if demoted_action and "validation-and-rollback-hold" in completed_phases:
        completed_phases.discard("validation-and-rollback-hold")
        _append_event(
            checkpoint,
            "execute-phase-reconcile-required",
            phase="validation-and-rollback-hold",
            reason="completed action was retried",
        )
        lines.append(
            "validation-and-rollback-hold: completed action changed; validation will rerun."
        )
    return lines


def execute_soperator_migration(
    *,
    config_path: Path,
    target_ref: str,
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    snapshot_collector: Callable[..., Mapping[str, Any]],
    approved: bool = False,
    command_runner: SoperatorMigrationCommandRunner | None = None,
    status_callback: Callable[[str], None] | None = None,
    status_poll_interval_seconds: float = 30.0,
    worker_rollout_strategy: str | None = None,
    worker_wave_groups: int | None = None,
    worker_wave_percent: int | None = None,
    max_parallel_worker_groups: int | None = None,
    strategy_max_surge_count: int | None = None,
    strategy_max_unavailable_count: int | None = None,
    strategy_drain_timeout: str | None = None,
) -> SoperatorMigrationExecutionResult:
    """Run checkpointed live Soperator migration phases."""

    normalized_target = normalize_component_token(target_ref)
    if not normalized_target:
        raise RuntimeError("Soperator migration execute requires a target ref.")
    with SoperatorMigrationExecutionLock(
        soperator_migration_lock_path(config_path, normalized_target)
    ):
        return _execute_soperator_migration_unlocked(
            config_path=config_path,
            target_ref=normalized_target,
            payload=payload,
            source_report=source_report,
            snapshot_collector=snapshot_collector,
            approved=approved,
            command_runner=command_runner,
            status_callback=status_callback,
            status_poll_interval_seconds=status_poll_interval_seconds,
            worker_rollout_strategy=worker_rollout_strategy,
            worker_wave_groups=worker_wave_groups,
            worker_wave_percent=worker_wave_percent,
            max_parallel_worker_groups=max_parallel_worker_groups,
            strategy_max_surge_count=strategy_max_surge_count,
            strategy_max_unavailable_count=strategy_max_unavailable_count,
            strategy_drain_timeout=strategy_drain_timeout,
        )


def _execute_soperator_migration_unlocked(
    *,
    config_path: Path,
    target_ref: str,
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    snapshot_collector: Callable[..., Mapping[str, Any]],
    approved: bool = False,
    command_runner: SoperatorMigrationCommandRunner | None = None,
    status_callback: Callable[[str], None] | None = None,
    status_poll_interval_seconds: float = 30.0,
    worker_rollout_strategy: str | None = None,
    worker_wave_groups: int | None = None,
    worker_wave_percent: int | None = None,
    max_parallel_worker_groups: int | None = None,
    strategy_max_surge_count: int | None = None,
    strategy_max_unavailable_count: int | None = None,
    strategy_drain_timeout: str | None = None,
) -> SoperatorMigrationExecutionResult:
    normalized_target = normalize_component_token(target_ref)
    if not normalized_target:
        raise RuntimeError("Soperator migration execute requires a target ref.")
    active_command_runner = command_runner or _default_command_runner
    onboarding = _target_onboarding(payload, normalized_target)
    rollout = resolve_external_node_template_rollout(
        onboarding,
        strategy=worker_rollout_strategy,
        worker_wave_groups=worker_wave_groups,
        worker_wave_percent=worker_wave_percent,
        max_parallel_worker_groups=max_parallel_worker_groups,
        strategy_max_surge_count=strategy_max_surge_count,
        strategy_max_unavailable_count=strategy_max_unavailable_count,
        strategy_drain_timeout=strategy_drain_timeout,
    )
    source_snapshot, report = _source_report_payload(source_report)
    source_report_fingerprint = _source_report_checkpoint_fingerprint(source_report)
    expected_source_version = _expected_source_version(onboarding=onboarding, report=report)
    source_analysis_fingerprint = str(report.get("fingerprint", "") or "").strip()
    if not source_analysis_fingerprint:
        raise RuntimeError(
            "Soperator source discovery report is missing its analysis fingerprint. "
            "Rerun `nebius-cxcli ext-soperator onboard` before executing migration."
        )
    expected_source_contract = _execution_source_contract(source_snapshot)
    expected_source_contract_fingerprint = _fingerprint(expected_source_contract)
    target_version = str(
        onboarding.get("target_version", "") or report.get("target_version", "") or ""
    )
    actions = _onboarding_actions(onboarding)
    phase_ids = _phase_ids_for_actions(report=report, onboarding=onboarding)
    if not phase_ids:
        phase_ids = ("discovery-and-plan",)
    requires_compute_executor = (
        ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION in actions
        or ONBOARDING_ACTION_UPGRADE_SOPERATOR in actions
    )

    kube_context = _target_kube_context(payload, normalized_target)
    checkpoint_path = soperator_migration_checkpoint_path(config_path, normalized_target)
    existing_checkpoint = _load_checkpoint(checkpoint_path)
    existing_completed = {
        str(phase or "").strip()
        for phase in (existing_checkpoint or {}).get("completed_phases", []) or []
        if str(phase or "").strip()
    }
    mutating_progress_started = bool(existing_completed & _MUTATING_PHASE_IDS) or (
        _checkpoint_has_mutating_progress(existing_checkpoint)
    )
    strict_source_fingerprint = not mutating_progress_started
    live_snapshot = snapshot_collector(kube_context=kube_context)
    live_report = analyze_soperator_onboarding_snapshot(
        live_snapshot,
        target_ref=normalized_target,
        pinned_chart_version=target_version,
        pinned_app_version=target_version,
        source_version_override=expected_source_version,
    )
    live_source_version = normalize_soperator_release_version(live_report.source_version)
    allowed_source_versions = {expected_source_version} if expected_source_version else set()
    if mutating_progress_started:
        allowed_source_versions.update(_target_resume_versions(target_version))
    if expected_source_version and live_source_version not in allowed_source_versions:
        raise RuntimeError(
            "Live Soperator source version changed since onboarding discovery: "
            f"expected {', '.join(sorted(allowed_source_versions))}, "
            f"found {live_source_version or 'not detected'}. "
            "Rerun `nebius-cxcli ext-soperator onboard` before executing migration."
        )
    if not live_source_version:
        raise RuntimeError(
            "Live Soperator source release was not detected. Rerun onboarding after installing "
            "the source Soperator release."
        )
    expected_node_groups = set(_mapping(expected_source_contract.get("node_groups")))
    ignored_live_target_node_groups = frozenset(
        role for role in _SOPERATOR_COMPUTE_ROLES if role not in expected_node_groups
    )
    live_source_contract_fingerprint = _fingerprint(
        _execution_source_contract(
            live_snapshot,
            ignored_node_groups=ignored_live_target_node_groups,
        )
    )
    if (
        strict_source_fingerprint
        and live_source_contract_fingerprint != expected_source_contract_fingerprint
    ):
        raise RuntimeError(
            "Live Soperator source discovery changed since onboarding: "
            f"expected stable contract fingerprint {expected_source_contract_fingerprint}, "
            f"found {live_source_contract_fingerprint}. "
            "Rerun `nebius-cxcli ext-soperator onboard` before executing migration."
        )

    execution_source_report = _source_report_with_execution_inventory(
        source_report=source_report,
        payload=payload,
        target_ref=normalized_target,
        kube_context=kube_context,
        command_runner=active_command_runner,
    )
    checkpoint = _checkpoint_for_run(
        existing=existing_checkpoint,
        target_ref=normalized_target,
        source_report_fingerprint=source_report_fingerprint,
        source_version=expected_source_version or live_source_version,
        target_version=target_version,
        phase_ids=phase_ids,
        allow_source_report_refresh=mutating_progress_started,
    )
    rollout_manifest = rollout.to_manifest_dict()
    saved_rollout = _mapping(checkpoint.get("external_node_template_rollout"))
    if saved_rollout and dict(saved_rollout) != rollout_manifest:
        raise RuntimeError(
            "Soperator migration checkpoint was started with different external "
            "node-template rollout settings. Resume with the same worker rollout "
            "strategy and budget, or remove the checkpoint only after deciding to "
            "restart the migration."
        )
    checkpoint["external_node_template_rollout"] = rollout_manifest
    completed_phases = set(
        str(phase or "").strip()
        for phase in checkpoint.get("completed_phases", []) or []
        if str(phase or "").strip()
    )
    completed_phases.add("discovery-and-plan")
    existing_approval = "customer-approval" in completed_phases or bool(
        str(checkpoint.get("customer_approved_at", "") or "").strip()
    )
    effective_approval = approved or existing_approval
    preflight_worker_groups: tuple[str, ...] = ()
    _service_rollout_groups, inferred_rollout_worker_groups = (
        _split_external_node_template_upgrade_groups(
            source_report=execution_source_report,
            worker_node_groups=preflight_worker_groups,
        )
        if _EXTERNAL_NODE_TEMPLATE_PHASE_ID in phase_ids
        else ((), ())
    )
    rollout_worker_group_count = len(inferred_rollout_worker_groups)
    quota_preflight_lines: list[str] = []
    worker_rollout_preflight_lines: list[str] = []
    quota_preflight_pending_phase = ""
    quota_preflight_pending_reason = ""
    if effective_approval:
        if requires_compute_executor:
            raw_worker_groups = tuple(
                str(group or "") for group in checkpoint.get("worker_node_groups", []) or []
            )
            preflight_worker_groups = _validate_worker_node_groups(
                source_report=execution_source_report,
                worker_node_groups=raw_worker_groups,
            )
            checkpoint["worker_node_groups"] = list(preflight_worker_groups)
            _service_rollout_groups, inferred_rollout_worker_groups = (
                _split_external_node_template_upgrade_groups(
                    source_report=execution_source_report,
                    worker_node_groups=preflight_worker_groups,
                )
            )
            rollout_worker_group_count = len(inferred_rollout_worker_groups)
        try:
            quota_preflight_lines = _run_soperator_migration_quota_preflight(
                checkpoint=checkpoint,
                completed_phases=completed_phases,
                phase_ids=phase_ids,
                payload=payload,
                target_ref=normalized_target,
                source_report=execution_source_report,
                worker_node_groups=preflight_worker_groups,
                command_runner=active_command_runner,
                rollout=rollout,
            )
        except SoperatorMigrationPhasePending as exc:
            quota_preflight_pending_phase = "rolling-compute-migration"
            quota_preflight_pending_reason = str(exc)
        if (
            _EXTERNAL_NODE_TEMPLATE_PHASE_ID in phase_ids
            and _EXTERNAL_NODE_TEMPLATE_PHASE_ID not in completed_phases
            and not quota_preflight_pending_phase
        ):
            worker_rollout_preflight_lines = _run_soperator_worker_rollout_live_preflight(
                source_report=execution_source_report,
                worker_node_groups=preflight_worker_groups,
                command_runner=active_command_runner,
                kube_context=kube_context,
                rollout=rollout,
            )
    else:
        quota_preflight_lines = [
            "Quota preflight: deferred until customer approval because no mutating phase will run."
        ]
    _append_event(
        checkpoint,
        "execute-preflight-completed",
        live_source_contract_fingerprint=live_source_contract_fingerprint,
        live_source_version=live_source_version,
        source_analysis_fingerprint=source_analysis_fingerprint,
        source_contract_fingerprint=expected_source_contract_fingerprint,
        strict_source_fingerprint=strict_source_fingerprint,
    )

    approved_worker_groups: tuple[str, ...] = ()
    if effective_approval:
        if requires_compute_executor:
            approved_worker_groups = preflight_worker_groups
            checkpoint["worker_node_groups"] = list(approved_worker_groups)
        completed_phases.add("customer-approval")
        if "customer_approved_at" not in checkpoint:
            checkpoint["customer_approved_at"] = _utc_now()
        if approved and not existing_approval:
            _append_event(
                checkpoint,
                "customer-approval-recorded",
                worker_node_groups=approved_worker_groups,
            )

    mutation_performed = False
    phase_lines: list[str] = []
    pending_phase = ""
    pending_reason = ""

    def _checkpoint_progress() -> None:
        checkpoint["completed_phases"] = _ordered_phase_list(completed_phases, phase_ids)
        checkpoint["updated_at"] = _utc_now()
        _write_checkpoint(checkpoint_path, checkpoint)

    status_reporter: SoperatorMigrationStatusReporter | None = None
    if effective_approval and status_callback is not None:
        status_reporter = SoperatorMigrationStatusReporter(
            emit=status_callback,
            command_runner=active_command_runner,
            kube_context=kube_context,
            checkpoint=checkpoint,
            payload=payload,
            source_report=execution_source_report,
            target_ref=normalized_target,
            phase_ids=phase_ids,
            poll_interval_seconds=status_poll_interval_seconds,
        )

    def _append_status_event(
        snapshot: SoperatorMigrationStatusSnapshot | None,
        *,
        point: str,
    ) -> None:
        event = dict(_status_snapshot_event(snapshot))
        if not event:
            return
        event["point"] = point
        _append_event(checkpoint, "execute-status", **event)

    if not effective_approval:
        pending_phase = "customer-approval"
        pending_reason = (
            "live preflight completed and checkpointed; customer approval is required "
            "before mutating phases."
        )
    elif quota_preflight_pending_phase:
        pending_phase = quota_preflight_pending_phase
        pending_reason = quota_preflight_pending_reason
    else:
        if _validation_hold_needs_reconcile(checkpoint, completed_phases):
            completed_phases.discard("validation-and-rollback-hold")
            _append_event(
                checkpoint,
                "execute-phase-reconcile-required",
                phase="validation-and-rollback-hold",
                reason="validation contract changed",
                target_revision=_VALIDATION_HOLD_REVISION,
            )
        if "rolling-compute-migration" in completed_phases and _has_soperator_custom_resources(
            live_snapshot
        ):
            phase_mutation, lines = _reconcile_completed_compute_cutover(
                checkpoint=checkpoint,
                payload=payload,
                source_report=execution_source_report,
                live_snapshot=live_snapshot,
                target_ref=normalized_target,
                kube_context=kube_context,
                command_runner=active_command_runner,
            )
            if phase_mutation or lines:
                mutation_performed = mutation_performed or phase_mutation
                phase_lines.extend([f"rolling-compute-migration: {line}" for line in lines])
                _append_event(
                    checkpoint,
                    "execute-phase-reconciled",
                    phase="rolling-compute-migration",
                    mutation_performed=phase_mutation,
                )
                _checkpoint_progress()
                live_snapshot = snapshot_collector(kube_context=kube_context)
        action_reconcile_lines = _reconcile_completed_action_phases(
            checkpoint=checkpoint,
            completed_phases=completed_phases,
            phase_ids=phase_ids,
            payload=payload,
            source_report=execution_source_report,
            live_snapshot=live_snapshot,
            target_ref=normalized_target,
            kube_context=kube_context,
            worker_node_groups=approved_worker_groups,
            command_runner=active_command_runner,
        )
        if action_reconcile_lines:
            phase_lines.extend(action_reconcile_lines)
            _checkpoint_progress()
        phase_handlers: Mapping[str, Callable[[], tuple[bool, list[str]]]] = {
            _EXTERNAL_NODE_TEMPLATE_PHASE_ID: lambda: _execute_external_node_template_upgrade_phase(
                checkpoint=checkpoint,
                payload=payload,
                source_report=execution_source_report,
                target_ref=normalized_target,
                kube_context=kube_context,
                worker_node_groups=approved_worker_groups,
                command_runner=active_command_runner,
                checkpoint_writer=_checkpoint_progress,
                rollout=rollout,
            ),
            _TARGET_GPU_STACK_PHASE_ID: lambda: _execute_target_gpu_stack_remediation_phase(
                checkpoint=checkpoint,
                payload=payload,
                target_ref=normalized_target,
                kube_context=kube_context,
                command_runner=active_command_runner,
            ),
            "create-aligned-sfs": lambda: _execute_create_aligned_sfs_phase(
                checkpoint=checkpoint,
                payload=payload,
                source_report=execution_source_report,
                target_ref=normalized_target,
                worker_node_groups=approved_worker_groups,
                command_runner=active_command_runner,
            ),
            "online-bulk-data-sync": lambda: _execute_online_bulk_data_sync_phase(
                checkpoint=checkpoint,
                payload=payload,
                source_report=execution_source_report,
                live_snapshot=live_snapshot,
                target_ref=normalized_target,
                kube_context=kube_context,
                command_runner=active_command_runner,
            ),
            "rolling-compute-migration": lambda: _execute_rolling_compute_migration_phase(
                checkpoint=checkpoint,
                payload=payload,
                source_report=execution_source_report,
                live_snapshot=live_snapshot,
                target_ref=normalized_target,
                kube_context=kube_context,
                worker_node_groups=approved_worker_groups,
                command_runner=active_command_runner,
                checkpoint_writer=_checkpoint_progress,
            ),
            "final-control-plane-cutover": lambda: _execute_final_cutover_phase(
                checkpoint=checkpoint,
                live_snapshot=live_snapshot,
                target_ref=normalized_target,
                kube_context=kube_context,
                command_runner=active_command_runner,
            ),
            "validation-and-rollback-hold": lambda: _execute_validation_hold_phase(
                config_path=config_path,
                checkpoint=checkpoint,
                payload=payload,
                live_snapshot=live_snapshot,
                target_ref=normalized_target,
                kube_context=kube_context,
                target_version=target_version,
                command_runner=active_command_runner,
            ),
            "retire-old-resources": lambda: _execute_retire_old_resources_phase(
                checkpoint=checkpoint,
                source_report=execution_source_report,
                live_snapshot=live_snapshot,
                kube_context=kube_context,
                command_runner=active_command_runner,
            ),
        }
        for phase_id in phase_ids:
            if phase_id in {"discovery-and-plan", "customer-approval"}:
                continue
            if phase_id in completed_phases:
                continue
            handler = phase_handlers.get(phase_id)
            if handler is None:
                pending_phase = phase_id
                pending_reason = f"unsupported Soperator migration phase '{phase_id}'."
                break
            status_started = False
            try:
                if status_reporter is not None:
                    _append_status_event(status_reporter.set_phase(phase_id), point="phase-start")
                    status_reporter.start()
                    status_started = True
                phase_mutation, lines = handler()
            except SoperatorMigrationPhasePending as exc:
                pending_phase = phase_id
                pending_reason = str(exc)
                break
            finally:
                if status_reporter is not None:
                    _append_status_event(status_reporter.emit(force=True), point="phase-end")
                    if status_started:
                        status_reporter.stop()
            mutation_performed = mutation_performed or phase_mutation
            completed_phases.add(phase_id)
            _append_event(
                checkpoint,
                "execute-phase-completed",
                phase=phase_id,
                mutation_performed=phase_mutation,
            )
            _clear_completed_pending_phase(checkpoint, phase_id)
            phase_lines.extend([f"{phase_id}: {line}" for line in lines])
            _checkpoint_progress()
            if phase_id in {
                _EXTERNAL_NODE_TEMPLATE_PHASE_ID,
                _TARGET_GPU_STACK_PHASE_ID,
                "create-aligned-sfs",
                "online-bulk-data-sync",
                "rolling-compute-migration",
                "final-control-plane-cutover",
            }:
                live_snapshot = snapshot_collector(kube_context=kube_context)
                _append_event(
                    checkpoint,
                    "execute-live-snapshot-refreshed",
                    after_phase=phase_id,
                )
                _checkpoint_progress()

    if not pending_phase and effective_approval:
        mk8s_state_lines = _verify_completed_soperator_migration_mk8s_state(
            payload=payload,
            source_report=execution_source_report,
            target_ref=normalized_target,
            worker_node_groups=approved_worker_groups,
            phase_ids=phase_ids,
            command_runner=active_command_runner,
        )
        mk8s_phase_lines = [f"post-migration-mk8s-check: {line}" for line in mk8s_state_lines]
        phase_lines.extend(mk8s_phase_lines)
        try:
            helm_state_lines = _verify_completed_soperator_migration_helm_state(
                command_runner=active_command_runner,
                kube_context=kube_context,
                target_version=target_version,
            )
        except RuntimeError as exc:
            if mk8s_phase_lines:
                raise RuntimeError("\n".join(mk8s_phase_lines) + "\n" + str(exc)) from exc
            raise
        mutation_performed = mutation_performed or _helm_state_lines_include_retirement_mutation(
            helm_state_lines
        )
        phase_lines.extend(f"post-migration-helm-check: {line}" for line in helm_state_lines)

    if pending_phase:
        checkpoint["pending_phase"] = pending_phase
        checkpoint["pending_reason"] = pending_reason
        _append_event(checkpoint, "execute-pending", pending_phase=pending_phase)
    else:
        checkpoint["pending_phase"] = "none"
        checkpoint["pending_reason"] = ""
        _append_event(checkpoint, "execute-completed")
    report_path = _migrate_report_path(config_path)
    checkpoint["migrate_report"] = str(report_path)
    _append_event(checkpoint, "migrate-report-written", path=str(report_path))
    report_path = _write_soperator_migrate_report(
        config_path=config_path,
        checkpoint_path=checkpoint_path,
        checkpoint=checkpoint,
        phase_ids=phase_ids,
        completed_phases=completed_phases,
        target_ref=normalized_target,
        source_version=live_source_version,
        target_version=target_version,
        pending_phase=str(checkpoint["pending_phase"]),
        pending_reason=pending_reason,
        mutation_performed=mutation_performed,
    )
    _checkpoint_progress()

    lines = [
        f"Execute preflight checkpoint: {checkpoint_path}",
        f"Live source version verified: {live_source_version}",
        "Completed execute phases: " + ", ".join(_ordered_phase_list(completed_phases, phase_ids)),
    ]
    lines.extend(quota_preflight_lines)
    lines.extend(worker_rollout_preflight_lines)
    if approved_worker_groups:
        lines.insert(
            3,
            "Auto-selected source worker node groups: " + ", ".join(approved_worker_groups),
        )
    lines.insert(
        3 if not approved_worker_groups else 4,
        "External node-template worker rollout: "
        + rollout.strategy
        + " ("
        + _worker_rollout_budget_label(
            rollout,
            worker_group_count=rollout_worker_group_count,
        )
        + "; "
        + _worker_group_strategy_label(rollout)
        + ").",
    )
    lines.extend(phase_lines)
    lines.extend(
        [
            f"Pending phase: {checkpoint['pending_phase']}",
            f"Pending reason: {pending_reason or 'none'}",
            "Migration performed: " + ("yes." if mutation_performed else "no."),
            f"Migrate report: {report_path}",
        ]
    )
    return SoperatorMigrationExecutionResult(
        checkpoint_path=checkpoint_path,
        completed_phases=tuple(_ordered_phase_list(completed_phases, phase_ids)),
        pending_phase=str(checkpoint["pending_phase"]),
        pending_reason=pending_reason,
        live_source_version=live_source_version,
        target_version=target_version,
        mutation_performed=mutation_performed,
        lines=tuple(lines),
        report_path=report_path,
    )
