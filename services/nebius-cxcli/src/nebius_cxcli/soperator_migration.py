"""Soperator migration execution checkpoints and guarded preflight."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from .component_instances import normalize_component_token
from .deploy_validation_report import (
    DEPLOY_REPORT_FILENAME,
    build_deploy_validation_report,
    clear_deploy_validation_artifacts,
    status_label,
    validation_section_lines,
)
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
    ONBOARDING_ACTION_REMEDIATE_TARGET_GPU_STACK,
    ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
    ONBOARDING_ACTION_UPGRADE_SOPERATOR,
    ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_GPU_STACK_PRESET,
    ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION,
    ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_OS,
    analyze_soperator_onboarding_snapshot,
    normalize_soperator_release_version,
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
        "slurm-cluster-storage",
        "soperator-checks",
        "soperator-controller",
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
_SOPERATOR_SOURCE_SAFE_PRUNE_RELEASE_NAMES = frozenset(
    {
        "soperator-checks",
        "soperator-controller",
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
    "helm-storageclasses",
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
        try:
            self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RuntimeError(
                f"Soperator migration is already running for this target or left a lock: {self.path}. "
                "Remove the lock only after verifying no matching migration process is active."
            ) from exc
        payload = {
            "pid": os.getpid(),
            "created_at": _utc_now(),
        }
        os.write(self._fd, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        with suppress(FileNotFoundError):
            self.path.unlink()


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
        or ONBOARDING_ACTION_REMEDIATE_TARGET_GPU_STACK in actions
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
        and ONBOARDING_ACTION_REMEDIATE_TARGET_GPU_STACK in actions
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
    except ValueError:
        return current == target
    return (current_version.major, current_version.minor) >= (
        target_version.major,
        target_version.minor,
    )


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


def _update_node_group_with_zero_surge_strategy(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    node_group_id: str,
    update_args: Sequence[str],
    original_node_group: Mapping[str, Any] | None = None,
    clear_template_gpu_settings: bool = False,
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
    updated = False
    try:
        zero_surge_strategy_args = _soperator_zero_surge_strategy_cli_args()
        if clear_template_gpu_settings:
            result = _json_from_node_group_update_file(
                command_runner,
                node_group_id,
                payload=_node_group_full_update_payload(
                    original_node_group=original_node_group,
                    update_args=update_args,
                    strategy_args=zero_surge_strategy_args,
                    clear_template_gpu_settings=True,
                ),
                timeout="45m",
                timeout_seconds=timeout_seconds,
            )
        else:
            result = _json_from_command(
                command_runner,
                _node_group_update_command(
                    node_group_id,
                    update_args=update_args,
                    strategy_args=zero_surge_strategy_args,
                    timeout="45m",
                ),
                timeout_seconds=timeout_seconds,
            )
        updated = True
        return result
    finally:
        try:
            _json_from_command(
                command_runner,
                _node_group_update_command(
                    node_group_id,
                    update_args=(),
                    strategy_args=original_strategy_args,
                    timeout="10m",
                ),
                timeout_seconds=600,
            )
        except Exception as exc:
            if updated:
                raise RuntimeError(
                    f"Could not restore original update strategy for node group {node_group_id}."
                ) from exc
            raise


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
                timeout_seconds=2700,
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
    return tuple([*service_groups, *worker_groups])


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
        ["kubectl", "--context", kube_context, "get", "nodes", "-o", "json"],
        timeout_seconds=300,
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
    execution_report = copy.deepcopy(to_plain_data(source_report))
    snapshot = execution_report.setdefault("snapshot", {})
    if isinstance(snapshot, dict):
        snapshot["node_groups"] = to_plain_data(inventory)
    return execution_report if isinstance(execution_report, Mapping) else source_report


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
            "Quota preflight worker node groups: preserved in place with zero-surge "
            "migration-owned template remediation, no parallel or surge worker quota required ("
            + ", ".join(worker_node_groups)
            + "); capacity may be reduced by one node in the active group during rollout."
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
        ],
        timeout_seconds=120,
    )
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


def _node_group_label(item: Mapping[str, Any]) -> str:
    metadata = _mapping(item.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    for key in (
        "slurm.nebius.ai/nodeset-name",
        "slurm.nebius.ai/nodeset",
        "nebius.com/node-group",
        "yandex.cloud/node-group-id",
        "node.kubernetes.io/instance-type",
    ):
        value = str(labels.get(key, "") or "").strip()
        if value:
            return value
    return "unlabeled"


def _collect_mk8s_status(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> SoperatorMigrationStatusSignal:
    result = command_runner(
        ["kubectl", "--context", kube_context, "get", "nodes", "-o", "json"],
        timeout_seconds=60,
        check=False,
    )
    if result.returncode != 0:
        return SoperatorMigrationStatusSignal(
            "MK8s",
            "unknown",
            "node status unavailable: " + _command_detail(result),
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return SoperatorMigrationStatusSignal(
            "MK8s",
            "unknown",
            "node status returned invalid JSON",
        )
    items = payload.get("items") if isinstance(payload, Mapping) else None
    nodes = tuple(item for item in _sequence_of_mappings(items))
    if not nodes:
        return SoperatorMigrationStatusSignal(
            "MK8s",
            "down",
            "nodes 0/0 Ready",
        )
    total = len(nodes)
    ready = sum(1 for item in nodes if _node_ready(item))
    cordoned = sum(1 for item in nodes if _mapping(item.get("spec")).get("unschedulable") is True)
    groups: dict[str, list[int]] = {}
    for item in nodes:
        group = groups.setdefault(_node_group_label(item), [0, 0])
        group[1] += 1
        if _node_ready(item):
            group[0] += 1
    group_summary = ", ".join(
        f"{name}:{counts[0]}/{counts[1]}" for name, counts in sorted(groups.items())[:4]
    )
    if len(groups) > 4:
        group_summary += f", +{len(groups) - 4} more"
    parts = [f"nodes {ready}/{total} Ready"]
    if cordoned:
        parts.append(f"{cordoned} cordoned")
    if group_summary:
        parts.append(group_summary)
    state = "serving"
    if ready <= 0:
        state = "down"
    elif ready < total or cordoned:
        state = "degraded"
    return SoperatorMigrationStatusSignal("MK8s", state, "; ".join(parts))


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


def _collect_slurm_status(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> SoperatorMigrationStatusSignal:
    try:
        sinfo = _kubectl_exec_login(
            command_runner=command_runner,
            kube_context=kube_context,
            args=("sinfo", "-h", "-o", "%t"),
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
            "Slurm",
            "down",
            "login or sinfo unavailable: " + _command_detail(sinfo),
        )
    raw_states = [line.strip() for line in sinfo.stdout.splitlines() if line.strip()]
    states = [_normalize_slurm_node_state(item) for item in raw_states]
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
        if any("*" in item for item in raw_states) or any(item in unavailable for item in states):
            node_state = "degraded"
        elif any(item in draining for item in states):
            node_state = "draining"
        else:
            node_state = "serving"
        node_summary = "nodes " + _state_counts(states)
    state = node_state
    if _STATUS_STATE_RANK[queue_state] > _STATUS_STATE_RANK[state]:
        state = queue_state
    return SoperatorMigrationStatusSignal("Slurm", state, f"{node_summary}; {queue_summary}")


def _collect_soperator_status(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
) -> SoperatorMigrationStatusSignal:
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
        ],
        timeout_seconds=60,
        check=False,
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
            f"Soperator migration status [{elapsed}] phase {phase_id} ({state}): {signal_text}"
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
            ],
            timeout_seconds=120,
            check=False,
        )
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
            gpu_count = resources.get("gpu", resources.get("nvidia.com/gpu"))
            if gpu_count not in (None, "", 0, "0"):
                resources["gpu"] = gpu_count
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
        if all(
            {
                normalize_component_token(ref)
                for ref in _sequence_of_scalars(_mapping(partition).get("nodeSetRefs"))
            }
            <= worker_name_set
            for partition in partitions
            if isinstance(partition, Mapping)
        ):
            return plain
    return {}


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
    live_size = _pvc_live_size(live_snapshot, "accounting-pvc") or _pv_live_size(
        live_snapshot,
        "accounting-pv",
    )
    if not live_size:
        live_size = "128Gi"
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
    storage["storageClassName"] = "slurm-local-pv"
    storage["volumeClaimTemplate"] = {
        "accessModes": ["ReadWriteMany"],
        "resources": {"requests": {"storage": live_size}},
        "storageClassName": "slurm-local-pv",
    }


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
        "-f",
        "-",
        "--wait",
        "--timeout",
        "45m",
    ]
    if version:
        command.extend(["--version", version])
    values_text = json.dumps(to_plain_data(_mapping(row.get("values"))), sort_keys=True)
    command_runner(command, input_text=values_text, timeout_seconds=3000)
    readiness = verify_helm_chart_ready(
        command_runner=command_runner,
        kube_context=kube_context,
        release_name=release_name,
        namespace=namespace,
        expected_version=version,
    )
    return {
        "id": component_id,
        "release_name": release_name,
        "namespace": namespace,
        "chart_ref": chart_ref,
        "version": version,
        "readiness": readiness.summary(),
        "applied_at": _utc_now(),
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
            verb_args=["get", "-f", "-", "-o", "json"],
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
            "-f",
            "-",
        ]
        if wait:
            command.extend(["--wait", "--timeout", "45m"])
        values_text = json.dumps(to_plain_data(values), sort_keys=True)
        adoption_attempts: dict[tuple[str, str, str], int] = {}
        pending_operation_cleared = False
        webhook_startup_retries = 0
        while True:
            try:
                command_runner(command, input_text=values_text, timeout_seconds=3000)
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
) -> bool:
    result = command_runner(
        [
            "helm",
            "--kube-context",
            kube_context,
            "history",
            "soperator",
            "-n",
            _SOPERATOR_NAMESPACE,
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
            _SOPERATOR_NAMESPACE,
            "delete",
            "secret",
            f"sh.helm.release.v1.soperator.v{latest_revision}",
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
) -> None:
    for role in _SOPERATOR_COMPUTE_ROLES:
        storage_group = _SOPERATOR_ROLE_SOURCE_KIND[role]
        for label_key in _SOPERATOR_NODESET_LABEL_KEYS:
            result = command_runner(
                [
                    "kubectl",
                    "--context",
                    kube_context,
                    "label",
                    "nodes",
                    "-l",
                    f"{label_key}={role}",
                    f"slurm.nebius.ai/nodeset-name={role}",
                    f"nebius.com/node-group={storage_group}",
                    "--overwrite",
                ],
                timeout_seconds=300,
                check=False,
            )
            if result.returncode != 0:
                detail = f"{result.stderr}\n{result.stdout}".lower()
                if "no resources found" not in detail and "not found" not in detail:
                    raise RuntimeError(
                        f"{_command_text(result.args)} failed: "
                        f"{result.stderr.strip() or result.stdout.strip()}"
                    )


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
        ],
        timeout_seconds=120,
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
        ],
        timeout_seconds=120,
    )
    return adopted_key


def _legacy_flux_helmrelease_name(name: str) -> bool:
    normalized = str(name or "").strip()
    return normalized == "soperator-fluxcd" or normalized.startswith(
        "flux-system-soperator-fluxcd-"
    )


def _suspend_legacy_flux_helmreleases(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    phase: dict[str, Any],
) -> list[str]:
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
        ],
        timeout_seconds=120,
        check=False,
    )
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

    groups = _external_node_template_upgrade_groups(
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
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
    for group_name, raw_group in groups:
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
                "strategy": "zero-surge",
                "update_args": list(update_args),
            }
        )
        if (
            group_state.get("status") in {"completed", "already-current"}
            and group_state.get("target") == target_payload
            and not update_args
            and not clear_template_gpu_settings
        ):
            lines.append(f"External node-template already handled: {group_name}.")
            continue
        if not update_args and not clear_template_gpu_settings:
            group_state["status"] = "already-current"
            group_state["completed_at"] = _utc_now()
            group_state["strategy_restored"] = False
            lines.append(f"External node-template already current: {group_name}.")
            continue
        group_state["status"] = "updating"
        group_state["started_at"] = group_state.get("started_at") or _utc_now()
        service_role = _source_group_service_quiesce_role(group_name, raw_group)
        service_quiesce_state: dict[str, Any] | None = None
        if service_role and _positive_int(raw_group.get("node_count"), fallback=1) <= 1:
            raw_quiesce_state = group_state.setdefault("service_quiesce", {})
            if not isinstance(raw_quiesce_state, dict):
                raise RuntimeError(
                    "Soperator migration checkpoint external-node-template-upgrade "
                    f"node group {group_name}.service_quiesce must be a mapping."
                )
            service_quiesce_state = raw_quiesce_state
            lines.extend(
                _quiesce_external_service_role(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    role=service_role,
                    state=service_quiesce_state,
                )
            )
            if checkpoint_writer is not None:
                checkpoint_writer()
        if checkpoint_writer is not None:
            checkpoint_writer()
        try:
            _update_node_group_with_zero_surge_strategy(
                command_runner=command_runner,
                node_group_id=node_group_id,
                update_args=update_args,
                original_node_group=node_group,
                clear_template_gpu_settings=clear_template_gpu_settings,
                timeout_seconds=2700,
            )
        except Exception as exc:
            group_state["status"] = "failed"
            group_state["error"] = str(exc)
            if service_quiesce_state is not None:
                try:
                    lines.extend(
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
            if checkpoint_writer is not None:
                checkpoint_writer()
            raise
        mutation_performed = True
        if service_quiesce_state is not None:
            lines.extend(
                _restore_external_service_role(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    state=service_quiesce_state,
                )
            )
            if checkpoint_writer is not None:
                checkpoint_writer()
        group_state["status"] = "completed"
        group_state["completed_at"] = _utc_now()
        group_state["strategy_restored"] = True
        lines.append(
            "External node-template upgraded: "
            f"{group_name} -> Kubernetes {target.k8s_version}, OS {target.os}"
            + (f", GPU stack {target.gpu_stack_preset}" if _source_group_is_gpu(raw_group) else "")
            + "."
        )
    lines.append(
        "External node-template strategy: zero-surge per node group "
        "(max_surge=0, max_unavailable=1, drain_timeout=30m); preserved worker "
        "groups do not need additional worker quota, but active group capacity "
        "may be reduced by one node during rollout."
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
            "target GPU stack remediation requires target-scoped GPU Operator or "
            "Network Operator app rows. Rerun `nebius-cxcli ext-soperator onboard` so "
            "the accepted config carries the remediation app rows before executing migration."
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
    if not _has_soperator_custom_resources(live_snapshot):
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
    if checkpoint_writer is not None:
        checkpoint_writer()
    quiet_lines = _ensure_slurm_quiet(
        command_runner=command_runner,
        kube_context=kube_context,
        allow_missing_login_recovery=not _has_live_slurmcluster_resource(live_snapshot),
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
    _reconcile_target_node_storage_labels(
        command_runner=command_runner,
        kube_context=kube_context,
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
    _reconcile_target_node_storage_labels(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    _suspend_legacy_flux_helmreleases(
        command_runner=command_runner,
        kube_context=kube_context,
        phase=phase,
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
        ],
        timeout_seconds=120,
        check=False,
    )
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
        f"Deploy report refreshed: {report_path}",
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


def _write_checkpoint(path: Path, checkpoint: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(to_plain_data(checkpoint), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


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
        "- Mutation performed: `" + ("yes" if mutation_performed else "no") + "`",
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
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
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
    if (
        name == _SOPERATOR_TARGET_RELEASE_NAME
        and str(getattr(release, "namespace", "") or "").strip() == _SOPERATOR_NAMESPACE
    ):
        return False
    if name in _SOPERATOR_SOURCE_RELEASE_NAMES:
        return True
    return any(chart.startswith(prefix) for prefix in _SOPERATOR_SOURCE_CHART_PREFIXES)


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
    releases = list_helm_releases(
        command_runner=command_runner,
        kube_context=kube_context,
        all_namespaces=True,
    )
    for release in releases:
        if str(getattr(release, "status", "") or "").strip().lower() != "deployed":
            continue
        if not _soperator_source_release_family(release):
            continue
        release_versions = _soperator_release_versions(release)
        if release_versions and not release_versions.issubset(target_versions):
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
) -> Mapping[str, Any]:
    result = command_runner(command, timeout_seconds=120, check=False)
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
    if not any(chart_name.startswith(prefix) for prefix in _SOPERATOR_SOURCE_CHART_PREFIXES):
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


def _source_flux_helmrelease_suspend_candidates(
    *,
    payload: Mapping[str, Any],
    stale_releases: Sequence[Any],
    target_version: str,
) -> tuple[Mapping[str, Any], ...]:
    release_keys = {
        _release_key(getattr(release, "namespace", ""), getattr(release, "name", ""))
        for release in stale_releases
    }
    candidates: list[Mapping[str, Any]] = []
    for item in _sequence_of_mappings(payload.get("items")):
        spec = _mapping(item.get("spec"))
        if bool(spec.get("suspend")):
            continue
        if _source_flux_helmrelease_matches_release(
            item,
            release_keys,
        ) or _source_flux_helmrelease_chart_family(item, target_version=target_version):
            candidates.append(item)
    return tuple(candidates)


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
        command_runner(
            [
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
            timeout_seconds=120,
        )
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
    candidates = _source_flux_helmrelease_suspend_candidates(
        payload=payload,
        stale_releases=stale_releases,
        target_version=target_version,
    )
    suspended_kustomizations = _suspend_source_flux_kustomizations(
        command_runner=command_runner,
        kube_context=kube_context,
        items=candidates,
    )
    suspended: list[str] = []
    for item in candidates:
        metadata = _mapping(item.get("metadata"))
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
                "patch",
                "helmrelease.helm.toolkit.fluxcd.io",
                name,
                "--type=merge",
                "-p",
                json.dumps({"spec": {"suspend": True}}, separators=(",", ":")),
            ],
            timeout_seconds=120,
        )
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
            "--wait=true",
            "--timeout=5m",
        ]
    )
    command_runner(command, timeout_seconds=360)


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
    stale_names = {
        str(getattr(release, "name", "") or "").strip()
        for release in stale_releases
        if str(getattr(release, "name", "") or "").strip()
    }
    if not stale_names:
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
        if release_name not in stale_names:
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
    elif suspended or suspended_kustomizations:
        lines.append("No stale source Soperator Helm release records remained.")
    if suspended_kustomizations:
        lines.append(
            "Suspended old Flux Kustomization desired state: "
            + ", ".join(suspended_kustomizations)
            + "."
        )
    if suspended:
        lines.append("Suspended old Flux HelmRelease desired state: " + ", ".join(suspended) + ".")
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
    target_readiness = verify_helm_chart_ready(
        command_runner=command_runner,
        kube_context=kube_context,
        release_name=_SOPERATOR_TARGET_RELEASE_NAME,
        namespace=_SOPERATOR_NAMESPACE,
        expected_version=target_version,
    )
    target_line = "Verified target Soperator Helm chart readiness: " + target_readiness.summary()
    retirement_lines = _retire_stale_source_soperator_helm_releases(
        command_runner=command_runner,
        kube_context=kube_context,
        target_version=target_version,
    )
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
    if not current_version or not _minor_version_at_least(current_version, target.k8s_version):
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
    if not current_version or not _minor_version_at_least(current_version, target.k8s_version):
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
) -> SoperatorMigrationExecutionResult:
    normalized_target = normalize_component_token(target_ref)
    if not normalized_target:
        raise RuntimeError("Soperator migration execute requires a target ref.")
    active_command_runner = command_runner or _default_command_runner
    onboarding = _target_onboarding(payload, normalized_target)
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
    quota_preflight_lines: list[str] = []
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
            )
        except SoperatorMigrationPhasePending as exc:
            quota_preflight_pending_phase = "rolling-compute-migration"
            quota_preflight_pending_reason = str(exc)
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
    if approved_worker_groups:
        lines.insert(
            3,
            "Auto-selected source worker node groups: " + ", ".join(approved_worker_groups),
        )
    lines.extend(phase_lines)
    lines.extend(
        [
            f"Pending phase: {checkpoint['pending_phase']}",
            f"Pending reason: {pending_reason or 'none'}",
            "Mutation performed: " + ("yes." if mutation_performed else "no."),
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
