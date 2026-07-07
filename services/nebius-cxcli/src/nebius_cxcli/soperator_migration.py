"""External Soperator upgrade execution checkpoints and guarded preflight."""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import yaml
from rich.console import Console
from rich.live import Live
from rich.table import Table

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
    parse_k8s_version,
    require_single_minor_hop,
    resolve_drain_timeout,
    terraform_node_group_strategy_for_policy,
    validate_node_template_field_value,
    validate_os_image_value,
)
from .nebius_api_helpers import sdk_message_to_mapping, sdk_parse_message, wait_nebius_operation
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
from .sdk_auth import init_nebius_sdk, suppress_expected_refresh_logs
from .slurm_job_control import (
    SLURM_JOB_CONTROL_WAIT_COMPLETED,
    SLURM_JOB_CONTROL_WAIT_TIMEOUT,
    build_slurm_jobs_table,
    build_slurm_wait_dashboard,
    prompt_slurm_job_control,
)
from .slurm_jobs import (
    AffectedSlurmJob,
    affected_slurm_partitions_from_scontrol_show_node,
    dedupe_slurm_jobs,
    ensure_requeueable_slurm_jobs,
    filter_affected_pending_slurm_jobs,
    parse_squeue_jobs,
    selected_display_job_ids,
)
from .soperator_gpu_driver_jail import (
    SOPERATOR_GPU_DRIVER_JAIL_INIT_CONTAINER_NAME,
    ensure_soperator_gpu_driver_jail_values,
    normalize_soperator_gpu_driver_jail_mounts,
)
from .soperator_jail_capacity import (
    JailCapacityPreflight,
    capacity_preflight_check_payload,
    probe_active_passive_jail_capacity,
)
from .soperator_jail_mounts import (
    JAIL_EXTERNAL_AUTO_PERSISTENT_MOUNT_PATHS,
    JAIL_EXTERNAL_SYSTEM_PATH,
    JAIL_LEGACY_ROOT_PATH,
    JAIL_PERSISTENT_MOUNTS_VALUES_KEY,
    apply_jail_persistent_mount_values,
    jail_persistent_mount_decisions,
    jail_persistent_mount_exclude_paths,
    jail_persistent_mount_status,
    jail_rootfs_uses_legacy_active_source,
    parse_jail_persistent_mount_specs,
)
from .soperator_onboarding import (
    ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
    ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
    ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
    ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
    ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
    ONBOARDING_ACTION_UPGRADE_SOPERATOR,
    ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_GPU_STACK_PRESET,
    ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION,
    ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_OS,
    SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA,
    SOPERATOR_MIGRATION_PROFILE_DATA_FILE,
    analyze_soperator_onboarding_snapshot,
    normalize_soperator_release_version,
    soperator_migration_profile_group,
    soperator_onboarding_target,
)
from .soperator_populate_jail import (
    POPULATE_JAIL_REFRESH_PHASE_ID,
    active_passive_jail_rootfs_slots,
    active_passive_pod_scheduling_fields,
    active_passive_populate_jail_job_manifest,
    active_passive_populate_jail_job_scheduling,
    completed_populate_jail_refresh_result,
    inspect_populate_jail,
    login_service_ready_endpoint_count,
    manual_populate_jail_refresh_result,
    normalize_populate_jail_refresh_mode,
    plan_populate_jail_refresh,
    populate_jail_refresh_values,
    skipped_populate_jail_refresh_result,
    switch_active_passive_jail_rootfs_values,
    wait_for_active_passive_populate_jail_job,
    wait_for_login_service_ready_endpoints,
    wait_for_login_statefulset_rollout_with_ready_endpoint_guard,
)
from .soperator_upgrade_safety import (
    ProtectedCustomerState,
    build_stage_fast_verification_payload,
    capture_protected_customer_state,
    protected_customer_state_from_payload,
    run_post_upgrade_fast_verification,
    safety_report_markdown_lines,
    stage_fast_verification_check,
    stage_fast_verification_failed,
    stage_fast_verification_markdown_lines,
    stage_fast_verification_report,
    stage_fast_verification_status,
    update_safety_payload_with_before,
    update_safety_payload_with_verification,
    upgrade_safety_checkpoint_payload,
)
from .soperator_validation import (
    SOPERATOR_CLUSTER_VALIDATION_KIND,
    SoperatorValidationCommandResult,
    run_soperator_cluster_validations,
    soperator_cluster_validation_specs,
)

SOPERATOR_MIGRATION_EXECUTION_SCHEMA = "nebius-cxcli-ext-soperator-upgrade-execution/v2"
SOPERATOR_MIGRATION_REPORT_SCHEMA = "nebius-cxcli-ext-soperator-upgrade-report/v2"
SOPERATOR_MIGRATION_CHECKPOINT_DIR = ".nebius-cxcli/ext-soperator-upgrades"
LEGACY_SOPERATOR_MIGRATION_CHECKPOINT_DIR = ".nebius-cxcli/soperator-migrations"
MIGRATE_REPORT_FILENAME = "ext-soperator-upgrade-report.md"
UPGRADE_REPORT_JSON_FILENAME = "ext-soperator-upgrade-report.json"
EXT_SOPERATOR_UPGRADE_SEGMENT_REPORT_DIRNAME = "ext-soperator-upgrades"
_MUTATING_PHASE_IDS = frozenset(
    {
        "external-node-template-upgrade",
        "target-gpu-stack-remediation",
        "create-aligned-sfs",
        "online-bulk-data-sync",
        "rolling-compute-migration",
        "final-control-plane-cutover",
        POPULATE_JAIL_REFRESH_PHASE_ID,
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
    POPULATE_JAIL_REFRESH_PHASE_ID,
    "validation-and-rollback-hold",
    "retire-old-resources",
    "post-upgrade-mk8s-check",
    "post-upgrade-helm-check",
)
_SUPPORTED_EXECUTE_PHASE_IDS = frozenset(_ORDERED_EXECUTE_PHASE_IDS)
_SOPERATOR_STORAGE_KEYS = ("jail", "controller-spool", "accounting")
_HOME_MOUNT_PROBE_SCRIPT = r"""
path=/home
if command -v findmnt >/dev/null 2>&1; then
    if output=$(findmnt -T "$path" -n -o TARGET,SOURCE,FSTYPE 2>/dev/null); then
        printf '%s\n' "$output"
        exit 0
    fi
fi
best=
best_line=
while IFS= read -r line; do
    left=${line%% - *}
    set -- $left
    if [ "$#" -lt 5 ]; then
        continue
    fi
    mount_point=$5
    case "$path" in
        "$mount_point"|"$mount_point"/*)
            if [ ${#mount_point} -gt ${#best} ]; then
                best=$mount_point
                best_line=$line
            fi
            ;;
    esac
done < /proc/self/mountinfo
if [ -n "$best_line" ]; then
    right=${best_line#* - }
    set -- $right
    fstype=${1:-unknown}
    source=${2:-unknown}
    printf '%s %s %s\n' "$best" "$source" "$fstype"
    exit 0
fi
echo "no mount evidence for /home" >&2
exit 1
"""
_SOPERATOR_SERVICE_ROLES = ("system", "controller", "login", "accounting")
_SOPERATOR_COMPUTE_ROLES = (*_SOPERATOR_SERVICE_ROLES, "worker")
_TARGET_GPU_STACK_PHASE_ID = "target-gpu-stack-remediation"
_EXTERNAL_NODE_TEMPLATE_PHASE_ID = "external-node-template-upgrade"
_POST_UPGRADE_CHECK_PHASE_IDS = ("post-upgrade-mk8s-check", "post-upgrade-helm-check")
_RESUME_OPTIONAL_PLANNED_PHASE_IDS = frozenset(
    {
        "final-control-plane-cutover",
        POPULATE_JAIL_REFRESH_PHASE_ID,
        "validation-and-rollback-hold",
        "retire-old-resources",
        "post-upgrade-mk8s-check",
        "post-upgrade-helm-check",
    }
)
_EXTERNAL_UPGRADE_TOP_LEVEL_STAGE_MK8S = "MK8s Node Upgrades"
_EXTERNAL_UPGRADE_TOP_LEVEL_STAGE_SOPERATOR = "Soperator Upgrade"
_EXTERNAL_UPGRADE_TOP_LEVEL_STAGE_JAIL = "Jail Upgrade"
_EXTERNAL_UPGRADE_MK8S_TOP_LEVEL_PHASE_IDS = frozenset(
    {
        _EXTERNAL_NODE_TEMPLATE_PHASE_ID,
        _TARGET_GPU_STACK_PHASE_ID,
        "post-upgrade-mk8s-check",
    }
)
_EXTERNAL_UPGRADE_JAIL_TOP_LEVEL_PHASE_IDS = frozenset({POPULATE_JAIL_REFRESH_PHASE_ID})
_FAST_STAGE_VERIFICATION_PHASE_IDS = frozenset(
    (*_MUTATING_PHASE_IDS, *_POST_UPGRADE_CHECK_PHASE_IDS)
)
_EXTERNAL_UPGRADE_BACKUP_REQUIRED_CATEGORIES = frozenset(
    {"accounting", "generated", "kubernetes", "recreation", "slurm", "soperator", "source"}
)
_EXTERNAL_UPGRADE_BACKUP_REQUIRED_FIELDS = (
    "path",
    "sha256",
    "manifest_sha256",
    "included_categories",
)
_STATUS_PHASE_LABELS = {
    "discovery-and-plan": "Discovery and upgrade plan",
    "customer-approval": "Customer approval gate",
    _EXTERNAL_NODE_TEMPLATE_PHASE_ID: "External node-template upgrade",
    _TARGET_GPU_STACK_PHASE_ID: "Target GPU stack reconciliation",
    "create-aligned-sfs": "Aligned SFS creation",
    "online-bulk-data-sync": "Online bulk data sync",
    "rolling-compute-migration": "Rolling compute migration",
    "final-control-plane-cutover": "Final control-plane cutover",
    POPULATE_JAIL_REFRESH_PHASE_ID: "Jail Upgrade",
    "validation-and-rollback-hold": "Validation and rollback hold",
    "retire-old-resources": "Retire old resources",
    "post-upgrade-mk8s-check": "Post-upgrade MK8s check",
    "post-upgrade-helm-check": "Post-upgrade Helm check",
}


def external_soperator_upgrade_top_level_stage(phase_id: str) -> str:
    if phase_id in _EXTERNAL_UPGRADE_MK8S_TOP_LEVEL_PHASE_IDS:
        return _EXTERNAL_UPGRADE_TOP_LEVEL_STAGE_MK8S
    if phase_id in _EXTERNAL_UPGRADE_JAIL_TOP_LEVEL_PHASE_IDS:
        return _EXTERNAL_UPGRADE_TOP_LEVEL_STAGE_JAIL
    return _EXTERNAL_UPGRADE_TOP_LEVEL_STAGE_SOPERATOR


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
SOPERATOR_SERVICE_ROLE_ROLLOUT_DEFAULT_STRATEGY = SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE
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
SOPERATOR_LOGIN_LB_ALLOCATION_ANNOTATION = "nebius.com/load-balancer-allocation-id"
SOPERATOR_LOGIN_LB_TYPE_ANNOTATION = "nebius.com/load-balancer-type"
SOPERATOR_LOGIN_LB_TYPE_INTERNAL = "internal"
SOPERATOR_LOGIN_LB_TYPE_EXTERNAL = "external"
_NEBIUS_MANAGED_BY_LABEL = "nebius.com/managed-by"
_SECURITY_PROFILES_OPERATOR_NAMESPACE = "security-profiles-operator-system"
_SECURITY_PROFILES_OPERATOR_WEBHOOK_RESOURCE = "deployment/security-profiles-operator-webhook"
_SOPERATOR_TARGET_RELEASE_NAME = "soperator"
_SOPERATOR_CONTROLLER_POD = "controller-0"
_SOPERATOR_CONTROLLER_CONTAINER = "slurmctld"
_SOPERATOR_LEGACY_SLURM_CONF = "/mnt/jail/etc/slurm/slurm.conf"
_SOPERATOR_SLURM_CLI_NAMES = frozenset(
    {"sacct", "sbatch", "scancel", "scontrol", "sinfo", "squeue", "srun"}
)
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
_TARGET_KUBE_RBAC_PROXY_REPOSITORY = "registry.k8s.io/kubebuilder/kube-rbac-proxy"
_TARGET_KUBE_RBAC_PROXY_TAG = "v0.15.0"
_ROLLING_COMPUTE_VALUES_REVISION = 14
_VALIDATION_HOLD_REVISION = 2
_TARGET_SLURM_PLUGIN_DIR = "/usr/lib/x86_64-linux-gnu/slurm"
_TARGET_GPU_GRES_AFFINITY_PARAMETER = "l3cache_as_socket"
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
_console = Console()
_EXTERNAL_UPGRADE_JOB_POLICIES = frozenset(
    {
        "interactive",
        "wait-to-finish",
        "wait-then-cancel",
        "fail",
        "cancel-selected",
        "cancel-all",
        "requeue-selected",
        "requeue-all",
        "requeue-hold-selected",
        "requeue-hold-all",
    }
)
_EXTERNAL_UPGRADE_DEFAULT_JOB_WAIT_TIMEOUT_SECONDS = 3600
_EXTERNAL_UPGRADE_DEFAULT_JOB_REFRESH_INTERVAL_SECONDS = 30
_EXTERNAL_UPGRADE_CANCEL_CLEAR_TIMEOUT_SECONDS = 300
EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY = "target-ready"
EXTERNAL_LOGIN_SESSION_POLICY_WAIT_ACTIVE = "wait-active"
EXTERNAL_LOGIN_SESSION_POLICY_GRACE_PERIOD = "grace-period"
EXTERNAL_LOGIN_SESSION_POLICIES = frozenset(
    {
        EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY,
        EXTERNAL_LOGIN_SESSION_POLICY_WAIT_ACTIVE,
        EXTERNAL_LOGIN_SESSION_POLICY_GRACE_PERIOD,
    }
)
EXTERNAL_LOGIN_SESSION_DRAIN_TIMEOUT_SECONDS = 30 * 60


def normalize_external_login_session_policy(policy: str | None) -> str:
    resolved = str(policy or "").strip() or EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY
    if resolved not in EXTERNAL_LOGIN_SESSION_POLICIES:
        raise ValueError(
            "--login-session-policy must be one of: "
            + ", ".join(sorted(EXTERNAL_LOGIN_SESSION_POLICIES))
        )
    return resolved


class SoperatorMigrationPhasePending(RuntimeError):
    """Checkpointed upgrade phase pending before an unsafe mutation."""


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
        """Run an external command for a live upgrade phase."""


class SoperatorMigrationNebiusApi(Protocol):
    def get_filesystem_by_name(self, *, project_id: str, name: str) -> Mapping[str, Any]:
        """Return an existing filesystem by project/name, or an empty mapping."""

    def create_filesystem(
        self,
        *,
        project_id: str,
        spec: SoperatorAlignedFilesystemSpec,
        timeout_seconds: int = 1800,
    ) -> Mapping[str, Any]:
        """Create a filesystem and return its payload."""

    def get_node_group(self, node_group_id: str) -> Mapping[str, Any]:
        """Return a node-group payload."""

    def list_node_groups(self, cluster_id: str) -> tuple[Mapping[str, Any], ...]:
        """Return node groups for a cluster."""

    def update_node_group(
        self,
        *,
        node_group_id: str,
        original_node_group: Mapping[str, Any],
        update_args: Sequence[str],
        strategy_args: Sequence[str],
        clear_template_gpu_settings: bool = False,
        timeout_seconds: int = 2700,
    ) -> Mapping[str, Any]:
        """Update a node group and return its payload."""

    def create_node_group(
        self,
        *,
        payload: Mapping[str, Any],
        timeout_seconds: int = 3600,
    ) -> Mapping[str, Any]:
        """Create a node group and return its payload."""

    def scale_node_group(
        self,
        *,
        node_group_id: str,
        count: int,
        timeout_seconds: int = 3000,
    ) -> None:
        """Set a node group's fixed node count."""

    def delete_node_group(
        self,
        *,
        node_group_id: str,
        timeout_seconds: int = 3000,
    ) -> None:
        """Delete a node group."""

    def get_cluster(self, cluster_id: str) -> Mapping[str, Any]:
        """Return a cluster payload."""

    def update_cluster_control_plane(
        self,
        *,
        cluster_id: str,
        control_plane_version: str,
        timeout_seconds: int = 3600,
    ) -> Mapping[str, Any]:
        """Update a cluster control-plane version and return its payload."""

    def list_allocations(self, *, project_id: str) -> tuple[Mapping[str, Any], ...]:
        """Return VPC allocations for a project."""

    def get_allocation(self, allocation_id: str) -> Mapping[str, Any]:
        """Return one VPC allocation payload."""

    def update_allocation_labels(
        self,
        *,
        allocation_id: str,
        original_allocation: Mapping[str, Any],
        labels: Mapping[str, str],
        timeout_seconds: int = 300,
    ) -> Mapping[str, Any]:
        """Update a VPC allocation's labels and return the updated payload."""

    def close(self) -> None:
        """Release any SDK resources owned by this API object."""


JailSfsResizeHandler = Callable[
    [
        JailCapacityPreflight,
        Callable[[], JailCapacityPreflight],
        dict[str, Any],
        dict[str, Any],
        Callable[[], None] | None,
    ],
    JailCapacityPreflight,
]


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
    service_role_strategy: str = SOPERATOR_SERVICE_ROLE_ROLLOUT_DEFAULT_STRATEGY
    worker_wave_groups: int | None = None
    worker_wave_percent: int | None = None
    max_parallel_worker_groups: int | None = None
    service_role_max_surge_count: int = (
        SOPERATOR_SAFE_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_SURGE_COUNT
    )
    service_role_max_unavailable_count: int = (
        SOPERATOR_SAFE_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_UNAVAILABLE_COUNT
    )
    service_role_drain_timeout: str = SOPERATOR_WORKER_GROUP_STRATEGY_DEFAULT_DRAIN_TIMEOUT
    strategy_max_surge_count: int = (
        SOPERATOR_ZERO_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_SURGE_COUNT
    )
    strategy_max_unavailable_count: int = (
        SOPERATOR_ZERO_SURGE_WORKER_GROUP_STRATEGY_DEFAULT_MAX_UNAVAILABLE_COUNT
    )
    strategy_drain_timeout: str = SOPERATOR_WORKER_GROUP_STRATEGY_DEFAULT_DRAIN_TIMEOUT

    def to_manifest_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "strategy": self.strategy,
            "service_role_strategy": self.service_role_strategy,
        }
        if self.worker_wave_groups is not None:
            result["worker_wave_groups"] = self.worker_wave_groups
        if self.worker_wave_percent is not None:
            result["worker_wave_percent"] = self.worker_wave_percent
        if self.max_parallel_worker_groups is not None:
            result["max_parallel_worker_groups"] = self.max_parallel_worker_groups
        result["service_role_group_strategy"] = {
            "max_surge_count": self.service_role_max_surge_count,
            "max_unavailable_count": self.service_role_max_unavailable_count,
            "drain_timeout": self.service_role_drain_timeout,
        }
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
class SoperatorLoginLoadBalancerAllocationDecision:
    service_name: str
    status: str
    address: str = ""
    load_balancer_type: str = SOPERATOR_LOGIN_LB_TYPE_EXTERNAL
    allocation_id: str = ""
    allocation_cidr: str = ""
    removed_labels: tuple[str, ...] = ()
    persisted_to_values: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "service_name": self.service_name,
            "status": self.status,
            "address": self.address,
            "load_balancer_type": self.load_balancer_type,
            "allocation_id": self.allocation_id,
            "allocation_cidr": self.allocation_cidr,
            "removed_labels": list(self.removed_labels),
            "persisted_to_values": self.persisted_to_values,
        }


def _sdk_not_found_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            "not found",
            "not_found",
            "notfound",
            "statuscode.not_found",
            "code = not_found",
        )
    )


def _sdk_field_paths_for_node_group_update(
    *,
    update_args: Sequence[str],
    strategy_args: Sequence[str],
    clear_template_gpu_settings: bool,
) -> tuple[str, ...]:
    command = tuple(str(item) for item in update_args)
    paths: list[str] = []
    if "--version" in command:
        paths.append("spec.version")
    if "--template-os" in command:
        paths.append("spec.template.os")
    if "--template-filesystems" in command:
        paths.append("spec.template.filesystems")
    if "--template-gpu-settings-drivers-preset" in command or clear_template_gpu_settings:
        paths.append("spec.template.gpu_settings")
    if strategy_args:
        paths.append("spec.strategy")
    return tuple(dict.fromkeys(paths))


def _sdk_request_mask(paths: Sequence[str]) -> Any:
    from nebius.base.fieldmask import Mask

    return Mask.unmarshal(",".join(paths))


def _filesystem_type_sdk_name(value: str) -> str:
    normalized = _nebius_filesystem_type(value).upper()
    return normalized if normalized else "NETWORK_SSD"


class _SdkSoperatorMigrationNebiusApi:
    def __init__(self, *, project_id: str) -> None:
        sdk = init_nebius_sdk(
            parent_id=project_id,
            context="external Soperator migration Nebius API",
            prefer_operator_auth=True,
        )
        from nebius.api.nebius.compute.v1 import FilesystemServiceClient
        from nebius.api.nebius.mk8s.v1 import ClusterServiceClient, NodeGroupServiceClient
        from nebius.api.nebius.vpc.v1 import AllocationServiceClient

        self._sdk = sdk
        self._filesystem_client = FilesystemServiceClient(sdk)
        self._cluster_client = ClusterServiceClient(sdk)
        self._node_group_client = NodeGroupServiceClient(sdk)
        self._allocation_client = AllocationServiceClient(sdk)

    def get_filesystem_by_name(self, *, project_id: str, name: str) -> Mapping[str, Any]:
        from nebius.api.nebius.common.v1 import GetByNameRequest

        try:
            with suppress_expected_refresh_logs():
                filesystem = self._filesystem_client.get_by_name(
                    GetByNameRequest(parent_id=project_id, name=name)
                ).wait()
        except Exception as exc:
            if _sdk_not_found_error(exc):
                return {}
            raise RuntimeError(f"Could not read aligned SFS filesystem '{name}': {exc}") from exc
        return sdk_message_to_mapping(filesystem)

    def create_filesystem(
        self,
        *,
        project_id: str,
        spec: SoperatorAlignedFilesystemSpec,
        timeout_seconds: int = 1800,
    ) -> Mapping[str, Any]:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.compute.v1 import CreateFilesystemRequest, FilesystemSpec

        request = CreateFilesystemRequest(
            metadata=ResourceMetadata(parent_id=project_id, name=spec.name),
            spec=FilesystemSpec(
                size_gibibytes=spec.size_gib,
                block_size_bytes=spec.block_size_kib * 1024,
                type=getattr(
                    FilesystemSpec.FilesystemType,
                    _filesystem_type_sdk_name(spec.filesystem_type),
                ),
                forbid_deletion=spec.forbid_deletion,
            ),
        )
        with suppress_expected_refresh_logs():
            operation = self._filesystem_client.create(request).wait()
            wait_nebius_operation(
                operation,
                timeout_seconds=timeout_seconds,
                action=f"Nebius filesystem create {spec.name}",
            )
        created = self.get_filesystem_by_name(project_id=project_id, name=spec.name)
        if not created:
            raise RuntimeError(f"Aligned SFS filesystem '{spec.name}' was created but not found.")
        return created

    def get_node_group(self, node_group_id: str) -> Mapping[str, Any]:
        from nebius.api.nebius.mk8s.v1 import GetNodeGroupRequest

        with suppress_expected_refresh_logs():
            node_group = self._node_group_client.get(
                GetNodeGroupRequest(id=node_group_id)
            ).wait()
        payload = sdk_message_to_mapping(node_group)
        if not payload:
            raise RuntimeError(f"Nebius node group {node_group_id} did not return a payload.")
        return payload

    def list_node_groups(self, cluster_id: str) -> tuple[Mapping[str, Any], ...]:
        from nebius.api.nebius.mk8s.v1 import ListNodeGroupsRequest

        items: list[Mapping[str, Any]] = []
        token = ""
        while True:
            with suppress_expected_refresh_logs():
                response = self._node_group_client.list(
                    ListNodeGroupsRequest(parent_id=cluster_id, page_token=token or None)
                ).wait()
            for item in getattr(response, "items", []) or []:
                payload = sdk_message_to_mapping(item)
                if payload:
                    items.append(payload)
            token = str(getattr(response, "next_page_token", "") or "").strip()
            if not token:
                return tuple(items)

    def update_node_group(
        self,
        *,
        node_group_id: str,
        original_node_group: Mapping[str, Any],
        update_args: Sequence[str],
        strategy_args: Sequence[str],
        clear_template_gpu_settings: bool = False,
        timeout_seconds: int = 2700,
    ) -> Mapping[str, Any]:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.mk8s.v1 import NodeGroupSpec, UpdateNodeGroupRequest

        mask_paths = _sdk_field_paths_for_node_group_update(
            update_args=update_args,
            strategy_args=strategy_args,
            clear_template_gpu_settings=clear_template_gpu_settings,
        )
        if not mask_paths:
            return self.get_node_group(node_group_id)
        payload = _node_group_full_update_payload(
            original_node_group=original_node_group,
            update_args=update_args,
            strategy_args=strategy_args,
            clear_template_gpu_settings=clear_template_gpu_settings,
        )
        metadata = _mapping(original_node_group.get("metadata"))
        parent_id = str(metadata.get("parent_id", metadata.get("parentId", "")) or "").strip()
        name = str(metadata.get("name", "") or "").strip()
        request_metadata: dict[str, str] = {"id": node_group_id}
        if parent_id:
            request_metadata["parent_id"] = parent_id
        if name:
            request_metadata["name"] = name
        request = UpdateNodeGroupRequest(
            metadata=ResourceMetadata(**request_metadata),
            spec=sdk_parse_message(NodeGroupSpec, _mapping(payload.get("spec"))),
        )
        request.set_mask(_sdk_request_mask(mask_paths))
        with suppress_expected_refresh_logs():
            operation = self._node_group_client.update(request).wait()
            wait_nebius_operation(
                operation,
                timeout_seconds=timeout_seconds,
                action=f"Nebius node-group update {node_group_id}",
            )
        return self.get_node_group(node_group_id)

    def create_node_group(
        self,
        *,
        payload: Mapping[str, Any],
        timeout_seconds: int = 3600,
    ) -> Mapping[str, Any]:
        from nebius.api.nebius.common.v1 import GetByNameRequest, ResourceMetadata
        from nebius.api.nebius.mk8s.v1 import CreateNodeGroupRequest, NodeGroupSpec

        metadata = _mapping(payload.get("metadata"))
        cluster_id = str(metadata.get("parent_id", metadata.get("parentId", "")) or "").strip()
        name = str(metadata.get("name", "") or "").strip()
        if not cluster_id or not name:
            raise RuntimeError("Nebius node-group create requires metadata.parent_id and name.")
        request = CreateNodeGroupRequest(
            metadata=ResourceMetadata(parent_id=cluster_id, name=name),
            spec=sdk_parse_message(NodeGroupSpec, _mapping(payload.get("spec"))),
        )
        with suppress_expected_refresh_logs():
            operation = self._node_group_client.create(request).wait()
            wait_nebius_operation(
                operation,
                timeout_seconds=timeout_seconds,
                action=f"Nebius node-group create {name}",
            )
            node_group = self._node_group_client.get_by_name(
                GetByNameRequest(parent_id=cluster_id, name=name)
            ).wait()
        return sdk_message_to_mapping(node_group)

    def scale_node_group(
        self,
        *,
        node_group_id: str,
        count: int,
        timeout_seconds: int = 3000,
    ) -> None:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.mk8s.v1 import NodeGroupSpec, UpdateNodeGroupRequest

        node_group = self.get_node_group(node_group_id)
        metadata = _mapping(node_group.get("metadata"))
        parent_id = str(metadata.get("parent_id", metadata.get("parentId", "")) or "").strip()
        name = str(metadata.get("name", "") or "").strip()
        request_metadata: dict[str, str] = {"id": node_group_id}
        if parent_id:
            request_metadata["parent_id"] = parent_id
        if name:
            request_metadata["name"] = name
        request = UpdateNodeGroupRequest(
            metadata=ResourceMetadata(**request_metadata),
            spec=NodeGroupSpec(fixed_node_count=count),
        )
        request.set_mask(_sdk_request_mask(("spec.fixed_node_count",)))
        try:
            with suppress_expected_refresh_logs():
                operation = self._node_group_client.update(request).wait()
                wait_nebius_operation(
                    operation,
                    timeout_seconds=timeout_seconds,
                    action=f"Nebius node-group scale {node_group_id}",
                )
        except Exception as exc:
            if _sdk_not_found_error(exc):
                return
            raise RuntimeError(f"Could not scale node group {node_group_id}: {exc}") from exc

    def delete_node_group(
        self,
        *,
        node_group_id: str,
        timeout_seconds: int = 3000,
    ) -> None:
        from nebius.api.nebius.mk8s.v1 import DeleteNodeGroupRequest

        try:
            with suppress_expected_refresh_logs():
                operation = self._node_group_client.delete(
                    DeleteNodeGroupRequest(id=node_group_id)
                ).wait()
                wait_nebius_operation(
                    operation,
                    timeout_seconds=timeout_seconds,
                    action=f"Nebius node-group delete {node_group_id}",
                )
        except Exception as exc:
            if _sdk_not_found_error(exc):
                return
            raise RuntimeError(f"Could not delete node group {node_group_id}: {exc}") from exc

    def get_cluster(self, cluster_id: str) -> Mapping[str, Any]:
        from nebius.api.nebius.mk8s.v1 import GetClusterRequest

        with suppress_expected_refresh_logs():
            cluster = self._cluster_client.get(GetClusterRequest(id=cluster_id)).wait()
        payload = sdk_message_to_mapping(cluster)
        if not payload:
            raise RuntimeError(f"Nebius MK8s cluster {cluster_id} did not return a payload.")
        return payload

    def update_cluster_control_plane(
        self,
        *,
        cluster_id: str,
        control_plane_version: str,
        timeout_seconds: int = 3600,
    ) -> Mapping[str, Any]:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.mk8s.v1 import (
            ClusterSpec,
            ControlPlaneEndpointsSpec,
            ControlPlaneSpec,
            PublicEndpointSpec,
            UpdateClusterRequest,
        )

        current = self.get_cluster(cluster_id)
        metadata = _mapping(current.get("metadata"))
        spec = _mapping(current.get("spec"))
        status = _mapping(current.get("status"))
        control_plane = _mapping(spec.get("control_plane") or spec.get("controlPlane"))
        status_control_plane = _mapping(
            status.get("control_plane") or status.get("controlPlane")
        )
        parent_id = str(metadata.get("parent_id", metadata.get("parentId", "")) or "").strip()
        name = str(metadata.get("name", "") or "").strip()
        subnet_id = str(
            control_plane.get("subnet_id", control_plane.get("subnetId", "")) or ""
        ).strip()
        control_plane_endpoints = _mapping(control_plane.get("endpoints"))
        status_control_plane_endpoints = _mapping(status_control_plane.get("endpoints"))
        has_public_endpoint = bool(
            _mapping(
                control_plane_endpoints.get("public_endpoint")
                or control_plane_endpoints.get("publicEndpoint")
            )
            or str(
                status_control_plane_endpoints.get(
                    "public_endpoint",
                    status_control_plane_endpoints.get("publicEndpoint", ""),
                )
                or ""
            ).strip()
        )
        request_metadata: dict[str, str] = {"id": cluster_id}
        if parent_id:
            request_metadata["parent_id"] = parent_id
        if name:
            request_metadata["name"] = name
        request_control_plane: dict[str, Any] = {"version": control_plane_version}
        if subnet_id:
            request_control_plane["subnet_id"] = subnet_id
        if has_public_endpoint:
            request_control_plane["endpoints"] = ControlPlaneEndpointsSpec(
                public_endpoint=PublicEndpointSpec()
            )
        request = UpdateClusterRequest(
            metadata=ResourceMetadata(**request_metadata),
            spec=ClusterSpec(control_plane=ControlPlaneSpec(**request_control_plane)),
        )
        request.set_mask(_sdk_request_mask(("spec.control_plane.version",)))
        with suppress_expected_refresh_logs():
            operation = self._cluster_client.update(request).wait()
            wait_nebius_operation(
                operation,
                timeout_seconds=timeout_seconds,
                action=f"Nebius MK8s cluster control-plane update {cluster_id}",
            )
        return self.get_cluster(cluster_id)

    def list_allocations(self, *, project_id: str) -> tuple[Mapping[str, Any], ...]:
        from nebius.api.nebius.vpc.v1 import ListAllocationsRequest

        items: list[Mapping[str, Any]] = []
        token = ""
        while True:
            with suppress_expected_refresh_logs():
                response = self._allocation_client.list(
                    ListAllocationsRequest(
                        parent_id=project_id,
                        page_size=999,
                        page_token=token or None,
                    )
                ).wait()
            for item in getattr(response, "items", []) or []:
                payload = sdk_message_to_mapping(item)
                if payload:
                    items.append(payload)
            token = str(getattr(response, "next_page_token", "") or "").strip()
            if not token:
                return tuple(items)

    def get_allocation(self, allocation_id: str) -> Mapping[str, Any]:
        from nebius.api.nebius.vpc.v1 import GetAllocationRequest

        with suppress_expected_refresh_logs():
            allocation = self._allocation_client.get(GetAllocationRequest(id=allocation_id)).wait()
        payload = sdk_message_to_mapping(allocation)
        if not payload:
            raise RuntimeError(f"Nebius VPC allocation {allocation_id} did not return a payload.")
        return payload

    def update_allocation_labels(
        self,
        *,
        allocation_id: str,
        original_allocation: Mapping[str, Any],
        labels: Mapping[str, str],
        timeout_seconds: int = 300,
    ) -> Mapping[str, Any]:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.vpc.v1 import UpdateAllocationRequest

        metadata = _mapping(original_allocation.get("metadata"))
        parent_id = str(metadata.get("parent_id", metadata.get("parentId", "")) or "").strip()
        name = str(metadata.get("name", "") or "").strip()
        resource_version = _positive_int(metadata.get("resource_version"), fallback=0)
        request_metadata: dict[str, Any] = {
            "id": allocation_id,
            "labels": dict(labels),
        }
        if parent_id:
            request_metadata["parent_id"] = parent_id
        if name:
            request_metadata["name"] = name
        if resource_version:
            request_metadata["resource_version"] = resource_version
        request = UpdateAllocationRequest(metadata=ResourceMetadata(**request_metadata))
        request.set_mask(_sdk_request_mask(("metadata.labels",)))
        with suppress_expected_refresh_logs():
            operation = self._allocation_client.update(request).wait()
            wait_nebius_operation(
                operation,
                timeout_seconds=timeout_seconds,
                action=f"Nebius VPC allocation label update {allocation_id}",
            )
        return self.get_allocation(allocation_id)

    def close(self) -> None:
        with suppress(Exception):
            self._sdk.sync_close()


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


def _unsupported_checkpoint_schema_message(path: Path) -> str:
    return (
        "Unsupported external Soperator upgrade checkpoint schema in "
        f"{path}. This cxcli version requires checkpoint schema "
        f"{SOPERATOR_MIGRATION_EXECUTION_SCHEMA} with a full locked_upgrade_path "
        "snapshot. Old progress-only checkpoints cannot be resumed; rerun "
        "`nebius-cxcli ext-soperator onboard` only as an intentional repair/replan "
        "path, or remove the checkpoint after reviewing recovery state."
    )


def _locked_upgrade_path_repair_message() -> str:
    return (
        "External Soperator upgrade checkpoint cannot resume because it does not "
        "contain the full locked_upgrade_path snapshot. Old progress-only "
        "checkpoints are not supported; rerun `nebius-cxcli ext-soperator onboard` "
        "only as an intentional repair/replan path, or remove the checkpoint after "
        "reviewing recovery state."
    )


def _checkpoint_locked_upgrade_path(checkpoint: Mapping[str, Any] | None) -> dict[str, Any]:
    locked_path = (checkpoint or {}).get("locked_upgrade_path")
    if not isinstance(locked_path, Mapping):
        return {}
    schema = str(locked_path.get("schema", "") or "").strip()
    if schema and schema != SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA:
        raise RuntimeError(
            "External Soperator upgrade checkpoint contains locked_upgrade_path schema "
            f"{schema}, but this cxcli requires {SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA}. "
            "Rerun `nebius-cxcli ext-soperator onboard` as an intentional repair/replan path, "
            "or remove the checkpoint after reviewing recovery state."
        )
    segments = locked_path.get("segments")
    if not isinstance(segments, list) or not segments:
        return {}
    payload = to_plain_data(dict(locked_path))
    return dict(payload) if isinstance(payload, Mapping) else {}


def _checkpoint_has_progress_only_locked_path(checkpoint: Mapping[str, Any] | None) -> bool:
    if not isinstance(checkpoint, Mapping) or _checkpoint_locked_upgrade_path(checkpoint):
        return False
    path_state = checkpoint.get("upgrade_path")
    if isinstance(path_state, Mapping) and any(
        key in path_state
        for key in (
            "fingerprint",
            "current_segment_id",
            "completed_segment_ids",
            "segment_state",
        )
    ):
        return True
    return any(
        key in checkpoint
        for key in (
            "upgrade_path_fingerprint",
            "current_segment_id",
            "completed_segment_ids",
            "segment_state",
        )
    )


def _checkpoint_upgrade_path_progress(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    segment_state = _mapping(checkpoint.get("segment_state"))
    return {
        "fingerprint": str(checkpoint.get("upgrade_path_fingerprint", "") or ""),
        "current_segment_id": str(checkpoint.get("current_segment_id", "") or ""),
        "completed_segment_ids": [
            str(segment_id or "").strip()
            for segment_id in checkpoint.get("completed_segment_ids", []) or []
            if str(segment_id or "").strip()
        ],
        "pending_phase": str(checkpoint.get("pending_phase", "") or ""),
        "segment_state": to_plain_data(segment_state),
    }


def ext_soperator_upgrade_segment_report_paths(
    config_path: Path,
    target_ref: str,
    segment_id: str,
) -> tuple[Path, Path]:
    normalized_target = normalize_component_token(target_ref) or "target"
    normalized_segment = normalize_component_token(segment_id) or "segment"
    report_dir = (
        config_path.parent
        / "generated"
        / "reports"
        / EXT_SOPERATOR_UPGRADE_SEGMENT_REPORT_DIRNAME
        / normalized_target
        / normalized_segment
    )
    return report_dir / "report.md", report_dir / "report.json"


def _locked_upgrade_path_segment_history(
    *,
    config_path: Path,
    target_ref: str,
    checkpoint: Mapping[str, Any],
    locked_upgrade_path: Mapping[str, Any],
) -> list[dict[str, Any]]:
    segments = locked_upgrade_path.get("segments")
    if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes, bytearray)):
        return []
    completed = {
        str(segment_id or "").strip()
        for segment_id in checkpoint.get("completed_segment_ids", []) or []
        if str(segment_id or "").strip()
    }
    current_segment_id = str(checkpoint.get("current_segment_id", "") or "").strip()
    pending_phase = str(checkpoint.get("pending_phase", "") or "").strip()
    segment_state = _mapping(checkpoint.get("segment_state"))
    checkpoint_backup = _mapping(checkpoint.get("backup"))
    history: list[dict[str, Any]] = []
    for raw_segment in segments:
        if not isinstance(raw_segment, Mapping):
            continue
        segment_id = str(raw_segment.get("id", "") or "").strip()
        if not segment_id:
            continue
        state = _mapping(segment_state.get(segment_id))
        report_path, json_report_path = ext_soperator_upgrade_segment_report_paths(
            config_path,
            target_ref,
            segment_id,
        )
        if segment_id in completed:
            status = "completed"
        elif segment_id == current_segment_id and (
            (pending_phase and pending_phase != "none") or state
        ):
            status = "current"
        else:
            status = "remaining"
        backup_path = str(state.get("backup_path", "") or checkpoint_backup.get("path", "") or "")
        history.append(
            {
                "id": segment_id,
                "title": str(raw_segment.get("title", "") or segment_id),
                "status": status,
                "current_k8s_version": str(raw_segment.get("current_k8s_version", "") or ""),
                "target_k8s_version": str(raw_segment.get("target_k8s_version", "") or ""),
                "soperator_app": to_plain_data(_mapping(raw_segment.get("soperator_app"))),
                "soperator_chart": to_plain_data(_mapping(raw_segment.get("soperator_chart"))),
                "jail_rootfs": to_plain_data(_mapping(raw_segment.get("jail_rootfs"))),
                "report_path": str(state.get("segment_report_path", "") or report_path),
                "json_report_path": str(
                    state.get("segment_json_report_path", "") or json_report_path
                ),
                "backup_path": backup_path,
            }
        )
    return history


def legacy_soperator_migration_checkpoint_path(config_path: Path, target_ref: str) -> Path:
    normalized = normalize_component_token(target_ref) or "mk8s"
    return (
        config_path.parent
        / LEGACY_SOPERATOR_MIGRATION_CHECKPOINT_DIR
        / normalized
        / "checkpoint.json"
    )


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
                    self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    return
                except FileExistsError:
                    pass
            raise RuntimeError(
                f"External Soperator upgrade is already running for this target or left a lock: {self.path}. "
                "Remove the lock only after verifying no matching upgrade process is active."
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


_SLURM_FAST_PROBE_TIMEOUT_SECONDS = 20
_TRANSIENT_KUBECTL_CONNECTION_FAILURE_MARKERS = (
    "unable to connect to the server: getting credentials",
    "authentication handshake failed",
    "tls handshake timeout",
)
_TRANSIENT_KUBECTL_READ_ONLY_TIMEOUT_MARKERS = (
    "client.timeout exceeded while awaiting headers",
    "read: operation timed out",
)
_KUBECTL_GLOBAL_FLAGS_WITH_VALUE = {
    "--as",
    "--as-group",
    "--cache-dir",
    "--certificate-authority",
    "--client-certificate",
    "--client-key",
    "--cluster",
    "--context",
    "--kubeconfig",
    "--namespace",
    "--request-timeout",
    "--server",
    "--token",
    "--user",
    "-n",
}
_KUBECTL_READ_ONLY_SUBCOMMANDS = {
    "api-resources",
    "api-versions",
    "auth",
    "cluster-info",
    "describe",
    "explain",
    "get",
    "logs",
    "top",
    "version",
    "wait",
}
_TRANSIENT_KUBECTL_MAX_ATTEMPTS = 3


def _kubectl_subcommand(args: Sequence[str]) -> str:
    skip_next = False
    for raw_arg in args[1:]:
        if skip_next:
            skip_next = False
            continue
        arg = str(raw_arg)
        if not arg:
            continue
        if arg.startswith("-"):
            flag = arg.split("=", 1)[0]
            if flag in _KUBECTL_GLOBAL_FLAGS_WITH_VALUE and "=" not in arg:
                skip_next = True
            continue
        return arg
    return ""


def _is_transient_kubectl_failure(
    args: Sequence[str],
    result: SoperatorMigrationCommandResult,
) -> bool:
    if result.returncode == 0 or not args:
        return False
    if Path(str(args[0])).name != "kubectl":
        return False
    detail = f"{result.stderr}\n{result.stdout}".lower()
    if any(marker in detail for marker in _TRANSIENT_KUBECTL_CONNECTION_FAILURE_MARKERS):
        return True
    if any(marker in detail for marker in _TRANSIENT_KUBECTL_READ_ONLY_TIMEOUT_MARKERS):
        return _kubectl_subcommand(args) in _KUBECTL_READ_ONLY_SUBCOMMANDS
    return False


def _default_command_runner(
    args: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: int = 300,
    check: bool = True,
) -> SoperatorMigrationCommandResult:
    command = list(args)
    result: SoperatorMigrationCommandResult | None = None
    for attempt in range(1, _TRANSIENT_KUBECTL_MAX_ATTEMPTS + 1):
        completed = subprocess.run(
            command,
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
        if (
            attempt >= _TRANSIENT_KUBECTL_MAX_ATTEMPTS
            or not _is_transient_kubectl_failure(args, result)
        ):
            break
        time.sleep(min(float(attempt * 2), 5.0))
    if result is None:  # pragma: no cover - defensive guard
        result = SoperatorMigrationCommandResult(tuple(str(item) for item in args), 1, "", "")
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


def _external_upgrade_report_phase_ids(
    *,
    phase_ids: Sequence[str],
    checkpoint: Mapping[str, Any],
) -> tuple[str, ...]:
    candidates: set[str] = set()
    phase_state = _mapping(checkpoint.get("phase_state"))
    for raw_phase in (
        *phase_ids,
        *(checkpoint.get("planned_phases", []) or []),
        *(checkpoint.get("completed_phases", []) or []),
        *phase_state.keys(),
    ):
        phase_id = str(raw_phase or "").strip()
        if phase_id in _SUPPORTED_EXECUTE_PHASE_IDS:
            candidates.add(phase_id)
    ordered = [phase_id for phase_id in _ORDERED_EXECUTE_PHASE_IDS if phase_id in candidates]
    return tuple(ordered)


def _phase_state(checkpoint: dict[str, Any], phase_id: str) -> dict[str, Any]:
    state = checkpoint.setdefault("phase_state", {})
    if not isinstance(state, dict):
        raise RuntimeError("External Soperator upgrade checkpoint phase_state must be a mapping.")
    phase = state.setdefault(phase_id, {})
    if not isinstance(phase, dict):
        raise RuntimeError(
            f"External Soperator upgrade checkpoint phase_state.{phase_id} must be a mapping."
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
            f"External Soperator upgrade execute requires deploy.targets[].kube_context for "
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
        gpu_stack_preset=validate_node_template_field_value(
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
            f"{field_name} must be 'none' or an explicit Go-style duration such as 30s, 30m, or 1h."
        )
    try:
        parse_go_duration_seconds(raw)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be 'none' or an explicit Go-style duration such as 30s, 30m, or 1h."
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
    resolved_strategy = (
        normalize_component_token(cli_strategy or config_strategy)
        or SOPERATOR_WORKER_ROLLOUT_DEFAULT_STRATEGY
    )
    if resolved_strategy not in SOPERATOR_WORKER_ROLLOUT_STRATEGIES:
        available = ", ".join(sorted(SOPERATOR_WORKER_ROLLOUT_STRATEGIES))
        raise ValueError(
            "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout.strategy "
            f"must be one of: {available}."
        )
    config_service_role_strategy = normalize_component_token(
        str(config.get("service_role_strategy", "") or "")
    )
    if config_service_role_strategy and config_service_role_strategy not in (
        SOPERATOR_WORKER_ROLLOUT_STRATEGIES
    ):
        available = ", ".join(sorted(SOPERATOR_WORKER_ROLLOUT_STRATEGIES))
        raise ValueError(
            "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
            f"service_role_strategy must be one of: {available}."
        )
    resolved_service_role_strategy = (
        config_service_role_strategy
        or (
            cli_strategy
            if strategy is not None and cli_strategy in SOPERATOR_WORKER_ROLLOUT_STRATEGIES
            else SOPERATOR_SERVICE_ROLE_ROLLOUT_DEFAULT_STRATEGY
        )
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

    if worker_wave_groups is not None:
        raw_max_parallel = max_parallel_worker_groups
        max_parallel_field_name = "--max-parallel-worker-groups"
    else:
        raw_max_parallel = (
            max_parallel_worker_groups
            if max_parallel_worker_groups is not None
            else config.get("max_parallel_worker_groups")
        )
        max_parallel_field_name = (
            "--max-parallel-worker-groups"
            if max_parallel_worker_groups is not None
            else (
                "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
                "max_parallel_worker_groups"
            )
        )

    if resolved_strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE:
        zero_surge_wave_fields: list[str] = []
        if resolved_wave_groups is not None:
            zero_surge_wave_fields.append("worker_wave_groups")
        if resolved_wave_percent is not None:
            zero_surge_wave_fields.append("worker_wave_percent")
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
        if (
            resolved_wave_groups is not None
            and raw_max_parallel is not None
            and str(raw_max_parallel).strip()
        ):
            raise ValueError(
                f"{max_parallel_field_name} is only supported with worker_wave_percent; "
                "worker_wave_groups already sets the fixed concurrent worker-group count."
            )
        resolved_parallel = _positive_int_or_none(
            raw_max_parallel,
            field_name=max_parallel_field_name,
        )
    use_config_worker_group_strategy = not cli_strategy or cli_strategy == config_strategy
    worker_group_strategy = (
        _mapping(config.get("worker_group_strategy")) if use_config_worker_group_strategy else {}
    )
    default_max_surge, default_max_unavailable = _default_worker_group_strategy_values(
        resolved_strategy
    )
    service_role_group_strategy = _mapping(config.get("service_role_group_strategy"))
    (
        default_service_role_max_surge,
        default_service_role_max_unavailable,
    ) = _default_worker_group_strategy_values(resolved_service_role_strategy)
    resolved_service_role_max_surge = _non_negative_int_or_default(
        service_role_group_strategy.get("max_surge_count"),
        field_name=(
            "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
            "service_role_group_strategy.max_surge_count"
        ),
        default=default_service_role_max_surge,
    )
    resolved_service_role_max_unavailable = _non_negative_int_or_default(
        service_role_group_strategy.get("max_unavailable_count"),
        field_name=(
            "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
            "service_role_group_strategy.max_unavailable_count"
        ),
        default=default_service_role_max_unavailable,
    )
    if resolved_service_role_max_surge == 0 and resolved_service_role_max_unavailable == 0:
        raise ValueError(
            "service_role_group_strategy must keep at least one of max_surge_count or "
            "max_unavailable_count greater than zero."
        )
    if (
        resolved_service_role_strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE
        and resolved_service_role_max_surge != 0
    ):
        raise ValueError("zero-surge service-role rollout requires max_surge_count to be 0.")
    if (
        resolved_service_role_strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE
        and resolved_service_role_max_surge <= 0
    ):
        raise ValueError(
            "safe-surge service-role rollout requires max_surge_count greater than 0."
        )
    resolved_service_role_drain_timeout = _rollout_drain_timeout_or_default(
        service_role_group_strategy.get("drain_timeout"),
        field_name=(
            "deploy.targets[].soperator_onboarding.node_template_upgrade.rollout."
            "service_role_group_strategy.drain_timeout"
        ),
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
        service_role_strategy=resolved_service_role_strategy,
        worker_wave_groups=resolved_wave_groups,
        worker_wave_percent=resolved_wave_percent,
        max_parallel_worker_groups=resolved_parallel,
        service_role_max_surge_count=resolved_service_role_max_surge,
        service_role_max_unavailable_count=resolved_service_role_max_unavailable,
        service_role_drain_timeout=resolved_service_role_drain_timeout,
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


def _service_role_group_strategy_label(rollout: SoperatorExternalNodeTemplateRollout) -> str:
    return (
        f"max_surge={rollout.service_role_max_surge_count}, "
        f"max_unavailable={rollout.service_role_max_unavailable_count}, "
        f"drain_timeout={rollout.service_role_drain_timeout}"
    )


def _effective_worker_group_strategy_label(
    rollout: SoperatorExternalNodeTemplateRollout,
) -> str:
    if rollout.strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE:
        return _worker_group_strategy_label(rollout) + " (zero-surge)"
    return _worker_group_strategy_label(rollout)


def _effective_service_role_strategy_label(
    rollout: SoperatorExternalNodeTemplateRollout,
) -> str:
    label = _service_role_group_strategy_label(rollout)
    if rollout.service_role_strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_ZERO_SURGE:
        return label + " (lower-continuity zero-surge)"
    return label


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
        f"Node-template rollout strategy: {rollout.strategy}",
        "Worker wave parallelism: "
        + _worker_rollout_budget_label(rollout, worker_group_count=len(worker_groups)),
        "Service-role per-group strategy: " + _effective_service_role_strategy_label(rollout),
        "Node-group per-group strategy: " + _effective_worker_group_strategy_label(rollout),
    ]
    service_safe_surge = (
        rollout.service_role_strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE
    )
    worker_safe_surge = rollout.strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE
    if service_safe_surge or worker_safe_surge:
        surge_counts = []
        if service_safe_surge and service_groups:
            surge_counts.append(
                f"{rollout.service_role_max_surge_count} surge node(s) per active service group"
            )
        if worker_safe_surge and worker_groups:
            surge_counts.append(
                f"{rollout.strategy_max_surge_count} surge node(s) per active worker group"
            )
        surge_text = "; ".join(surge_counts) or "configured surge nodes for active groups"
        lines.append(
            f"Safe-surge spare capacity required: {surge_text}; --execute preflight "
            "verifies quota and capacity before any cluster mutation."
        )
        finite_timeouts = [
            timeout
            for timeout in (rollout.service_role_drain_timeout, rollout.strategy_drain_timeout)
            if timeout != "none"
        ]
        if finite_timeouts:
            lines.append(
                "Node-group drain timeout: finite timeout may let Nebius delete a node after "
                f"{', '.join(sorted(set(finite_timeouts)))} if draining is still blocked."
            )
        if not worker_safe_surge and worker_groups:
            unavailable_count = rollout.strategy_max_unavailable_count
            lines.append(
                "Zero-surge worker capacity: no worker surge quota; active worker group "
                f"capacity may be reduced by {unavailable_count} node"
                f"{'' if unavailable_count == 1 else 's'} during rollout."
            )
        if not service_safe_surge and service_groups:
            unavailable_count = rollout.service_role_max_unavailable_count
            lines.append(
                "Lower-continuity zero-surge service-role capacity: active login or service "
                f"group capacity may be reduced by {unavailable_count} node"
                f"{'' if unavailable_count == 1 else 's'} during rollout."
            )
    else:
        unavailable_count = rollout.strategy_max_unavailable_count
        lines.append(
            "Zero-surge spare capacity required: no surge quota; active service or worker group "
            f"capacity may be reduced by {unavailable_count} node"
            f"{'' if unavailable_count == 1 else 's'} during rollout."
        )
    if service_groups:
        lines.append(
            f"Service-role rollout: serial {rollout.service_role_strategy} for "
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
        raise RuntimeError(
            "External Soperator upgrade execute requires client_info.nebius.project_id."
        )
    return project_id


def _nebius_identity(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    client_info = _mapping(payload.get("client_info"))
    nebius = _mapping(client_info.get("nebius"))
    tenant_id = str(nebius.get("tenant_id", "") or "").strip()
    project_id = str(nebius.get("project_id", "") or "").strip()
    region_id = str(nebius.get("region_id", "") or "").strip()
    if not project_id or not region_id:
        raise RuntimeError(
            "External Soperator upgrade quota preflight requires "
            "client_info.nebius.project_id and region_id."
        )
    return tenant_id, project_id, region_id


def _target_soperator_values(payload: Mapping[str, Any], target_ref: str) -> Mapping[str, Any]:
    row = _target_soperator_row(payload, target_ref)
    return _mapping(row.get("values")) if row else {}


def _target_soperator_row(payload: Mapping[str, Any], target_ref: str) -> Mapping[str, Any]:
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
        return row
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


def _target_placements(
    payload: Mapping[str, Any], target_ref: str
) -> Mapping[str, tuple[str, ...]]:
    row = _target_soperator_row(payload, target_ref)
    raw_mapping = _mapping(row.get("placements"))
    result: dict[str, tuple[str, ...]] = {}
    for raw_placement, raw_groups in raw_mapping.items():
        placement = normalize_component_token(raw_placement)
        if not placement:
            continue
        groups = tuple(
            dict.fromkeys(
                normalize_component_token(item)
                for item in _string_sequence(raw_groups)
                if normalize_component_token(item)
            )
        )
        if groups:
            result[placement] = groups
    for role in (*_SOPERATOR_SERVICE_ROLES, "worker"):
        result.setdefault(role, ())
    return result


def _approved_role_attachment_keys(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    worker_node_groups: Sequence[str],
    source_report: Mapping[str, Any] | None = None,
) -> Mapping[str, tuple[str, ...]]:
    placements = _target_placements(payload, target_ref)
    result: dict[str, list[str]] = {}
    worker_groups = {
        group for group in (normalize_component_token(item) for item in worker_node_groups) if group
    }
    for group in worker_groups:
        keys = result.setdefault(group, [])
        keys.append("jail")
    for role in _SOPERATOR_SERVICE_ROLES:
        for group in placements.get(role, ()):
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
        raise RuntimeError("Soperator source discovery bundle is missing snapshot or report data.")
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
_REDACTED_KUBERNETES_CONTRACT_VALUE = "[redacted]"
_SENSITIVE_KUBERNETES_CONTRACT_KEY_PARTS = frozenset(
    {
        "password",
        "privatekey",
        "secret",
        "token",
    }
)
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
        ("spec", "secrets"),
        ("spec", "slurmNodes", "controller", "openMetrics"),
        ("spec", "slurmNodes", "controller", "sssdDebugLevel"),
        ("spec", "slurmNodes", "login", "sssdDebugLevel"),
    ),
}


def _sensitive_kubernetes_contract_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    compact = normalized.replace("_", "")
    return any(
        part in normalized or part in compact for part in _SENSITIVE_KUBERNETES_CONTRACT_KEY_PARTS
    )


def _strip_volatile_kubernetes_contract(value: Any) -> Any:
    plain = to_plain_data(value)
    if isinstance(plain, Mapping):
        result: dict[str, Any] = {}
        for key, item in plain.items():
            text_key = str(key)
            if text_key in _VOLATILE_KUBERNETES_CONTRACT_KEYS:
                continue
            if _sensitive_kubernetes_contract_key(text_key):
                continue
            stripped = _strip_volatile_kubernetes_contract(item)
            if stripped == _REDACTED_KUBERNETES_CONTRACT_VALUE:
                continue
            result[text_key] = stripped
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


def _phase_allowed_by_onboarding_actions(
    phase_id: str,
    actions: frozenset[str],
) -> bool:
    if phase_id in {"discovery-and-plan", "customer-approval"}:
        return True
    if phase_id == _EXTERNAL_NODE_TEMPLATE_PHASE_ID:
        return ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE in actions
    if phase_id == _TARGET_GPU_STACK_PHASE_ID:
        return ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK in actions
    if phase_id in {"create-aligned-sfs", "online-bulk-data-sync"}:
        return bool(
            actions
            & {
                ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
                ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
            }
        )
    if phase_id == "rolling-compute-migration":
        return bool(
            actions
            & {
                ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
                ONBOARDING_ACTION_UPGRADE_SOPERATOR,
            }
        )
    if phase_id in {
        "final-control-plane-cutover",
        POPULATE_JAIL_REFRESH_PHASE_ID,
        "validation-and-rollback-hold",
        "retire-old-resources",
    }:
        return bool(
            actions
            & {
                ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
                ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
                ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
                ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
                ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
                ONBOARDING_ACTION_UPGRADE_SOPERATOR,
            }
        )
    return True


def _phase_ids_for_actions(
    *,
    report: Mapping[str, Any],
    onboarding: Mapping[str, Any],
    populate_jail_refresh: str = "auto",
) -> tuple[str, ...]:
    actions = _onboarding_actions(onboarding)
    explicit_populate_jail_refresh = normalize_populate_jail_refresh_mode(
        populate_jail_refresh
    ) in {"force", "manual"}
    jail_rootfs = report.get("jail_rootfs")
    jail_rootfs_refresh_required = (
        isinstance(jail_rootfs, Mapping) and jail_rootfs.get("refresh_required") is True
    )
    phase_ids = tuple(
        phase_id
        for phase_id in _phase_ids(report)
        if _phase_allowed_by_onboarding_actions(phase_id, actions)
    )
    if (
        phase_ids
        and ONBOARDING_ACTION_UPGRADE_SOPERATOR not in actions
        and not explicit_populate_jail_refresh
        and not jail_rootfs_refresh_required
    ):
        phase_ids = tuple(
            phase_id for phase_id in phase_ids if phase_id != POPULATE_JAIL_REFRESH_PHASE_ID
        )
    if not phase_ids and (
        ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE in actions
        or ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK in actions
        or ONBOARDING_ACTION_UPGRADE_SOPERATOR in actions
        or jail_rootfs_refresh_required
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
        and (
            ONBOARDING_ACTION_UPGRADE_SOPERATOR in actions
            or jail_rootfs_refresh_required
        )
        and POPULATE_JAIL_REFRESH_PHASE_ID not in phase_ids
    ):
        phases = list(phase_ids)
        insert_at = len(phases)
        for predecessor in ("final-control-plane-cutover", "rolling-compute-migration"):
            if predecessor in phases:
                insert_at = phases.index(predecessor) + 1
                break
        phases.insert(insert_at, POPULATE_JAIL_REFRESH_PHASE_ID)
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
    if (
        phase_ids
        and explicit_populate_jail_refresh
        and POPULATE_JAIL_REFRESH_PHASE_ID not in phase_ids
    ):
        phases = list(phase_ids)
        insert_at = len(phases)
        for predecessor in (
            "final-control-plane-cutover",
            "rolling-compute-migration",
            _TARGET_GPU_STACK_PHASE_ID,
            _EXTERNAL_NODE_TEMPLATE_PHASE_ID,
            "online-bulk-data-sync",
            "create-aligned-sfs",
            "customer-approval",
        ):
            if predecessor in phases:
                insert_at = phases.index(predecessor) + 1
                break
        phases.insert(insert_at, POPULATE_JAIL_REFRESH_PHASE_ID)
        phase_ids = tuple(phases)
    if phase_ids and actions:
        phases = list(phase_ids)
        for phase_id in _POST_UPGRADE_CHECK_PHASE_IDS:
            if phase_id not in phases:
                phases.append(phase_id)
        phase_ids = tuple(phases)
    return phase_ids


def _target_soperator_helm_release_required(onboarding: Mapping[str, Any]) -> bool:
    return ONBOARDING_ACTION_UPGRADE_SOPERATOR in _onboarding_actions(onboarding)


def _phase_ids_with_jail_persistent_mount_prerequisites(
    *,
    phase_ids: Sequence[str],
    payload: Mapping[str, Any],
    target_ref: str,
) -> tuple[str, ...]:
    status = jail_persistent_mount_status(_target_soperator_values(payload, target_ref))
    if not status.verified:
        return tuple(phase_ids)
    phases = [str(phase_id) for phase_id in phase_ids if str(phase_id)]
    if _legacy_persistent_mount_migration_required(_target_soperator_values(payload, target_ref)):
        insert_at = len(phases)
        for predecessor in ("final-control-plane-cutover", "rolling-compute-migration"):
            if predecessor in phases:
                insert_at = phases.index(predecessor) + 1
                break
        phases.insert(insert_at, POPULATE_JAIL_REFRESH_PHASE_ID)
    return tuple(dict.fromkeys(phases))


def _path_contains_posix(parent: str, child: str) -> bool:
    parent = "/" + str(parent or "").strip().strip("/")
    child = "/" + str(child or "").strip().strip("/")
    if parent == "/":
        return True
    return child == parent or child.startswith(f"{parent}/")


def _paths_overlap_posix(first: str, second: str) -> bool:
    return _path_contains_posix(first, second) or _path_contains_posix(second, first)


def _store_relative_path(path: str, *, store_path: str = JAIL_LEGACY_ROOT_PATH) -> str:
    normalized = "/" + str(path or "").strip().strip("/")
    store = "/" + str(store_path or "").strip().strip("/")
    if normalized == store:
        raise ValueError("persistent mount migration path must not be the jail store root.")
    if not normalized.startswith(f"{store}/"):
        raise ValueError(
            f"persistent mount migration path must be below {store}; got {normalized}."
        )
    return "/" + normalized.removeprefix(f"{store}/").strip("/")


def _legacy_persistent_mount_migration_entries(
    values: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    if not jail_rootfs_uses_legacy_active_source(values):
        return ()
    entries: list[dict[str, Any]] = []
    for mount in _sequence_of_mappings(values.get(JAIL_PERSISTENT_MOUNTS_VALUES_KEY)):
        mount_path = "/" + str(mount.get("mountPath") or "").strip().strip("/")
        local_path = "/" + str(mount.get("localPath") or "").strip().strip("/")
        if mount_path in {"", "/"} or local_path in {"", "/"}:
            continue
        for field, value in (
            ("mountPath", mount_path),
            ("localPath", local_path),
        ):
            if any(character in value for character in ('"', "\n", "\r")):
                raise SoperatorMigrationPhasePending(
                    "persistent mount migration paths must not contain quotes or newlines: "
                    f"{field}={value!r}."
                )
        source_path = f"{JAIL_LEGACY_ROOT_PATH}{mount_path}"
        if _paths_overlap_posix(source_path, local_path):
            raise SoperatorMigrationPhasePending(
                "persistent mount migration source and target overlap: "
                f"{source_path} -> {local_path}. Use a shared target such as "
                f"{JAIL_LEGACY_ROOT_PATH}/shared{mount_path}."
            )
        relative_target = _store_relative_path(local_path)
        marker_name = normalize_component_token(mount_path.strip("/")) or "root"
        entries.append(
            {
                "name": f"jail-persistent-migration-{marker_name}",
                "mount_path": mount_path,
                "source_path": source_path,
                "source_store_path": _store_relative_path(source_path),
                "target_local_path": local_path,
                "target_store_path": relative_target,
                "marker_path": (
                    f"{JAIL_EXTERNAL_SYSTEM_PATH}/persistent-migrations/{marker_name}.json"
                ),
                "marker_store_path": f"/.cxcli/persistent-migrations/{marker_name}.json",
                "bytes_planned": None,
                "pvc_name": str(mount.get("pvcName") or ""),
                "status": "planned",
            }
        )
    return tuple(entries)


def _legacy_persistent_mount_migration_required(values: Mapping[str, Any]) -> bool:
    return bool(_legacy_persistent_mount_migration_entries(values))


def _persistent_mount_decisions_with_migration_entries(
    decisions: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    entries_by_mount = {
        str(entry.get("mount_path") or ""): entry
        for entry in entries
        if str(entry.get("mount_path") or "")
    }
    updated: list[dict[str, Any]] = []
    for decision in decisions:
        next_decision = dict(to_plain_data(decision))
        entry = entries_by_mount.get(str(next_decision.get("mount_path") or ""))
        if not entry:
            updated.append(next_decision)
            continue
        source_status = str(entry.get("source_status") or "").strip()
        if source_status:
            next_decision["source_status"] = source_status
            if str(next_decision.get("status") or "") == "pending-probe":
                next_decision["status"] = source_status
            next_decision["copy_required"] = source_status != "absent"
        copy_status = str(entry.get("copy_status") or "").strip()
        if copy_status:
            next_decision["copy_status"] = copy_status
        marker_status = str(entry.get("marker_status") or "").strip()
        if marker_status:
            next_decision["marker_status"] = marker_status
        next_decision["marker_present"] = bool(entry.get("marker_present"))
        updated.append(next_decision)
    return tuple(updated)


def _sync_persistent_mount_decisions_from_migration_entries(
    checkpoint: dict[str, Any],
    entries: Sequence[Mapping[str, Any]],
) -> None:
    persistent_state = dict(_mapping(checkpoint.get("persistent_jail_mounts")))
    decisions = _sequence_of_mappings(persistent_state.get("decisions"))
    if not decisions:
        synthesized: list[dict[str, Any]] = []
        for entry in entries:
            mount_path = str(entry.get("mount_path") or "").strip()
            if not mount_path:
                continue
            source_status = str(entry.get("source_status") or "unknown").strip() or "unknown"
            next_decision = {
                "mount_path": mount_path,
                "local_path": str(entry.get("target_local_path") or "").strip(),
                "status": source_status,
                "source_status": source_status,
                "origin": "checkpoint",
                "source_path": str(entry.get("source_path") or "").strip(),
                "target_local_path": str(entry.get("target_local_path") or "").strip(),
                "copy_required": source_status != "absent",
            }
            copy_status = str(entry.get("copy_status") or "").strip()
            if copy_status:
                next_decision["copy_status"] = copy_status
            synthesized.append(next_decision)
        if not synthesized:
            return
        persistent_state["decisions"] = synthesized
        checkpoint["persistent_jail_mounts"] = persistent_state
        return
    persistent_state["decisions"] = [
        dict(item)
        for item in _persistent_mount_decisions_with_migration_entries(decisions, entries)
    ]
    checkpoint["persistent_jail_mounts"] = persistent_state


def _payload_with_target_soperator_values(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    patched = dict(copy.deepcopy(to_plain_data(payload)))
    apps = patched.get("apps")
    charts = _mapping(apps).get("charts") if isinstance(apps, Mapping) else None
    if not isinstance(charts, list):
        raise RuntimeError("Soperator target values could not be patched: apps.charts is missing.")
    normalized_target = normalize_component_token(target_ref)
    for row in charts:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "") or "").strip() != "soperator":
            continue
        if normalize_component_token(row.get("instance_id")) != normalized_target:
            continue
        row["values"] = copy.deepcopy(dict(to_plain_data(values)))
        return patched
    raise RuntimeError(f"Soperator target '{target_ref}' values could not be found for patching.")


def _prepare_jail_persistent_mount_payload(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    jail_persistent_mounts: Sequence[str] = (),
    populate_jail_refresh: str,
    auto_persistent_mounts: bool = True,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    values = _target_soperator_values(payload, target_ref)
    explicit_mounts = parse_jail_persistent_mount_specs(jail_persistent_mounts)
    if not auto_persistent_mounts and not explicit_mounts:
        state: dict[str, Any] = {
            "status": "not_required",
            "reason": "jail rootfs refresh is not planned for this segment",
            "mounts": [],
            "copy_required": False,
            "migration": {
                "status": "not_required",
                "source_rootfs": JAIL_LEGACY_ROOT_PATH,
                "entries": [],
            },
            "rootfs_path": "/mnt/jail/.cxcli/rootfs",
            "legacy_active_rootfs": False,
        }
        return payload, state
    patched_values = apply_jail_persistent_mount_values(
        values,
        target_ref=target_ref,
        persistent_mounts=explicit_mounts,
        layout="external",
    )
    status = jail_persistent_mount_status(patched_values)
    migration_entries = _legacy_persistent_mount_migration_entries(patched_values)
    decisions = jail_persistent_mount_decisions(
        original_values=values,
        patched_values=patched_values,
        explicit_mounts=explicit_mounts,
    )
    state: dict[str, Any] = {
        "status": status.status,
        "reason": status.reason,
        "mounts": [mount.as_payload() for mount in status.mounts],
        "decisions": [dict(decision) for decision in decisions],
        "auto_preserve_paths": list(JAIL_EXTERNAL_AUTO_PERSISTENT_MOUNT_PATHS),
        "copy_required": bool(migration_entries),
        "migration": {
            "status": "planned" if migration_entries else "not_required",
            "source_rootfs": JAIL_LEGACY_ROOT_PATH,
            "entries": [dict(entry) for entry in migration_entries],
        },
        "rootfs_path": "/mnt/jail/.cxcli/rootfs",
        "legacy_active_rootfs": jail_rootfs_uses_legacy_active_source(patched_values),
    }
    if not status.verified and normalize_populate_jail_refresh_mode(populate_jail_refresh) != "manual":
        raise SoperatorMigrationPhasePending(
            "jail rootfs refresh is blocked until /home is configured as a persistent "
            "jail mount or provided by an existing customer-owned jail submount. Use "
            "--populate-jail-refresh manual to stop before rootfs slot refresh."
        )
    return (
        _payload_with_target_soperator_values(
            payload=payload,
            target_ref=target_ref,
            values=patched_values,
        ),
        state,
    )


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
        if normalized in _SOPERATOR_SERVICE_ROLES:
            return normalized
    return ""


def _zero_surge_service_quiesce_required(
    role: str,
    source_group: Mapping[str, Any],
) -> bool:
    if not role:
        return False
    if role in {"accounting", "login", "system"}:
        return True
    return _positive_int(source_group.get("node_count"), fallback=1) <= 1


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
            "Soperator compute remediation could not infer source worker node groups from "
            "the accepted onboarding inventory. Rerun `nebius-cxcli ext-soperator onboard` "
            "against the Nebius MK8s target so cxcli can read live node-group names and "
            "slurm.nebius.ai/nodeset worker labels."
        )
    available = {normalize_component_token(name) for name in inventory}
    missing = tuple(group for group in normalized_groups if group not in available)
    if missing:
        raise RuntimeError(
            "Soperator compute remediation worker node group(s) were not found in source "
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


def _non_negative_int(value: Any, *, fallback: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


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


def _soperator_storage_keys_for_target(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
) -> tuple[str, ...]:
    _ = (payload, target_ref)
    return tuple(key for key in _SOPERATOR_STORAGE_KEYS if key != "jail")


def _aligned_filesystem_specs(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
) -> tuple[SoperatorAlignedFilesystemSpec, ...]:
    values = _target_soperator_values(payload, target_ref)
    configured = _mapping(_mapping(values.get("sfs")).get("filesystems"))
    specs: list[SoperatorAlignedFilesystemSpec] = []
    for key in _soperator_storage_keys_for_target(payload=payload, target_ref=target_ref):
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
    nebius_api: SoperatorMigrationNebiusApi,
    project_id: str,
    name: str,
) -> Mapping[str, Any]:
    return nebius_api.get_filesystem_by_name(project_id=project_id, name=name)


def _create_filesystem(
    *,
    nebius_api: SoperatorMigrationNebiusApi,
    project_id: str,
    spec: SoperatorAlignedFilesystemSpec,
) -> Mapping[str, Any]:
    return nebius_api.create_filesystem(project_id=project_id, spec=spec, timeout_seconds=1800)


def _source_group_node_group_id(source_group: Mapping[str, Any]) -> str:
    labels = _source_group_labels(source_group)
    for key in _SOURCE_NODE_GROUP_ID_LABEL_KEYS:
        value = str(labels.get(key, "") or "").strip()
        if value:
            return value
    return str(source_group.get("node_group_id", "") or source_group.get("id", "") or "").strip()


def _node_group_payload_by_id(
    *,
    nebius_api: SoperatorMigrationNebiusApi,
    node_group_id: str,
) -> Mapping[str, Any]:
    return nebius_api.get_node_group(node_group_id)


def _cluster_payload_by_id(
    *,
    nebius_api: SoperatorMigrationNebiusApi,
    cluster_id: str,
) -> Mapping[str, Any]:
    return nebius_api.get_cluster(cluster_id)


def _cluster_control_plane_version(cluster: Mapping[str, Any]) -> str:
    spec = _mapping(cluster.get("spec"))
    control_plane = _mapping(spec.get("control_plane"))
    if not control_plane:
        control_plane = _mapping(spec.get("controlPlane"))
    return str(
        control_plane.get("version") or spec.get("version") or cluster.get("version") or ""
    ).strip()


def _update_cluster_control_plane(
    *,
    nebius_api: SoperatorMigrationNebiusApi,
    cluster_id: str,
    control_plane_version: str,
) -> Mapping[str, Any]:
    return nebius_api.update_cluster_control_plane(
        cluster_id=cluster_id,
        control_plane_version=control_plane_version,
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
        raise RuntimeError("External Soperator upgrade requires a zero-surge strategy.")
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


def _soperator_service_role_strategy_cli_args(
    rollout: SoperatorExternalNodeTemplateRollout,
) -> list[str]:
    return [
        "--strategy-max-surge-count",
        str(rollout.service_role_max_surge_count),
        "--strategy-max-unavailable-count",
        str(rollout.service_role_max_unavailable_count),
        "--strategy-drain-timeout",
        _rollout_drain_timeout_cli_value(rollout.service_role_drain_timeout),
    ]


def _external_node_template_strategy_cli_args(
    rollout: SoperatorExternalNodeTemplateRollout,
    *,
    worker_group: bool,
) -> tuple[list[str], str]:
    if worker_group:
        return _soperator_worker_strategy_cli_args(rollout), rollout.strategy
    return _soperator_service_role_strategy_cli_args(rollout), rollout.service_role_strategy


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
    if (
        "--template-os" in command
        and _node_group_template_os(node_group) != command[command.index("--template-os") + 1]
    ):
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
        "Rerun the same `nebius-cxcli ext-soperator upgrade ... --execute --approve` "
        "command; cxcli will re-read the live node group and resume without starting "
        "a duplicate update."
    )


def _reconcile_node_group_update_timeout(
    nebius_api: SoperatorMigrationNebiusApi,
    *,
    node_group_id: str,
    update_args: Sequence[str],
    strategy_args: Sequence[str],
    clear_template_gpu_settings: bool,
    action: str,
    timeout: subprocess.TimeoutExpired,
) -> Mapping[str, Any]:
    live_node_group = _node_group_payload_by_id(
        nebius_api=nebius_api,
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
            "`nebius-cxcli ext-soperator upgrade ... --execute --approve` command; "
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
    nebius_api: SoperatorMigrationNebiusApi,
    node_group_id: str,
    update_args: Sequence[str],
    strategy_args: Sequence[str],
    original_node_group: Mapping[str, Any] | None = None,
    clear_template_gpu_settings: bool = False,
    timeout: str = "45m",
    timeout_seconds: int = 2700,
) -> Mapping[str, Any]:
    original_node_group = original_node_group or _node_group_payload_by_id(
        nebius_api=nebius_api,
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
        try:
            result = nebius_api.update_node_group(
                node_group_id=node_group_id,
                original_node_group=original_node_group,
                update_args=update_args,
                strategy_args=strategy_args,
                clear_template_gpu_settings=clear_template_gpu_settings,
                timeout_seconds=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            result = _reconcile_node_group_update_timeout(
                nebius_api,
                node_group_id=node_group_id,
                update_args=update_args,
                strategy_args=strategy_args,
                clear_template_gpu_settings=clear_template_gpu_settings,
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
                    nebius_api.update_node_group(
                        node_group_id=node_group_id,
                        original_node_group=nebius_api.get_node_group(node_group_id),
                        update_args=(),
                        strategy_args=original_strategy_args,
                        clear_template_gpu_settings=False,
                        timeout_seconds=timeout_seconds,
                    )
                except subprocess.TimeoutExpired as exc:
                    _reconcile_node_group_update_timeout(
                        nebius_api,
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
    nebius_api: SoperatorMigrationNebiusApi,
    node_group_id: str,
    update_args: Sequence[str],
    original_node_group: Mapping[str, Any] | None = None,
    clear_template_gpu_settings: bool = False,
    timeout_seconds: int = 2700,
) -> Mapping[str, Any]:
    timeout = _node_group_rollout_timeout_text(timeout_seconds)
    return _update_node_group_with_temporary_strategy(
        nebius_api=nebius_api,
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
    nebius_api: SoperatorMigrationNebiusApi,
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
            nebius_api=nebius_api,
            node_group_id=node_group_id,
        )
        existing = _node_group_template_filesystems(node_group)
        merged = _merge_filesystem_attachments(existing, desired)
        updated = len(merged) != len(existing)
        if updated:
            _update_node_group_with_zero_surge_strategy(
                nebius_api=nebius_api,
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


def _copy_job_paths_for_storage_key(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    key: str,
) -> tuple[str, str]:
    _ = (payload, target_ref, key)
    return "/", "/"


def _validate_copy_job_path(path: str, *, role: str) -> str:
    normalized = "/" + str(path or "").strip().strip("/")
    if normalized == "//":
        normalized = "/"
    if normalized == "/":
        return normalized
    if any(part in {"", ".", ".."} for part in normalized.split("/")[1:]):
        raise ValueError(f"copy job {role} path contains an unsafe path segment: {path!r}")
    return normalized


def _copy_job_shell_command(
    *,
    source_path: str,
    target_path: str,
) -> str:
    source = _validate_copy_job_path(source_path, role="source")
    target = _validate_copy_job_path(target_path, role="target")
    source_dir = "/old" if source == "/" else f"/old{source}"
    target_dir = "/new" if target == "/" else f"/new{target}"
    return (
        f"mkdir -p {shlex.quote(target_dir)} && "
        f"cd {shlex.quote(source_dir)} && "
        "tar --xattrs --acls --numeric-owner -cpf - . "
        f"| tar --xattrs --acls --numeric-owner -xpf - -C {shlex.quote(target_dir)}"
    )


def _copy_job_name_for_storage_key(key: str) -> str:
    normalized = normalize_component_token(key) or key.replace("_", "-")
    return f"cxcli-soperator-sync-{normalized}"


def _copy_job_manifest(
    *,
    key: str,
    source_pvc: str,
    target_pvc: str,
    source_path: str = "/",
    target_path: str = "/",
) -> dict[str, Any]:
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": _copy_job_name_for_storage_key(key),
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
                                _copy_job_shell_command(
                                    source_path=source_path,
                                    target_path=target_path,
                                ),
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


def _store_mount_path(relative_path: str) -> str:
    normalized = _validate_copy_job_path(relative_path, role="persistent migration")
    return "/store" if normalized == "/" else f"/store{normalized}"


def _persistent_mount_migration_job_name(target_ref: str) -> str:
    base = normalize_component_token(f"{target_ref}-jail-persistent-migration")
    return (base or "jail-persistent-migration")[:63].rstrip("-")


def _persistent_mount_source_probe_job_name(target_ref: str) -> str:
    base = normalize_component_token(f"{target_ref}-jail-persistent-source-probe")
    return (base or "jail-persistent-source-probe")[:63].rstrip("-")


def _persistent_mount_source_probe_shell_command(
    entries: Sequence[Mapping[str, Any]],
) -> str:
    blocks: list[str] = [
        "set -eu",
        "probe_entry() {",
        "  source_path=\"$1\"",
        "  target_path=\"$2\"",
        "  marker_path=\"$3\"",
        "  mount_path=\"$4\"",
        "  source_status=absent",
        "  marker_present=false",
        "  marker_status=none",
        "  if [ -e \"$source_path\" ]; then",
        "    source_status=present",
        "  fi",
        "  if [ -f \"$marker_path\" ]; then",
        "    marker_present=true",
        "    if grep -Fq '\"status\":\"copied\"' \"$marker_path\"; then",
        "      marker_status=copied",
        "    elif grep -Fq '\"status\":\"source_missing\"' \"$marker_path\"; then",
        "      marker_status=source_missing",
        "    else",
        "      marker_status=unknown",
        "    fi",
        "  fi",
        "  printf '{\"mount_path\":\"%s\",\"source_path\":\"%s\",\"target_path\":\"%s\",\"marker_path\":\"%s\",\"source_status\":\"%s\",\"marker_status\":\"%s\",\"marker_present\":%s}\\n' "
        "\"$mount_path\" \"$source_path\" \"$target_path\" \"$marker_path\" \"$source_status\" \"$marker_status\" \"$marker_present\"",
        "}",
    ]
    for entry in entries:
        blocks.append(
            "probe_entry "
            f"{shlex.quote(_store_mount_path(str(entry.get('source_store_path') or '')))} "
            f"{shlex.quote(_store_mount_path(str(entry.get('target_store_path') or '')))} "
            f"{shlex.quote(_store_mount_path(str(entry.get('marker_store_path') or '')))} "
            f"{shlex.quote(str(entry.get('mount_path') or ''))}"
        )
    return "\n".join(blocks)


def _persistent_mount_source_probe_job_manifest(
    *,
    target_ref: str,
    image: str,
    jail_pvc: str,
    entries: Sequence[Mapping[str, Any]],
    scheduling: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    pod_spec = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "containers": [
            {
                "name": "persistent-mount-source-probe",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "command": [
                    "/bin/sh",
                    "-ceu",
                    _persistent_mount_source_probe_shell_command(entries),
                ],
                "volumeMounts": [
                    {"name": "jail-store", "mountPath": "/store", "readOnly": True}
                ],
            }
        ],
        "volumes": [
            {
                "name": "jail-store",
                "persistentVolumeClaim": {"claimName": jail_pvc},
            }
        ],
    }
    pod_spec.update(active_passive_pod_scheduling_fields(scheduling))
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "namespace": _SOPERATOR_NAMESPACE,
            "name": _persistent_mount_source_probe_job_name(target_ref),
            "labels": {
                "app.kubernetes.io/managed-by": "nebius-cxcli",
                "app.kubernetes.io/component": "jail-persistent-source-probe",
                "nebius-cxcli.io/soperator-migration": "true",
            },
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/component": "jail-persistent-source-probe",
                    }
                },
                "spec": pod_spec,
            },
        },
    }


def _parse_persistent_mount_source_probe_output(output: str) -> tuple[dict[str, Any], ...]:
    entries: list[dict[str, Any]] = []
    for line in str(output or "").splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, Mapping):
            continue
        mount_path = str(item.get("mount_path") or "").strip()
        if not mount_path:
            continue
        source_status = str(item.get("source_status") or "").strip()
        if source_status not in {"present", "absent"}:
            source_status = "unknown"
        entries.append(
            {
                "mount_path": mount_path,
                "source_path": str(item.get("source_path") or "").strip(),
                "target_path": str(item.get("target_path") or "").strip(),
                "marker_path": str(item.get("marker_path") or "").strip(),
                "source_status": source_status,
                "marker_status": str(item.get("marker_status") or "none").strip() or "none",
                "marker_present": bool(item.get("marker_present")),
            }
        )
    return tuple(entries)


def _persistent_mount_migration_copy_status_by_log(output: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in str(output or "").splitlines():
        text = line.strip()
        if text.startswith("persistent mount migration copied: "):
            statuses[text.rsplit(": ", 1)[-1]] = "copied"
        elif text.startswith("persistent mount migration source missing: "):
            statuses[text.rsplit(": ", 1)[-1]] = "source_missing"
        elif text.startswith("persistent mount migration skipped: "):
            mount = text.removeprefix("persistent mount migration skipped: ")
            statuses[mount.split(" ", 1)[0]] = "skipped"
    return statuses


def _persistent_mount_migration_shell_command(
    entries: Sequence[Mapping[str, Any]],
) -> str:
    blocks: list[str] = [
        "set -eu",
        "copy_entry() {",
        "  source_path=\"$1\"",
        "  target_path=\"$2\"",
        "  marker_path=\"$3\"",
        "  mount_path=\"$4\"",
        "  marker_matches() {",
        "    marker_status=\"\"",
        "    if grep -Fq '\"status\":\"copied\"' \"$marker_path\"; then",
        "      marker_status=\"copied\"",
        "    elif grep -Fq '\"status\":\"source_missing\"' \"$marker_path\"; then",
        "      marker_status=\"source_missing\"",
        "    else",
        "      return 1",
        "    fi",
        "    grep -Fq \"\\\"mount_path\\\":\\\"$mount_path\\\"\" \"$marker_path\" || return 1",
        "    grep -Fq \"\\\"source_path\\\":\\\"$source_path\\\"\" \"$marker_path\" || return 1",
        "    grep -Fq \"\\\"target_path\\\":\\\"$target_path\\\"\" \"$marker_path\" || return 1",
        "  }",
        "  write_marker() {",
        "    status=\"$1\"",
        "    printf '{\"status\":\"%s\",\"mount_path\":\"%s\",\"source_path\":\"%s\",\"target_path\":\"%s\"}\\n' "
        "\"$status\" \"$mount_path\" \"$source_path\" \"$target_path\" > \"$marker_path\"",
        "  }",
        "  if [ -f \"$marker_path\" ]; then",
        "    if ! marker_matches; then",
        "      printf 'persistent mount migration marker does not match this copy: %s\\n' \"$marker_path\" >&2",
        "      exit 16",
        "    fi",
        "    if [ \"$marker_status\" = \"source_missing\" ] && [ -e \"$source_path\" ]; then",
        "      printf 'persistent mount migration source_missing marker is stale; source now exists: %s\\n' \"$source_path\" >&2",
        "      exit 18",
        "    fi",
        "    printf 'persistent mount migration skipped: %s marker exists\\n' \"$mount_path\"",
        "    return 0",
        "  fi",
        "  if [ -L \"$source_path\" ]; then",
        "    printf 'persistent mount migration source is a symlink: %s\\n' \"$source_path\" >&2",
        "    exit 12",
        "  fi",
        "  if [ -L \"$target_path\" ]; then",
        "    printf 'persistent mount migration target is a symlink: %s\\n' \"$target_path\" >&2",
        "    exit 13",
        "  fi",
        "  if [ -e \"$target_path\" ] && [ ! -d \"$target_path\" ]; then",
        "    printf 'persistent mount migration target is not a directory: %s\\n' \"$target_path\" >&2",
        "    exit 17",
        "  fi",
        "  if [ -d \"$target_path\" ] && [ -n \"$(find \"$target_path\" -mindepth 1 -maxdepth 1 -print -quit)\" ]; then",
        "    printf 'persistent mount migration target is non-empty without marker: %s\\n' \"$target_path\" >&2",
        "    exit 15",
        "  fi",
        "  marker_dir=$(dirname \"$marker_path\")",
        "  if [ ! -e \"$source_path\" ]; then",
        "    mkdir -p \"$target_path\" \"$marker_dir\"",
        "    write_marker source_missing",
        "    printf 'persistent mount migration source missing: %s\\n' \"$mount_path\"",
        "    return 0",
        "  fi",
        "  if [ ! -d \"$source_path\" ]; then",
        "    printf 'persistent mount migration source is not a directory: %s\\n' \"$source_path\" >&2",
        "    exit 14",
        "  fi",
        "  mkdir -p \"$target_path\"",
        "  if [ -n \"$(find \"$target_path\" -mindepth 1 -maxdepth 1 -print -quit)\" ]; then",
        "    printf 'persistent mount migration target is non-empty without marker: %s\\n' \"$target_path\" >&2",
        "    exit 15",
        "  fi",
        "  mkdir -p \"$marker_dir\"",
        "  (cd \"$source_path\" && tar --xattrs --acls --numeric-owner -cpf - .) | "
        "tar --xattrs --acls --numeric-owner -xpf - -C \"$target_path\"",
        "  write_marker copied",
        "  printf 'persistent mount migration copied: %s\\n' \"$mount_path\"",
        "}",
    ]
    for entry in entries:
        blocks.append(
            "copy_entry "
            f"{shlex.quote(_store_mount_path(str(entry.get('source_store_path') or '')))} "
            f"{shlex.quote(_store_mount_path(str(entry.get('target_store_path') or '')))} "
            f"{shlex.quote(_store_mount_path(str(entry.get('marker_store_path') or '')))} "
            f"{shlex.quote(str(entry.get('mount_path') or ''))}"
        )
    return "\n".join(blocks)


def _persistent_mount_migration_job_manifest(
    *,
    target_ref: str,
    image: str,
    jail_pvc: str,
    entries: Sequence[Mapping[str, Any]],
    scheduling: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    pod_spec = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "containers": [
            {
                "name": "persistent-mount-migration",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "command": [
                    "/bin/sh",
                    "-ceu",
                    _persistent_mount_migration_shell_command(entries),
                ],
                "volumeMounts": [{"name": "jail-store", "mountPath": "/store"}],
            }
        ],
        "volumes": [
            {
                "name": "jail-store",
                "persistentVolumeClaim": {"claimName": jail_pvc},
            }
        ],
    }
    pod_spec.update(active_passive_pod_scheduling_fields(scheduling))
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "namespace": _SOPERATOR_NAMESPACE,
            "name": _persistent_mount_migration_job_name(target_ref),
            "labels": {
                "app.kubernetes.io/managed-by": "nebius-cxcli",
                "app.kubernetes.io/component": "jail-persistent-migration",
                "nebius-cxcli.io/soperator-migration": "true",
            },
        },
        "spec": {
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/component": "jail-persistent-migration",
                    }
                },
                "spec": pod_spec,
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


def _job_condition_true(job: Mapping[str, Any], condition_type: str) -> bool:
    status = _mapping(job.get("status"))
    for condition in _sequence_of_mappings(status.get("conditions")):
        if (
            str(condition.get("type", "") or "") == condition_type
            and str(condition.get("status", "") or "").lower() == "true"
        ):
            return True
    return False


def _delete_failed_job_before_reapply(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    name: str,
) -> None:
    exists, job = _kubectl_get_namespace_resource(
        command_runner=command_runner,
        kube_context=kube_context,
        resource=f"job/{name}",
    )
    if not exists or not _job_condition_true(job, "Failed"):
        return
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "delete",
            "job",
            name,
            "--ignore-not-found",
        ],
        timeout_seconds=300,
    )


def _delete_job_before_reapply(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    name: str,
    wait: bool = False,
) -> None:
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "delete",
            "job",
            name,
            "--ignore-not-found",
            f"--wait={'true' if wait else 'false'}",
        ],
        timeout_seconds=300,
        check=False,
    )


def _wait_for_job_complete_or_failed(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    name: str,
    timeout_seconds: int,
    job_label: str = "Soperator data sync Job",
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        exists, job = _kubectl_get_namespace_resource(
            command_runner=command_runner,
            kube_context=kube_context,
            resource=f"job/{name}",
        )
        if not exists:
            raise RuntimeError(f"{job_label} disappeared before completion: {name}")
        if exists and _job_condition_true(job, "Complete"):
            return
        if exists and _job_condition_true(job, "Failed"):
            raise RuntimeError(f"{job_label} failed: {name}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"{job_label} did not complete before timeout: {name}")
        time.sleep(min(10.0, remaining))


def _probe_legacy_persistent_mount_sources(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
    image: str,
    jail_pvc: str,
    entries: Sequence[Mapping[str, Any]],
    scheduling: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], ...]:
    if not entries:
        return ()
    manifest = _persistent_mount_source_probe_job_manifest(
        target_ref=target_ref,
        image=image,
        jail_pvc=jail_pvc,
        entries=entries,
        scheduling=scheduling,
    )
    job_name = str(_mapping(manifest.get("metadata")).get("name") or "")
    if job_name:
        _delete_job_before_reapply(
            command_runner=command_runner,
            kube_context=kube_context,
            name=job_name,
            wait=True,
        )
    _kubectl_apply_objects(
        command_runner=command_runner,
        kube_context=kube_context,
        objects=(manifest,),
        timeout_seconds=300,
    )
    _wait_for_job_complete_or_failed(
        command_runner=command_runner,
        kube_context=kube_context,
        name=job_name,
        timeout_seconds=900,
        job_label="Soperator persistent mount source probe Job",
    )
    result = command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "logs",
            f"job/{job_name}",
        ],
        timeout_seconds=120,
        check=False,
    )
    parsed = _parse_persistent_mount_source_probe_output(result.stdout)
    if parsed:
        return parsed
    return tuple(
        {
            "mount_path": str(entry.get("mount_path") or ""),
            "source_path": str(entry.get("source_path") or ""),
            "target_path": str(entry.get("target_local_path") or ""),
            "marker_path": str(entry.get("marker_path") or ""),
            "source_status": "unknown",
            "marker_status": "unknown",
            "marker_present": False,
        }
        for entry in entries
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

    placements = _target_placements(payload, target_ref)
    cpu_candidates: list[str] = []
    for role in _SOPERATOR_SERVICE_ROLES:
        for group in placements.get(role, ()):
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
            "onboarding with explicit compute placements."
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
    nebius_api: SoperatorMigrationNebiusApi,
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
            nebius_api=nebius_api,
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
    nebius_api: SoperatorMigrationNebiusApi,
    cluster_id: str,
) -> tuple[Mapping[str, Any], ...]:
    return nebius_api.list_node_groups(cluster_id)


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
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    cluster_id: str,
) -> Mapping[str, Mapping[str, Any]]:
    node_groups = _list_node_groups(nebius_api=nebius_api, cluster_id=cluster_id)
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
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
) -> Mapping[str, Any]:
    cluster_id = _target_cluster_id(payload, target_ref)
    if not cluster_id:
        return source_report
    inventory = _live_nebius_node_group_inventory(
        nebius_api=nebius_api,
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
        labels["slurm.nebius.ai/nodeset"] = role
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
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, "rolling-compute-migration")
    target_groups = phase.setdefault("target_node_groups", {})
    if not isinstance(target_groups, dict):
        raise RuntimeError(
            "External Soperator upgrade checkpoint rolling-compute-migration.target_node_groups must be a mapping."
        )
    old_groups = phase.setdefault("old_node_groups", {})
    if not isinstance(old_groups, dict):
        raise RuntimeError(
            "External Soperator upgrade checkpoint rolling-compute-migration.old_node_groups must be a mapping."
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
            nebius_api=nebius_api,
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
    live_node_groups = _list_node_groups(nebius_api=nebius_api, cluster_id=cluster_id)
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
                nebius_api=nebius_api,
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
        created = nebius_api.create_node_group(payload=create_payload, timeout_seconds=3900)
        node_group_id = _node_group_id(created)
        if not node_group_id:
            node_group_id = _node_group_id(
                _find_node_group_by_name(
                    _list_node_groups(nebius_api=nebius_api, cluster_id=cluster_id),
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
    return f"External Soperator upgrade target {target_ref}"


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
    nebius_api: SoperatorMigrationNebiusApi,
) -> tuple[list[QuotaRequirement], list[QuotaCoverageGap], list[str]]:
    project_id = _nebius_project_id(payload)
    _tenant_id, _project_id, region = _nebius_identity(payload)
    requirements: list[QuotaRequirement] = []
    gaps: list[QuotaCoverageGap] = []
    lines: list[str] = []
    for spec in _aligned_filesystem_specs(payload=payload, target_ref=target_ref):
        existing = _get_filesystem_by_name(
            nebius_api=nebius_api,
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
    nebius_api: SoperatorMigrationNebiusApi,
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
            nebius_api=nebius_api,
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

    live_node_groups = _list_node_groups(nebius_api=nebius_api, cluster_id=cluster_id)
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
                    nebius_api=nebius_api,
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
    nebius_api: SoperatorMigrationNebiusApi,
) -> tuple[list[QuotaRequirement], list[QuotaCoverageGap], list[str]]:
    _tenant_id, project_id, region = _nebius_identity(payload)
    planned_groups, lines = _planned_target_node_group_quota_inputs(
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        worker_node_groups=worker_node_groups,
        nebius_api=nebius_api,
    )
    if not planned_groups:
        return [], [], lines
    requirements, gaps = estimate_mk8s_quota_requirements(
        project_id=project_id,
        region=region,
        instance_id=f"{target_ref}-soperator-migration",
        inputs={"node_groups": dict(planned_groups)},
        context="external soperator upgrade quota preflight",
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
        totals[(item.region, item.quota_name)] = totals.get(
            (item.region, item.quota_name), 0
        ) + int(item.required)
    if not totals:
        return "no quota counters"
    parts = [
        f"{region} {quota_name}={required}"
        for (region, quota_name), required in sorted(totals.items())
    ]
    if len(parts) > 8:
        parts = [*parts[:8], f"+{len(parts) - 8} more"]
    return "; ".join(parts)


def _safe_surge_node_group_quota_requirements(
    *,
    payload: Mapping[str, Any],
    target_ref: str,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
    nebius_api: SoperatorMigrationNebiusApi,
    rollout: SoperatorExternalNodeTemplateRollout,
) -> tuple[list[QuotaRequirement], list[QuotaCoverageGap], list[str]]:
    _tenant_id, project_id, region = _nebius_identity(payload)
    service_safe_surge = (
        rollout.service_role_strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE
    )
    worker_safe_surge = rollout.strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE
    if not service_safe_surge and not worker_safe_surge:
        return (
            [],
            [],
            [
                "Quota preflight safe-surge: disabled by zero-surge service-role and worker "
                "rollout strategy; "
                "no surge quota required."
            ],
        )
    service_groups, worker_groups = _split_external_node_template_upgrade_groups(
        source_report=source_report,
        worker_node_groups=worker_node_groups,
    )
    if not service_groups and not worker_groups:
        return [], [], ["Quota preflight safe-surge: no source node groups detected."]
    waves = _external_node_template_worker_waves(worker_groups, rollout=rollout)
    safe_surge_waves: list[
        tuple[str, str, int, tuple[tuple[str, Mapping[str, Any]], ...]]
    ] = []
    if service_safe_surge:
        if rollout.service_role_max_surge_count <= 0:
            return (
                [],
                [],
                [
                    "Quota preflight safe-surge: no service-role surge quota required "
                    "because service_role_group_strategy max_surge_count is 0."
                ],
            )
        safe_surge_waves.extend(
            (
                f"service group {group_name}",
                f"service-{group_name}",
                rollout.service_role_max_surge_count,
                ((group_name, raw_group),),
            )
            for group_name, raw_group in service_groups
        )
    if worker_safe_surge:
        if rollout.strategy_max_surge_count <= 0:
            return (
                [],
                [],
                [
                    "Quota preflight safe-surge: no worker surge quota required "
                    "because worker_group_strategy max_surge_count is 0."
                ],
            )
        safe_surge_waves.extend(
            (
                f"worker wave {wave_index}",
                f"worker-wave-{wave_index}",
                rollout.strategy_max_surge_count,
                wave,
            )
            for wave_index, wave in enumerate(waves, start=1)
        )
    if not safe_surge_waves:
        return [], [], ["Quota preflight safe-surge: no active safe-surge groups detected."]
    all_requirements: list[QuotaRequirement] = []
    all_gaps: list[QuotaCoverageGap] = []
    lines: list[str] = [
        "[green]Verified[/green] safe-surge preflight: checking spare capacity for "
        + (
            f"{len(service_groups)} service group(s) serially"
            if service_safe_surge
            else "0 service group(s)"
        )
        + " and "
        + (
            _worker_rollout_budget_label(rollout, worker_group_count=len(worker_groups))
            if worker_safe_surge
            else "zero-surge worker groups"
        )
        + "."
    ]
    lines.append(
        "Quota preflight safe-surge counts "
        + "; ".join(
            f"{wave_label} requires {surge_count} temporary surge node(s) per group"
            for wave_label, _instance_suffix, surge_count, _wave in safe_surge_waves
        )
        + "."
    )
    for wave_label, instance_suffix, surge_count, wave in safe_surge_waves:
        planned_groups: dict[str, Any] = {}
        for group_name, raw_group in wave:
            node_group_id = _source_group_node_group_id(raw_group)
            if not node_group_id:
                raise SoperatorMigrationPhasePending(
                    "quota preflight requires Nebius node group id for source "
                    f"group '{group_name}'."
                )
            node_group = _node_group_payload_by_id(
                nebius_api=nebius_api,
                node_group_id=node_group_id,
            )
            template = _mapping(_mapping(node_group.get("spec")).get("template"))
            if not template:
                raise SoperatorMigrationPhasePending(
                    "quota preflight could not clone a Nebius node template for source "
                    f"group '{group_name}'."
                )
            planned_groups[f"{target_ref}-{group_name}-safe-surge"] = {
                "node_count": surge_count,
                "gpu": _source_group_is_gpu(raw_group),
                "template": dict(to_plain_data(_lower_nebius_enums(template))),
            }
        requirements, gaps = estimate_mk8s_quota_requirements(
            project_id=project_id,
            region=region,
            instance_id=f"{target_ref}-soperator-safe-surge-{instance_suffix}",
            inputs={"node_groups": planned_groups},
            context="external soperator upgrade safe-surge quota preflight",
        )
        wave_requirements = [
            item for item in requirements if item.quota_name != "mk8s.cluster.count"
        ]
        all_requirements.extend(wave_requirements)
        all_gaps.extend(gaps)
        lines.append(
            f"[green]Verified[/green] safe-surge {wave_label}: "
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
        token for token in (normalize_component_token(value) for value in values) if token
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
        timeout_seconds=_SLURM_FAST_PROBE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Soperator worker rollout preflight could not inspect Slurm jobs from a "
            "login pod before mutation: " + _command_detail(result)
        )
    jobs = [line.strip().lower() for line in result.stdout.splitlines() if line.strip()]
    if jobs:
        raise RuntimeError(
            "Soperator worker rollout preflight failed before mutation: Slurm queue is "
            "not empty (jobs "
            + _state_counts(jobs)
            + "). Wait for jobs to finish or drain the workload intentionally before "
            "rerunning the upgrade."
        )
    return ["Slurm worker rollout preflight: queue empty."]


def _run_soperator_worker_rollout_live_preflight(
    *,
    source_report: Mapping[str, Any],
    worker_node_groups: Sequence[str],
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    rollout: SoperatorExternalNodeTemplateRollout,
    job_policy: str,
    cancel_job_ids: Sequence[str],
    requeue_job_ids: Sequence[str],
    job_wait_timeout_seconds: int,
    job_refresh_interval_seconds: int,
    slurm_decision_recorder: Callable[[Mapping[str, Any]], None] | None = None,
    interactive_prompt_pause: Callable[[], Any] | None = None,
    allow_resolved_interactive_job_policy: bool = False,
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

    unavailable = [item for item in selected_nodes if not _node_ready(item) or _node_cordoned(item)]
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
            + (":NotReady" if not _node_ready(item) else ":cordoned")
            for index, item in enumerate(unavailable, start=1)
        ]
        raise RuntimeError(
            "Soperator worker rollout preflight failed before mutation: selected worker "
            "nodes must start Ready and schedulable, but "
            f"{len(unavailable)} unavailable node(s) were found. "
            "Problem nodes: " + _format_problem_node_details(details) + "."
        )

    ready = len(selected_nodes) - len(unavailable)
    lines = [
        "Worker rollout live preflight: "
        f"{ready}/{len(selected_nodes)} selected worker nodes Ready/schedulable; "
        f"current unavailable {len(unavailable)}/{unavailable_budget} ({budget_label})."
    ]
    source_snapshot = _mapping(source_report.get("snapshot"))
    if not _has_live_slurmcluster_resource(source_snapshot):
        lines.append("Slurm worker rollout preflight: no live source SlurmCluster detected.")
    else:
        lines.extend(
            _handle_external_upgrade_slurm_jobs(
                command_runner=command_runner,
                kube_context=kube_context,
                node_names=tuple(_node_name(item) for item in selected_nodes),
                policy=job_policy,
                cancel_job_ids=cancel_job_ids,
                requeue_job_ids=requeue_job_ids,
                wait_timeout_seconds=job_wait_timeout_seconds,
                refresh_interval_seconds=job_refresh_interval_seconds,
                decision_recorder=slurm_decision_recorder,
                interactive_prompt_pause=interactive_prompt_pause,
                allow_resolved_interactive_job_policy=allow_resolved_interactive_job_policy,
            )
        )
    return lines


def _quota_preflight_failure_message(report: QuotaReport) -> str:
    detail_lines = format_quota_report_lines(
        report,
        phase="external soperator upgrade",
        include_confirmed_components=True,
        markup=False,
    )
    details = "\n".join(detail_lines).strip()
    if not details:
        details = "live quota could not be confirmed for this upgrade plan"
    return (
        "External Soperator upgrade quota preflight failed before any cluster mutation. "
        "Resolve confirmed shortages, unresolved quota limits, quota coverage gaps, "
        "or quota lookup errors before rerunning the upgrade.\n" + details
    )


def _quota_preflight_success_lines(report: QuotaReport, plan_lines: Sequence[str]) -> list[str]:
    lines = list(plan_lines)
    if not report.checks and not report.coverage_gaps and not report.errors:
        lines.append("Quota preflight: no net-new External Soperator upgrade quota required.")
        return lines
    lines.extend(
        format_quota_report_lines(
            report,
            phase="external soperator upgrade",
            include_confirmed_components=True,
        )
    )
    if report.sufficient_checks:
        lines.append("Quota preflight: all checked upgrade quota requirements are sufficient.")
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
    nebius_api: SoperatorMigrationNebiusApi,
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
            nebius_api=nebius_api,
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
                nebius_api=nebius_api,
            )
        )
        requirements.extend(compute_requirements)
        gaps.extend(compute_gaps)
        plan_lines.extend(compute_lines)

    if (
        _EXTERNAL_NODE_TEMPLATE_PHASE_ID in phase_ids
        and _EXTERNAL_NODE_TEMPLATE_PHASE_ID not in completed_phases
    ):
        safe_surge_requirements, safe_surge_gaps, safe_surge_lines = (
            _safe_surge_node_group_quota_requirements(
                payload=payload,
                target_ref=target_ref,
                source_report=source_report,
                worker_node_groups=worker_node_groups,
                nebius_api=nebius_api,
                rollout=rollout,
            )
        )
        requirements.extend(safe_surge_requirements)
        gaps.extend(safe_surge_gaps)
        plan_lines.extend(safe_surge_lines)

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
            context="external soperator upgrade quota preflight",
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
    names = _login_pod_names(command_runner=command_runner, kube_context=kube_context)
    return names[0] if names else ""


def _login_pod_names(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> tuple[str, ...]:
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
        return ()
    items = parsed.get("items", []) if isinstance(parsed, Mapping) else []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return ()
    running: list[str] = []
    fallback: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        metadata = _mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        labels = _mapping(metadata.get("labels"))
        label_text = " ".join(str(value) for value in labels.values())
        phase = str(_mapping(item.get("status")).get("phase", "") or "")
        if not name or ("login" not in name and "login" not in label_text):
            continue
        if phase == "Running":
            running.append(name)
        else:
            fallback.append(name)
    return tuple(dict.fromkeys([*running, *fallback]))


def _login_service_identities(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    required: bool = False,
) -> tuple[dict[str, Any], ...]:
    result = command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "get",
            "services",
            "-o",
            "json",
            "--request-timeout=20s",
        ],
        timeout_seconds=30,
        check=False,
    )
    if result.returncode != 0:
        if required:
            raise SoperatorMigrationPhasePending(
                "login Service continuity guard failed: could not list Services before "
                f"target chart handoff: {result.stderr.strip() or result.stdout.strip() or result.returncode}."
            )
        return ()
    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        if required:
            raise SoperatorMigrationPhasePending(
                "login Service continuity guard failed: kubectl returned invalid Service JSON."
            ) from exc
        return ()
    identities: list[dict[str, Any]] = []
    for item in _sequence_of_mappings(parsed.get("items") if isinstance(parsed, Mapping) else None):
        metadata = _mapping(item.get("metadata"))
        name = str(metadata.get("name") or "").strip()
        annotations = _mapping(metadata.get("annotations"))
        labels = _mapping(metadata.get("labels"))
        label_text = " ".join(str(value) for value in labels.values())
        if "login" not in name and "login" not in label_text:
            continue
        spec = _mapping(item.get("spec"))
        status = _mapping(item.get("status"))
        ingress = _mapping(status.get("loadBalancer")).get("ingress") or []
        ingress_values: list[str] = []
        for entry in _sequence_of_mappings(ingress):
            ip = str(entry.get("ip") or "").strip()
            hostname = str(entry.get("hostname") or "").strip()
            if ip:
                ingress_values.append(ip)
            elif hostname:
                ingress_values.append(hostname)
        identities.append(
            {
                "name": name,
                "uid": str(metadata.get("uid") or "").strip(),
                "type": str(spec.get("type") or "").strip(),
                "cluster_ip": str(spec.get("clusterIP") or "").strip(),
                "load_balancer_ip": str(spec.get("loadBalancerIP") or "").strip(),
                "load_balancer_ingress": copy.deepcopy(to_plain_data(ingress)),
                "load_balancer_external": sorted(dict.fromkeys(ingress_values)),
                "load_balancer_type": str(
                    annotations.get(SOPERATOR_LOGIN_LB_TYPE_ANNOTATION)
                    or SOPERATOR_LOGIN_LB_TYPE_EXTERNAL
                ).strip()
                or SOPERATOR_LOGIN_LB_TYPE_EXTERNAL,
                "load_balancer_allocation_id": str(
                    annotations.get(SOPERATOR_LOGIN_LB_ALLOCATION_ANNOTATION) or ""
                ).strip(),
            }
        )
    if required and not identities:
        raise SoperatorMigrationPhasePending(
            "login Service continuity guard failed: no login Service identity was detected."
        )
    return tuple(identities)


def _login_service_identity_by_name(
    identities: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("name") or ""): item
        for item in identities
        if str(item.get("name") or "")
    }


def _login_service_load_balancer_addresses(identity: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    external = identity.get("load_balancer_external")
    if isinstance(external, Sequence) and not isinstance(external, (str, bytes, bytearray)):
        values.extend(str(item or "").strip() for item in external)
    for key in ("load_balancer_ip",):
        value = str(identity.get(key) or "").strip()
        if value:
            values.append(value)
    return tuple(dict.fromkeys(item for item in values if item))


def _normalize_login_load_balancer_type(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == SOPERATOR_LOGIN_LB_TYPE_INTERNAL:
        return SOPERATOR_LOGIN_LB_TYPE_INTERNAL
    return SOPERATOR_LOGIN_LB_TYPE_EXTERNAL


def _service_address_ipv4_cidr(address: str) -> str:
    try:
        parsed = ipaddress.ip_address(str(address or "").strip())
    except ValueError:
        return ""
    if parsed.version != 4:
        return ""
    return f"{parsed}/32"


def _login_service_single_ipv4_address(identity: Mapping[str, Any]) -> tuple[str, str] | None:
    addresses = _login_service_load_balancer_addresses(identity)
    if not addresses:
        return None
    ipv4_addresses: list[tuple[str, str]] = []
    non_ipv4_addresses: list[str] = []
    for address in addresses:
        cidr = _service_address_ipv4_cidr(address)
        if cidr:
            ipv4_addresses.append((address, cidr))
        else:
            non_ipv4_addresses.append(address)
    deduped_ipv4 = list(dict.fromkeys(ipv4_addresses))
    if len(deduped_ipv4) == 1:
        return deduped_ipv4[0]
    name = str(identity.get("name") or "login").strip()
    if not deduped_ipv4:
        raise SoperatorMigrationPhasePending(
            "login Service LoadBalancer allocation retention is blocked: "
            f"Service {name} has no IPv4 LoadBalancer address that can be matched to "
            "a Nebius VPC allocation: "
            + ", ".join(non_ipv4_addresses or addresses)
            + "."
        )
    raise SoperatorMigrationPhasePending(
        "login Service LoadBalancer allocation retention is blocked: "
        f"Service {name} has multiple IPv4 LoadBalancer addresses, but a single "
        "Soperator login Service can persist only one Nebius allocation id: "
        + ", ".join(address for address, _cidr in deduped_ipv4)
        + "."
    )


def _allocation_metadata(allocation: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(allocation.get("metadata"))


def _allocation_id(allocation: Mapping[str, Any]) -> str:
    return str(_allocation_metadata(allocation).get("id") or "").strip()


def _allocation_labels(allocation: Mapping[str, Any]) -> dict[str, str]:
    labels = _mapping(_allocation_metadata(allocation).get("labels"))
    return {str(key): str(value) for key, value in labels.items()}


def _normalized_ipv4_cidr(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = ipaddress.ip_network(text, strict=False)
    except ValueError:
        return ""
    if parsed.version != 4:
        return ""
    return str(parsed)


def _allocation_cidrs(allocation: Mapping[str, Any]) -> tuple[str, ...]:
    cidrs: list[str] = []
    status = _mapping(allocation.get("status"))
    details = _mapping(status.get("details"))
    cidrs.append(str(details.get("allocated_cidr") or "").strip())
    spec = _mapping(allocation.get("spec"))
    for key in ("ipv4_private", "ipv4Private", "ipv4_public", "ipv4Public"):
        cidrs.append(str(_mapping(spec.get(key)).get("cidr") or "").strip())
    return tuple(
        dict.fromkeys(
            cidr for cidr in (_normalized_ipv4_cidr(item) for item in cidrs) if cidr
        )
    )


def _allocation_load_balancer_type(allocation: Mapping[str, Any]) -> str:
    spec = _mapping(allocation.get("spec"))
    if _mapping(spec.get("ipv4_private")) or _mapping(spec.get("ipv4Private")):
        return SOPERATOR_LOGIN_LB_TYPE_INTERNAL
    if _mapping(spec.get("ipv4_public")) or _mapping(spec.get("ipv4Public")):
        return SOPERATOR_LOGIN_LB_TYPE_EXTERNAL
    cidrs = _allocation_cidrs(allocation)
    if cidrs and all(ipaddress.ip_network(cidr).network_address.is_private for cidr in cidrs):
        return SOPERATOR_LOGIN_LB_TYPE_INTERNAL
    return SOPERATOR_LOGIN_LB_TYPE_EXTERNAL


def _matching_allocations_for_service_address(
    allocations: Sequence[Mapping[str, Any]],
    *,
    address_cidr: str,
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        allocation
        for allocation in allocations
        if address_cidr in _allocation_cidrs(allocation)
    )


def _allocation_status_text(
    *,
    allocation: Mapping[str, Any],
    removed_labels: Sequence[str],
    annotation_was_present: bool,
) -> str:
    labels = _allocation_labels(allocation)
    if _NEBIUS_MANAGED_BY_LABEL in labels or removed_labels:
        return "converted-dynamic"
    return "already-static" if annotation_was_present else "converted-dynamic"


def _ensure_allocation_not_mk8s_managed(
    *,
    allocation: Mapping[str, Any],
    nebius_api: SoperatorMigrationNebiusApi,
) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    labels = _allocation_labels(allocation)
    if labels.get(_NEBIUS_MANAGED_BY_LABEL) != "mk8s":
        return allocation, ()
    allocation_id = _allocation_id(allocation)
    updated_labels = dict(labels)
    updated_labels.pop(_NEBIUS_MANAGED_BY_LABEL, None)
    try:
        updated = nebius_api.update_allocation_labels(
            allocation_id=allocation_id,
            original_allocation=allocation,
            labels=updated_labels,
        )
    except Exception as exc:
        raise SoperatorMigrationPhasePending(
            "login Service LoadBalancer allocation retention is blocked: "
            f"could not remove {_NEBIUS_MANAGED_BY_LABEL}=mk8s from Nebius VPC "
            f"allocation {allocation_id}: {exc}"
        ) from exc
    if _allocation_labels(updated).get(_NEBIUS_MANAGED_BY_LABEL) == "mk8s":
        raise SoperatorMigrationPhasePending(
            "login Service LoadBalancer allocation retention is blocked: "
            f"Nebius VPC allocation {allocation_id} still has "
            f"{_NEBIUS_MANAGED_BY_LABEL}=mk8s after label update."
        )
    return updated, (_NEBIUS_MANAGED_BY_LABEL,)


def _require_allocation_matches_login_service(
    *,
    allocation: Mapping[str, Any],
    allocation_id: str,
    service_name: str,
    address: str,
    address_cidr: str,
    load_balancer_type: str,
) -> None:
    if address_cidr not in _allocation_cidrs(allocation):
        raise SoperatorMigrationPhasePending(
            "login Service LoadBalancer allocation retention is blocked: "
            f"Service {service_name} uses address {address}, but Nebius VPC "
            f"allocation {allocation_id} does not report allocated CIDR {address_cidr}."
        )
    allocation_type = _allocation_load_balancer_type(allocation)
    if allocation_type != load_balancer_type:
        raise SoperatorMigrationPhasePending(
            "login Service LoadBalancer allocation retention is blocked: "
            f"Service {service_name} is {load_balancer_type}, but Nebius VPC "
            f"allocation {allocation_id} is {allocation_type}."
        )


def _resolve_login_service_allocation(
    *,
    identity: Mapping[str, Any],
    project_id: str,
    nebius_api: SoperatorMigrationNebiusApi,
) -> tuple[Mapping[str, Any], str, str, str, bool]:
    name = str(identity.get("name") or "login").strip()
    resolved_address = _login_service_single_ipv4_address(identity)
    if resolved_address is None:
        return {}, "", "", "", False
    address, address_cidr = resolved_address
    load_balancer_type = _normalize_login_load_balancer_type(identity.get("load_balancer_type"))
    allocation_id = str(identity.get("load_balancer_allocation_id") or "").strip()
    if allocation_id:
        try:
            allocation = nebius_api.get_allocation(allocation_id)
        except Exception as exc:
            raise SoperatorMigrationPhasePending(
                "login Service LoadBalancer allocation retention is blocked: "
                f"could not read annotated Nebius VPC allocation {allocation_id} for "
                f"Service {name}: {exc}"
            ) from exc
        _require_allocation_matches_login_service(
            allocation=allocation,
            allocation_id=allocation_id,
            service_name=name,
            address=address,
            address_cidr=address_cidr,
            load_balancer_type=load_balancer_type,
        )
        return allocation, allocation_id, address, address_cidr, True

    try:
        allocations = nebius_api.list_allocations(project_id=project_id)
    except Exception as exc:
        raise SoperatorMigrationPhasePending(
            "login Service LoadBalancer allocation retention is blocked: "
            f"could not list Nebius VPC allocations in project {project_id}: {exc}"
        ) from exc
    matches = _matching_allocations_for_service_address(allocations, address_cidr=address_cidr)
    typed_matches = tuple(
        allocation
        for allocation in matches
        if _allocation_load_balancer_type(allocation) == load_balancer_type
    )
    if not typed_matches:
        detail = (
            "matching allocations had a different public/internal type"
            if matches
            else f"no allocation reported allocated CIDR {address_cidr}"
        )
        raise SoperatorMigrationPhasePending(
            "login Service LoadBalancer allocation retention is blocked: "
            f"could not find a unique {load_balancer_type} Nebius VPC allocation "
            f"for Service {name} address {address}: {detail}."
        )
    unique_matches = {
        _allocation_id(allocation): allocation
        for allocation in typed_matches
        if _allocation_id(allocation)
    }
    if len(unique_matches) != 1:
        raise SoperatorMigrationPhasePending(
            "login Service LoadBalancer allocation retention is blocked: "
            f"Service {name} address {address} matched multiple Nebius VPC "
            "allocations: "
            + ", ".join(sorted(unique_matches))
            + "."
        )
    allocation_id, allocation = next(iter(unique_matches.items()))
    return allocation, allocation_id, address, address_cidr, False


def _annotate_login_service_load_balancer_allocation(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    service_name: str,
    allocation_id: str,
    load_balancer_type: str,
) -> None:
    annotations = [f"{SOPERATOR_LOGIN_LB_ALLOCATION_ANNOTATION}={allocation_id}"]
    if load_balancer_type == SOPERATOR_LOGIN_LB_TYPE_INTERNAL:
        annotations.append(
            f"{SOPERATOR_LOGIN_LB_TYPE_ANNOTATION}={SOPERATOR_LOGIN_LB_TYPE_INTERNAL}"
        )
    result = command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "annotate",
            "service",
            service_name,
            *annotations,
            "--overwrite",
            "--request-timeout=20s",
        ],
        timeout_seconds=30,
        check=False,
    )
    if result.returncode != 0:
        raise SoperatorMigrationPhasePending(
            "login Service LoadBalancer allocation retention is blocked: "
            f"could not annotate Service {service_name} with reusable Nebius "
            f"allocation {allocation_id}: "
            f"{result.stderr.strip() or result.stdout.strip() or result.returncode}."
        )


def apply_soperator_login_load_balancer_allocation_values(
    values: dict[str, Any],
    *,
    allocation_id: str,
    load_balancer_type: str,
    context: str,
) -> bool:
    allocation_id = str(allocation_id or "").strip()
    if not allocation_id:
        return False
    load_balancer_type = _normalize_login_load_balancer_type(load_balancer_type)
    slurm_nodes = _ensure_child_mapping(values, "slurmNodes")
    login = _ensure_child_mapping(slurm_nodes, "login")
    annotations = _ensure_child_mapping(login, "sshdServiceAnnotations")
    current_allocation_id = str(
        annotations.get(SOPERATOR_LOGIN_LB_ALLOCATION_ANNOTATION) or ""
    ).strip()
    if current_allocation_id and current_allocation_id != allocation_id:
        raise RuntimeError(
            f"{context} already declares "
            f"{SOPERATOR_LOGIN_LB_ALLOCATION_ANNOTATION}={current_allocation_id}, "
            f"but the live login Service uses allocation {allocation_id}."
        )
    changed = False
    if current_allocation_id != allocation_id:
        annotations[SOPERATOR_LOGIN_LB_ALLOCATION_ANNOTATION] = allocation_id
        changed = True
    if load_balancer_type == SOPERATOR_LOGIN_LB_TYPE_INTERNAL:
        if annotations.get(SOPERATOR_LOGIN_LB_TYPE_ANNOTATION) != SOPERATOR_LOGIN_LB_TYPE_INTERNAL:
            annotations[SOPERATOR_LOGIN_LB_TYPE_ANNOTATION] = SOPERATOR_LOGIN_LB_TYPE_INTERNAL
            changed = True
    elif SOPERATOR_LOGIN_LB_TYPE_ANNOTATION in annotations:
        annotations.pop(SOPERATOR_LOGIN_LB_TYPE_ANNOTATION, None)
        changed = True
    return changed


def stabilize_soperator_login_load_balancer_allocations(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    project_id: str,
    nebius_api: SoperatorMigrationNebiusApi,
    values: dict[str, Any] | None = None,
    service_identities: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[
    tuple[Mapping[str, Any], ...],
    tuple[SoperatorLoginLoadBalancerAllocationDecision, ...],
    tuple[str, ...],
]:
    identities = tuple(
        service_identities
        if service_identities is not None
        else _login_service_identities(
            command_runner=command_runner,
            kube_context=kube_context,
            required=True,
        )
    )
    decisions: list[SoperatorLoginLoadBalancerAllocationDecision] = []
    persisted_allocations: set[tuple[str, str]] = set()
    for identity in identities:
        if str(identity.get("type") or "").strip() != "LoadBalancer":
            continue
        service_name = str(identity.get("name") or "login").strip()
        resolved_address = _login_service_single_ipv4_address(identity)
        if resolved_address is None:
            decisions.append(
                SoperatorLoginLoadBalancerAllocationDecision(
                    service_name=service_name,
                    status="no-address",
                    load_balancer_type=_normalize_login_load_balancer_type(
                        identity.get("load_balancer_type")
                    ),
                )
            )
            continue
        allocation, allocation_id, address, address_cidr, annotation_was_present = (
            _resolve_login_service_allocation(
                identity=identity,
                project_id=project_id,
                nebius_api=nebius_api,
            )
        )
        if not allocation_id:
            continue
        allocation, removed_labels = _ensure_allocation_not_mk8s_managed(
            allocation=allocation,
            nebius_api=nebius_api,
        )
        load_balancer_type = _normalize_login_load_balancer_type(
            identity.get("load_balancer_type")
        )
        if not annotation_was_present:
            _annotate_login_service_load_balancer_allocation(
                command_runner=command_runner,
                kube_context=kube_context,
                service_name=service_name,
                allocation_id=allocation_id,
                load_balancer_type=load_balancer_type,
            )
        persisted_to_values = False
        if values is not None:
            try:
                apply_soperator_login_load_balancer_allocation_values(
                    values,
                    allocation_id=allocation_id,
                    load_balancer_type=load_balancer_type,
                    context=f"Soperator login Service {service_name}",
                )
            except RuntimeError as exc:
                raise SoperatorMigrationPhasePending(
                    "login Service LoadBalancer allocation retention is blocked: "
                    f"{exc}"
                ) from exc
            persisted_to_values = True
            persisted_allocations.add((allocation_id, load_balancer_type))
        decisions.append(
            SoperatorLoginLoadBalancerAllocationDecision(
                service_name=service_name,
                status=_allocation_status_text(
                    allocation=allocation,
                    removed_labels=removed_labels,
                    annotation_was_present=annotation_was_present,
                ),
                address=address,
                load_balancer_type=load_balancer_type,
                allocation_id=allocation_id,
                allocation_cidr=address_cidr,
                removed_labels=tuple(removed_labels),
                persisted_to_values=persisted_to_values,
            )
        )
    if len(persisted_allocations) > 1:
        raise SoperatorMigrationPhasePending(
            "login Service LoadBalancer allocation retention is blocked: multiple "
            "login LoadBalancer Services require different allocation annotations, "
            "but Soperator chart values can persist only one login Service allocation."
        )
    if not decisions:
        return identities, (), ()
    refreshed = _login_service_identities(
        command_runner=command_runner,
        kube_context=kube_context,
        required=True,
    )
    refreshed_by_name = _login_service_identity_by_name(refreshed)
    for decision in decisions:
        if not decision.allocation_id:
            continue
        refreshed_identity = refreshed_by_name.get(decision.service_name)
        if not refreshed_identity:
            raise SoperatorMigrationPhasePending(
                "login Service LoadBalancer allocation retention is blocked: "
                f"Service {decision.service_name} disappeared after allocation annotation."
            )
        refreshed_addresses = _login_service_load_balancer_addresses(refreshed_identity)
        if decision.address not in refreshed_addresses:
            raise SoperatorMigrationPhasePending(
                "login Service LoadBalancer allocation retention is blocked: "
                f"Service {decision.service_name} address changed from {decision.address} "
                f"to {', '.join(refreshed_addresses) or 'none'} after annotation."
            )
        refreshed_allocation_id = str(
            refreshed_identity.get("load_balancer_allocation_id") or ""
        ).strip()
        if refreshed_allocation_id != decision.allocation_id:
            raise SoperatorMigrationPhasePending(
                "login Service LoadBalancer allocation retention is blocked: "
                f"Service {decision.service_name} annotation "
                f"{SOPERATOR_LOGIN_LB_ALLOCATION_ANNOTATION} is "
                f"{refreshed_allocation_id or 'missing'}, expected {decision.allocation_id}."
            )
        refreshed_type = _normalize_login_load_balancer_type(
            refreshed_identity.get("load_balancer_type")
        )
        if refreshed_type != decision.load_balancer_type:
            raise SoperatorMigrationPhasePending(
                "login Service LoadBalancer allocation retention is blocked: "
                f"Service {decision.service_name} LoadBalancer type changed from "
                f"{decision.load_balancer_type} to {refreshed_type} after annotation."
            )
    lines = tuple(_login_load_balancer_allocation_decision_line(item) for item in decisions)
    return refreshed, tuple(decisions), lines


def _login_load_balancer_allocation_decision_line(
    decision: SoperatorLoginLoadBalancerAllocationDecision,
) -> str:
    if decision.status == "no-address":
        return (
            f"Login Service {decision.service_name} has no LoadBalancer address yet; "
            "allocation retention will be rechecked when an address exists."
        )
    action = (
        "Converted dynamic login LoadBalancer allocation"
        if decision.status == "converted-dynamic"
        else "Verified reusable login LoadBalancer allocation"
    )
    value_text = " and persisted it in Soperator values" if decision.persisted_to_values else ""
    return (
        f"{action} for Service {decision.service_name}: {decision.address} "
        f"({decision.load_balancer_type}) uses {decision.allocation_id}{value_text}."
    )


def _assert_login_service_stable_load_balancer_preconditions(
    service_identities: Sequence[Mapping[str, Any]],
) -> None:
    for identity in service_identities:
        if str(identity.get("type") or "").strip() != "LoadBalancer":
            continue
        addresses = _login_service_load_balancer_addresses(identity)
        if not addresses:
            continue
        allocation_id = str(identity.get("load_balancer_allocation_id") or "").strip()
        if allocation_id:
            continue
        name = str(identity.get("name") or "login").strip()
        lb_type = str(identity.get("load_balancer_type") or "external").strip() or "external"
        raise SoperatorMigrationPhasePending(
            "login Service continuity guard failed before target chart handoff: "
            f"LoadBalancer Service {name} already has {lb_type} address "
            + ", ".join(addresses)
            + f" but is still missing annotation {SOPERATOR_LOGIN_LB_ALLOCATION_ANNOTATION} "
            "after automatic allocation retention. Ensure the Nebius VPC allocation for "
            "the current public or internal address can be listed and updated, then retry."
        )


def _assert_login_service_identity_preserved(
    *,
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
) -> None:
    before_by_name = _login_service_identity_by_name(before)
    after_by_name = _login_service_identity_by_name(after)
    missing = sorted(set(before_by_name) - set(after_by_name))
    if missing:
        raise SoperatorMigrationPhasePending(
            "login Service continuity guard failed: target chart did not preserve "
            + ", ".join(missing)
            + "."
        )
    for name, before_identity in before_by_name.items():
        after_identity = after_by_name.get(name, {})
        if before_identity.get("uid") and after_identity.get("uid") != before_identity.get("uid"):
            raise SoperatorMigrationPhasePending(
                "login Service continuity guard failed: Service "
                f"{name} was recreated during target chart handoff. Preserve the Service "
                "object or bind it to a reusable Nebius load-balancer allocation before retrying."
            )
        for key in (
            "cluster_ip",
            "load_balancer_ip",
            "load_balancer_external",
            "load_balancer_type",
            "load_balancer_allocation_id",
        ):
            before_value = before_identity.get(key)
            if not before_value:
                continue
            if after_identity.get(key) != before_value:
                if key in {
                    "load_balancer_external",
                    "load_balancer_type",
                    "load_balancer_allocation_id",
                }:
                    raise SoperatorMigrationPhasePending(
                        "login Service continuity guard failed: Service "
                        f"{name} changed {key} during target chart handoff. Convert the "
                        "current Nebius LoadBalancer public or internal IP into a reusable allocation and "
                        "preserve the nebius.com/load-balancer-allocation-id annotation before "
                        "retrying."
                    )
                raise SoperatorMigrationPhasePending(
                    "login Service continuity guard failed: Service "
                    f"{name} changed {key} during target chart handoff."
            )


def _wait_for_preserved_login_service_ready_endpoints(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    service_names: Sequence[str],
    min_ready_endpoints: int = 1,
    timeout_seconds: int = 600,
    poll_interval_seconds: int = 5,
) -> dict[str, Any]:
    names = tuple(dict.fromkeys(str(name or "").strip() for name in service_names if name))
    if not names:
        return {"service_names": [], "ready_endpoints": 0, "status": "skipped"}
    deadline = time.monotonic() + max(timeout_seconds, 0)
    last_ready = 0
    while True:
        last_ready = login_service_ready_endpoint_count(
            command_runner,
            namespace=_SOPERATOR_NAMESPACE,
            service_names=names,
            kube_context=kube_context,
        )
        if last_ready >= min_ready_endpoints:
            return {"service_names": list(names), "ready_endpoints": last_ready}
        if time.monotonic() >= deadline:
            raise SoperatorMigrationPhasePending(
                "login Service continuity guard failed: preserved login Service "
                f"endpoint(s) are not ready: services={', '.join(names)}, "
                f"ready={last_ready}, required={min_ready_endpoints}."
            )
        time.sleep(max(poll_interval_seconds, 1))


def _active_login_ssh_session_probe(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    pod_names: Sequence[str] | None = None,
) -> dict[str, Any]:
    script = (
        "if command -v ss >/dev/null 2>&1; then "
        "ss -Htn state established '( sport = :22 )' | wc -l; "
        "elif command -v netstat >/dev/null 2>&1; then "
        "netstat -tn | awk '$4 ~ /:22$/ && $6 == \"ESTABLISHED\" {count++} "
        "END {print count+0}'; "
        "else echo unsupported; exit 42; fi"
    )
    pods = (
        tuple(dict.fromkeys(str(name or "").strip() for name in pod_names if str(name or "").strip()))
        if pod_names is not None
        else _login_pod_names(command_runner=command_runner, kube_context=kube_context)
    )
    pod_results: list[dict[str, Any]] = []
    total = 0
    checked = False
    for pod in pods:
        result = command_runner(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                _SOPERATOR_NAMESPACE,
                "exec",
                pod,
                "--",
                "sh",
                "-ceu",
                script,
            ],
            timeout_seconds=60,
            check=False,
        )
        output = str(result.stdout or "").strip().splitlines()
        raw_count = output[-1].strip() if output else ""
        try:
            active = int(raw_count)
        except ValueError:
            pod_results.append(
                {
                    "pod": pod,
                    "status": "unsupported" if result.returncode == 42 else "unknown",
                    "detail": result.stderr.strip() or raw_count,
                }
            )
            continue
        checked = True
        total += max(active, 0)
        pod_results.append({"pod": pod, "status": "checked", "active_sessions": active})
    status = "checked" if checked else ("no_login_pods" if not pods else "unsupported")
    return {"status": status, "active_sessions": total, "pods": pod_results}


def _wait_for_login_session_policy(
    *,
    phase: dict[str, Any],
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    policy: str,
    timeout_seconds: int,
    pod_names: Sequence[str] | None = None,
) -> list[str]:
    continuity = phase.setdefault("login_continuity", {})
    if not isinstance(continuity, dict):
        raise RuntimeError("login_continuity must be a mapping.")
    continuity["session_policy"] = policy
    continuity["session_drain_timeout_seconds"] = timeout_seconds
    if policy == EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY:
        continuity["session_drain"] = {
            "status": "skipped",
            "reason": "target-ready does not wait for existing TCP SSH sessions",
        }
        return [
            "Login continuity: target-ready policy selected; existing TCP SSH sessions "
            "remain best-effort if a backing pod or node is restarted."
        ]
    if policy == EXTERNAL_LOGIN_SESSION_POLICY_GRACE_PERIOD:
        deadline = time.monotonic() + max(timeout_seconds, 0)
        while time.monotonic() < deadline:
            time.sleep(min(30.0, max(0.0, deadline - time.monotonic())))
        continuity["session_drain"] = {
            "status": "grace_period_elapsed",
            "timeout_seconds": timeout_seconds,
        }
        return [f"Login continuity: grace-period elapsed after {timeout_seconds}s."]
    deadline = time.monotonic() + max(timeout_seconds, 0)
    last_probe: dict[str, Any] = {}
    while True:
        last_probe = _active_login_ssh_session_probe(
            command_runner=command_runner,
            kube_context=kube_context,
            pod_names=pod_names,
        )
        continuity["last_session_probe"] = last_probe
        if last_probe.get("status") != "checked":
            continuity["session_drain"] = {
                "status": "pending",
                "reason": "active SSH session detection is unavailable",
                "last_probe": last_probe,
            }
            raise SoperatorMigrationPhasePending(
                "login session drain requested but active SSH session detection is unavailable."
            )
        active_sessions = _nonnegative_int(last_probe.get("active_sessions"), fallback=0)
        if active_sessions == 0:
            continuity["session_drain"] = {
                "status": "drained",
                "last_probe": last_probe,
            }
            return ["Login continuity: active SSH sessions drained before source retirement."]
        if time.monotonic() >= deadline:
            continuity["session_drain"] = {
                "status": "pending",
                "active_sessions": active_sessions,
                "last_probe": last_probe,
            }
            raise SoperatorMigrationPhasePending(
                "login session drain timed out with "
                f"{active_sessions} active SSH session(s) on old login pods."
            )
        time.sleep(min(30.0, max(1.0, deadline - time.monotonic())))


def _ensure_rolling_login_continuity(
    *,
    phase: dict[str, Any],
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
    service_identity_before: Sequence[Mapping[str, Any]],
    source_login_pod_names: Sequence[str],
    login_session_policy: str,
    login_session_drain_timeout_seconds: int,
) -> list[str]:
    continuity = phase.setdefault("login_continuity", {})
    if not isinstance(continuity, dict):
        raise RuntimeError("rolling-compute-migration.login_continuity must be a mapping.")
    continuity["status"] = "checking"
    continuity["service_identity_before_handoff"] = [
        dict(to_plain_data(item)) for item in service_identity_before
    ]
    continuity["source_login_pods_before_handoff"] = list(source_login_pod_names)
    service_ready = wait_for_login_service_ready_endpoints(
        command_runner,
        namespace=_SOPERATOR_NAMESPACE,
        target_ref=target_ref,
        kube_context=kube_context,
        timeout_seconds=600,
    )
    continuity["service_ready_before_source_retirement"] = service_ready
    rollout_ready = wait_for_login_statefulset_rollout_with_ready_endpoint_guard(
        command_runner,
        namespace=_SOPERATOR_NAMESPACE,
        target_ref=target_ref,
        kube_context=kube_context,
        timeout_seconds=900,
    )
    continuity["statefulset_ready_before_source_retirement"] = rollout_ready
    service_identity_after = _login_service_identities(
        command_runner=command_runner,
        kube_context=kube_context,
        required=True,
    )
    continuity["service_identity_after_handoff"] = [
        dict(to_plain_data(item)) for item in service_identity_after
    ]
    _assert_login_service_identity_preserved(
        before=service_identity_before,
        after=service_identity_after,
    )
    preserved_service_names = tuple(
        str(item.get("name") or "")
        for item in service_identity_before
        if str(item.get("name") or "")
    )
    continuity["preserved_service_ready_after_handoff"] = (
        _wait_for_preserved_login_service_ready_endpoints(
            command_runner=command_runner,
            kube_context=kube_context,
            service_names=preserved_service_names,
            timeout_seconds=600,
        )
    )
    smoke = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("scontrol", "ping"),
        check=False,
        timeout_seconds=120,
    )
    if smoke.returncode != 0 and "login pod not found" in smoke.stderr.lower():
        continuity["slurm_smoke"] = {
            "status": "skipped",
            "reason": "login pod not found",
            "returncode": smoke.returncode,
            "stdout": smoke.stdout.strip(),
            "stderr": smoke.stderr.strip(),
        }
        smoke_lines = [
            "Login continuity: Slurm smoke skipped because no login pod was available."
        ]
    else:
        continuity["slurm_smoke"] = {
            "status": "passed" if smoke.returncode == 0 else "failed",
            "returncode": smoke.returncode,
            "stdout": smoke.stdout.strip(),
            "stderr": smoke.stderr.strip(),
        }
        if smoke.returncode != 0:
            raise SoperatorMigrationPhasePending(
                "login continuity guard failed: Slurm smoke check from login did not pass."
            )
        smoke_lines = ["Login continuity: Slurm smoke check from login passed."]
    lines = _wait_for_login_session_policy(
        phase=phase,
        command_runner=command_runner,
        kube_context=kube_context,
        policy=login_session_policy,
        timeout_seconds=login_session_drain_timeout_seconds,
        pod_names=source_login_pod_names,
    )
    continuity["status"] = "target_ready"
    continuity["checked_at"] = _utc_now()
    return [
        "Login continuity: target login StatefulSet and ready Service endpoints verified "
        "before source login retirement.",
        *smoke_lines,
        *lines,
    ]



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

    def _login_exec_args(exec_args: Sequence[str]) -> list[str]:
        return [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "exec",
            pod,
            "--",
            *exec_args,
        ]

    login_args = _login_exec_args(args)
    try:
        result = command_runner(
            login_args,
            check=False,
            timeout_seconds=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        result = SoperatorMigrationCommandResult(
            tuple(str(item) for item in login_args),
            124,
            "",
            f"kubectl exec login pod timed out after {timeout_seconds}s",
        )
    if _slurm_cli_needs_controller_fallback(result):
        slurm_conf_args = _slurm_cli_with_legacy_conf(args)
        if slurm_conf_args != tuple(args):
            slurm_conf_login_args = _login_exec_args(slurm_conf_args)
            try:
                slurm_conf_result = command_runner(
                    slurm_conf_login_args,
                    check=False,
                    timeout_seconds=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                slurm_conf_result = SoperatorMigrationCommandResult(
                    tuple(str(item) for item in slurm_conf_login_args),
                    124,
                    "",
                    f"kubectl exec login pod timed out after {timeout_seconds}s",
                )
            if slurm_conf_result.returncode == 0:
                return slurm_conf_result
        controller_args = [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "exec",
            _SOPERATOR_CONTROLLER_POD,
            "-c",
            _SOPERATOR_CONTROLLER_CONTAINER,
            "--",
            *args,
        ]
        try:
            controller_result = command_runner(
                controller_args,
                check=False,
                timeout_seconds=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            controller_result = SoperatorMigrationCommandResult(
                tuple(str(item) for item in controller_args),
                124,
                "",
                f"kubectl exec controller pod timed out after {timeout_seconds}s",
            )
        if controller_result.returncode == 0:
            return controller_result
    if check and result.returncode != 0:
        raise RuntimeError(_command_detail(result))
    return result


def _slurm_cli_with_legacy_conf(args: Sequence[str]) -> tuple[str, ...]:
    selected = tuple(str(item) for item in args)
    if not selected or selected[0] not in _SOPERATOR_SLURM_CLI_NAMES:
        return selected
    if selected[:2] == ("env", f"SLURM_CONF={_SOPERATOR_LEGACY_SLURM_CONF}"):
        return selected
    return ("env", f"SLURM_CONF={_SOPERATOR_LEGACY_SLURM_CONF}", *selected)


def _slurm_cli_needs_controller_fallback(result: SoperatorMigrationCommandResult) -> bool:
    if result.returncode == 0:
        return False
    detail = _command_detail(result).lower()
    return any(
        marker in detail
        for marker in (
            "container not found",
            "could not establish a configuration source",
            "dns srv lookup failed",
            "resolve_ctls_from_dns_srv",
            "failed to fetch config",
            "timed out",
            "unable to upgrade connection",
        )
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


def _slurm_inspection_transient_unavailable(value: object) -> bool:
    detail = str(value or "").lower()
    return any(
        marker in detail
        for marker in (
            "container not found",
            "containercreating",
            "podinitializing",
            "pod is not running",
            "unable to upgrade connection",
        )
    )


def _external_upgrade_parse_slurm_jobs(
    output: str,
    *,
    impact_scope: str = "allocated-node",
) -> tuple[AffectedSlurmJob, ...]:
    return parse_squeue_jobs(output, impact_scope=impact_scope)


def _external_upgrade_parse_slurm_node_aliases(output: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    current_node = ""
    for raw_line in output.splitlines():
        tokens = str(raw_line or "").strip().split()
        if not tokens:
            continue
        for token in tokens:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not value or value in {"(null)", "N/A"}:
                continue
            if key == "NodeName":
                current_node = value
                aliases[value] = value
                continue
            if key in {"NodeHostName", "NodeAddr", "InstanceId"} and current_node:
                aliases[value] = current_node
    return aliases


def _is_nebius_compute_instance_node_name(value: str) -> bool:
    return bool(re.fullmatch(r"computeinstance-[A-Za-z0-9-]+", value))


def _external_upgrade_live_kubernetes_node_names(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> set[str] | None:
    payload = _json_from_command(
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
        timeout_seconds=60,
        check=False,
    )
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    names: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = str(_mapping(item.get("metadata")).get("name", "") or "").strip()
        if name:
            names.add(name)
    return names


def _external_upgrade_live_unresolved_slurm_nodes(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    nodes: Sequence[str],
) -> tuple[str, ...]:
    unresolved = tuple(str(node or "").strip() for node in nodes if str(node or "").strip())
    if not any(_is_nebius_compute_instance_node_name(node) for node in unresolved):
        return unresolved
    live_nodes = _external_upgrade_live_kubernetes_node_names(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    if live_nodes is None:
        return unresolved
    return tuple(
        node
        for node in unresolved
        if not _is_nebius_compute_instance_node_name(node) or node in live_nodes
    )


def _external_upgrade_slurm_node_filter(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    node_names: Sequence[str],
) -> tuple[str, ...]:
    selected_nodes = tuple(
        str(node or "").strip() for node in node_names if str(node or "").strip()
    )
    if not selected_nodes:
        return ()
    result = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("scontrol", "show", "nodes"),
        check=False,
        timeout_seconds=_SLURM_FAST_PROBE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return selected_nodes
    aliases = _external_upgrade_parse_slurm_node_aliases(result.stdout)
    if not aliases:
        return selected_nodes
    resolved: list[str] = []
    unresolved: list[str] = []
    for node in selected_nodes:
        slurm_node = aliases.get(node)
        if not slurm_node:
            unresolved.append(node)
            continue
        if slurm_node not in resolved:
            resolved.append(slurm_node)
    if unresolved:
        unresolved = list(
            _external_upgrade_live_unresolved_slurm_nodes(
                command_runner=command_runner,
                kube_context=kube_context,
                nodes=unresolved,
            )
        )
    if unresolved:
        raise RuntimeError(
            "External Soperator upgrade could not map Kubernetes node(s) to Slurm node "
            "names for affected-node job inspection: "
            + ", ".join(unresolved)
            + ". Rerun discovery/onboarding after Slurm node aliases are visible, or "
            "resolve the node identity mismatch before using --job-policy."
        )
    return tuple(resolved)


def _external_upgrade_worker_nodeset_pod_candidates(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> tuple[str, ...]:
    payload = _json_from_command(
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
        timeout_seconds=60,
        check=False,
    )
    items = payload.get("items")
    if not isinstance(items, list):
        return ()
    selected: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        status = _mapping(item.get("status"))
        if str(status.get("phase", "") or "") != "Running":
            continue
        metadata = _mapping(item.get("metadata"))
        labels = _mapping(metadata.get("labels"))
        nodeset_name = ""
        for label_key in _SOPERATOR_NODESET_LABEL_KEYS:
            nodeset_name = str(labels.get(label_key, "") or "").strip()
            if nodeset_name:
                break
        if not normalize_component_token(nodeset_name).startswith(_SOURCE_WORKER_NODESET_PREFIX):
            continue
        pod_name = str(metadata.get("name", "") or "").strip()
        if pod_name and pod_name not in seen:
            seen.add(pod_name)
            selected.append(pod_name)
    return tuple(selected)


def _external_upgrade_worker_nodeset_slurm_nodes(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> tuple[str, ...]:
    return _external_upgrade_slurm_node_filter(
        command_runner=command_runner,
        kube_context=kube_context,
        node_names=_external_upgrade_worker_nodeset_pod_candidates(
            command_runner=command_runner,
            kube_context=kube_context,
        ),
    )


_EXTERNAL_UPGRADE_SQUEUE_FORMAT = "%i|%u|%T|%P|%N|%n|%Y|%r|%M|%l|%L|%j"
_EXTERNAL_UPGRADE_ACTIVE_SQUEUE_STATES = "RUNNING,COMPLETING,CONFIGURING,SUSPENDED,STOPPED"


def _external_upgrade_affected_partitions(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    node_names: Sequence[str],
) -> tuple[str, ...]:
    selected_nodes = tuple(
        str(node or "").strip() for node in node_names if str(node or "").strip()
    )
    if not selected_nodes:
        return ()
    result = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("scontrol", "show", "node", ",".join(selected_nodes), "-o"),
        check=False,
        timeout_seconds=_SLURM_FAST_PROBE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "External Soperator upgrade could not inspect Slurm partitions for affected nodes "
            f"{', '.join(selected_nodes)} before pending-job policy evaluation: "
            + _command_detail(result)
        )
    partitions = affected_slurm_partitions_from_scontrol_show_node(result.stdout)
    if not partitions:
        raise RuntimeError(
            "External Soperator upgrade could not determine Slurm partitions for affected nodes "
            f"{', '.join(selected_nodes)} before pending-job policy evaluation. "
            "Resolve Slurm node partition metadata before using --job-policy."
        )
    return partitions


def _external_upgrade_slurm_jobs(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    node_names: Sequence[str],
) -> tuple[AffectedSlurmJob, ...]:
    selected_nodes = _external_upgrade_slurm_node_filter(
        command_runner=command_runner,
        kube_context=kube_context,
        node_names=node_names,
    )
    if not selected_nodes:
        return ()

    def _squeue(
        *,
        states: str,
        nodes: Sequence[str] = (),
        partitions: Sequence[str] = (),
    ) -> SoperatorMigrationCommandResult:
        args: list[str] = [
            "squeue",
            "-h",
            "-t",
            states,
            "-o",
            _EXTERNAL_UPGRADE_SQUEUE_FORMAT,
        ]
        filtered_nodes = tuple(str(node or "").strip() for node in nodes if str(node or "").strip())
        if filtered_nodes:
            args.extend(["-w", ",".join(filtered_nodes)])
        filtered_partitions = tuple(
            str(partition or "").strip() for partition in partitions if str(partition or "").strip()
        )
        if filtered_partitions:
            args.extend(["-p", ",".join(filtered_partitions)])
        return _kubectl_exec_login(
            command_runner=command_runner,
            kube_context=kube_context,
            args=tuple(args),
            check=False,
            timeout_seconds=_SLURM_FAST_PROBE_TIMEOUT_SECONDS,
        )

    result = _squeue(states=_EXTERNAL_UPGRADE_ACTIVE_SQUEUE_STATES, nodes=selected_nodes)
    if (
        result.returncode != 0
        and selected_nodes
        and "invalid node name" in _command_detail(result).lower()
    ):
        raise RuntimeError(
            "External Soperator upgrade could not inspect Slurm jobs for affected nodes "
            "because Slurm rejected the scoped node filter "
            f"{', '.join(selected_nodes)}: " + _command_detail(result)
        )
    if result.returncode != 0:
        raise RuntimeError(
            "External Soperator upgrade could not inspect Slurm jobs from a login pod: "
            + _command_detail(result)
        )
    active_jobs = _external_upgrade_parse_slurm_jobs(
        result.stdout,
        impact_scope="allocated-node",
    )
    partitions = _external_upgrade_affected_partitions(
        command_runner=command_runner,
        kube_context=kube_context,
        node_names=selected_nodes,
    )
    pending_result = _squeue(states="PENDING", partitions=partitions)
    if pending_result.returncode != 0:
        raise RuntimeError(
            "External Soperator upgrade could not inspect pending Slurm jobs from a login pod: "
            + _command_detail(pending_result)
        )
    pending_candidates = list(
        _external_upgrade_parse_slurm_jobs(pending_result.stdout, impact_scope="pending")
    )
    node_pending_result = _squeue(states="PENDING")
    if node_pending_result.returncode != 0:
        raise RuntimeError(
            "External Soperator upgrade could not inspect node-scoped pending Slurm jobs "
            "from a login pod: " + _command_detail(node_pending_result)
        )
    pending_candidates.extend(
        _external_upgrade_parse_slurm_jobs(node_pending_result.stdout, impact_scope="pending")
    )
    pending_jobs = filter_affected_pending_slurm_jobs(
        dedupe_slurm_jobs(pending_candidates),
        affected_nodes=selected_nodes,
        affected_partitions=partitions,
    )
    return dedupe_slurm_jobs((*active_jobs, *pending_jobs))


def _external_upgrade_cancel_slurm_jobs(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    job_ids: Sequence[str],
) -> None:
    selected = tuple(str(job_id or "").strip() for job_id in job_ids if str(job_id or "").strip())
    if not selected:
        return
    result = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("scancel", *selected),
        check=False,
        timeout_seconds=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "External Soperator upgrade could not cancel Slurm jobs: " + _command_detail(result)
        )


def _external_upgrade_requeue_slurm_jobs(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    job_ids: Sequence[str],
    hold: bool = False,
) -> None:
    selected = tuple(str(job_id or "").strip() for job_id in job_ids if str(job_id or "").strip())
    if not selected:
        return
    action = "requeuehold" if hold else "requeue"
    result = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("scontrol", action, *selected),
        check=False,
        timeout_seconds=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"External Soperator upgrade could not {action} Slurm jobs: " + _command_detail(result)
        )


def _wait_for_external_upgrade_requeued_jobs_to_leave_nodes(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    node_names: Sequence[str],
    job_ids: Sequence[str],
    timeout_seconds: int,
    refresh_interval_seconds: int,
) -> tuple[AffectedSlurmJob, ...]:
    selected = frozenset(
        str(job_id or "").strip() for job_id in job_ids if str(job_id or "").strip()
    )
    if not selected:
        return _external_upgrade_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            node_names=node_names,
        )
    transition_timeout = min(timeout_seconds if timeout_seconds > 0 else 300, 300)
    deadline = time.monotonic() + max(transition_timeout, 1)
    while True:
        jobs = _external_upgrade_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            node_names=node_names,
        )
        selected_remaining = [job for job in jobs if job.job_id in selected]
        if not selected_remaining:
            return jobs
        if time.monotonic() >= deadline:
            raise SoperatorMigrationPhasePending(
                f"{len(selected_remaining)} affected Slurm job(s) remain after requeue/requeuehold. "
                "Rerun after they finish or cancel them explicitly."
            )
        time.sleep(max(refresh_interval_seconds, 1))


_EXTERNAL_UPGRADE_JOBS_TABLE_TITLE = "Affected Slurm jobs blocking external Soperator upgrade"


def _print_external_upgrade_jobs_table(
    jobs: Sequence[AffectedSlurmJob],
) -> None:
    _console.print(build_slurm_jobs_table(jobs, title=_EXTERNAL_UPGRADE_JOBS_TABLE_TITLE))


@contextmanager
def _external_upgrade_slurm_prompt_paused(
    pause: Callable[[], Any] | None,
) -> Iterator[None]:
    if callable(pause):
        with pause():
            yield
        return
    yield


def _external_upgrade_is_tty_session() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _external_upgrade_text_prompt(message: str, default: str, show_default: bool) -> str:
    suffix = f" [{default}]" if show_default and default else ""
    raw = input(f"{message}{suffix}: ").strip()
    return raw or default


def _external_upgrade_job_policy(
    policy: str | None,
    *,
    default_policy: str | None = None,
    allow_resolved_interactive: bool = False,
) -> str:
    interactive = _external_upgrade_is_tty_session()
    resolved_policy = str(policy or "").strip()
    if not resolved_policy:
        resolved_policy = str(default_policy or "").strip()
        if not resolved_policy:
            resolved_policy = "interactive" if interactive else "fail"
    if resolved_policy not in _EXTERNAL_UPGRADE_JOB_POLICIES:
        raise RuntimeError(
            "--job-policy must be one of: " + ", ".join(sorted(_EXTERNAL_UPGRADE_JOB_POLICIES))
        )
    if resolved_policy == "interactive" and not (interactive or allow_resolved_interactive):
        raise RuntimeError(
            "--job-policy interactive requires an interactive terminal. Use "
            "fail, wait-to-finish, wait-then-cancel, cancel-selected, cancel-all, "
            "requeue-selected, requeue-all, requeue-hold-selected, or "
            "requeue-hold-all."
        )
    return resolved_policy


def _prompt_external_upgrade_slurm_job_control(
    jobs: Sequence[AffectedSlurmJob],
    *,
    prompt_pause: Callable[[], Any] | None,
    jobs_provider: Callable[[], Sequence[AffectedSlurmJob]] | None = None,
    wait_timeout_seconds: int = 0,
    refresh_interval_seconds: int = 30,
) -> tuple[str, tuple[str, ...]]:
    with _external_upgrade_slurm_prompt_paused(prompt_pause):
        return prompt_slurm_job_control(
            jobs,
            console=_console,
            table_title=_EXTERNAL_UPGRADE_JOBS_TABLE_TITLE,
            is_tty=_external_upgrade_is_tty_session(),
            text_prompt=_external_upgrade_text_prompt,
            jobs_provider=jobs_provider,
            wait_timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=refresh_interval_seconds,
        )


def _external_upgrade_wait_dashboard(
    jobs: Sequence[AffectedSlurmJob],
    *,
    local_elapsed_seconds: int,
    poll_interval_seconds: int,
) -> Table:
    return build_slurm_wait_dashboard(
        jobs,
        local_elapsed_seconds=local_elapsed_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def _wait_for_external_upgrade_slurm_jobs_until_timeout(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    node_names: Sequence[str],
    timeout_seconds: int,
    refresh_interval_seconds: int,
) -> tuple[AffectedSlurmJob, ...]:
    deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    poll_interval = max(refresh_interval_seconds, 1)

    def _jobs() -> tuple[AffectedSlurmJob, ...]:
        return _external_upgrade_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            node_names=node_names,
        )

    def _timed_out() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    def _sleep_seconds() -> float:
        sleep_seconds = float(poll_interval)
        if deadline is not None:
            sleep_seconds = min(sleep_seconds, max(deadline - time.monotonic(), 0.0))
        return sleep_seconds

    if not getattr(_console, "is_terminal", False):
        while True:
            try:
                jobs = _jobs()
            except RuntimeError as exc:
                if not _slurm_inspection_transient_unavailable(exc):
                    raise
                if _timed_out():
                    raise RuntimeError(
                        "Timed out waiting for Slurm login readiness before job inspection: "
                        + str(exc)
                    ) from exc
                _console.print(
                    "Waiting for Slurm login readiness before job inspection: " + str(exc),
                    soft_wrap=True,
                )
                sleep_seconds = _sleep_seconds()
                if sleep_seconds <= 0:
                    raise RuntimeError(
                        "Timed out waiting for Slurm login readiness before job inspection: "
                        + str(exc)
                    ) from exc
                time.sleep(sleep_seconds)
                continue
            if not jobs:
                _console.print("No affected Slurm jobs remain", soft_wrap=True)
                return ()
            _console.print(
                _external_upgrade_wait_dashboard(
                    jobs,
                    local_elapsed_seconds=0,
                    poll_interval_seconds=poll_interval,
                )
            )
            if _timed_out():
                return jobs
            sleep_seconds = _sleep_seconds()
            if sleep_seconds <= 0:
                return jobs
            time.sleep(sleep_seconds)

    with Live(console=_console, refresh_per_second=4) as live:
        while True:
            try:
                jobs = _jobs()
            except RuntimeError as exc:
                if not _slurm_inspection_transient_unavailable(exc):
                    raise
                if _timed_out():
                    raise RuntimeError(
                        "Timed out waiting for Slurm login readiness before job inspection: "
                        + str(exc)
                    ) from exc
                for local_elapsed in range(poll_interval):
                    live.update(
                        Table(
                            title=(
                                "Waiting for Slurm login readiness before job inspection "
                                f"({local_elapsed}s)"
                            )
                        )
                    )
                    if _timed_out():
                        raise RuntimeError(
                            "Timed out waiting for Slurm login readiness before job inspection: "
                            + str(exc)
                        ) from exc
                    time.sleep(1)
                continue
            if not jobs:
                live.update(Table(title="No affected Slurm jobs remain"))
                return ()
            for local_elapsed in range(poll_interval):
                live.update(
                    _external_upgrade_wait_dashboard(
                        jobs,
                        local_elapsed_seconds=local_elapsed,
                        poll_interval_seconds=poll_interval,
                    )
                )
                if _timed_out():
                    return jobs
                time.sleep(1)


def _wait_for_external_upgrade_slurm_jobs(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    node_names: Sequence[str],
    timeout_seconds: int,
    refresh_interval_seconds: int,
) -> None:
    remaining = _wait_for_external_upgrade_slurm_jobs_until_timeout(
        command_runner=command_runner,
        kube_context=kube_context,
        node_names=node_names,
        timeout_seconds=timeout_seconds,
        refresh_interval_seconds=refresh_interval_seconds,
    )
    if remaining:
        raise RuntimeError(
            "Timed out waiting for Slurm jobs to finish. Rerun with a longer "
            "--job-wait-timeout, cancel, requeue, or requeue-hold selected jobs, "
            "or use --job-policy wait-then-cancel."
        )


def _handle_external_upgrade_slurm_jobs(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    node_names: Sequence[str],
    policy: str | None,
    cancel_job_ids: Sequence[str],
    requeue_job_ids: Sequence[str],
    wait_timeout_seconds: int,
    refresh_interval_seconds: int,
    decision_recorder: Callable[[Mapping[str, Any]], None] | None = None,
    interactive_prompt_pause: Callable[[], Any] | None = None,
    allow_resolved_interactive_job_policy: bool = False,
) -> list[str]:
    def _record(action: str, **details: Any) -> None:
        if decision_recorder is None:
            return
        decision_recorder(
            {
                "at": _utc_now(),
                "action": action,
                **to_plain_data(details),
            }
        )

    resolved_policy = _external_upgrade_job_policy(
        policy,
        allow_resolved_interactive=allow_resolved_interactive_job_policy,
    )
    if resolved_policy == "wait-then-cancel" and wait_timeout_seconds <= 0:
        raise RuntimeError(
            "--job-policy wait-then-cancel requires a positive --job-wait-timeout. "
            "Use --job-policy wait-to-finish --job-wait-timeout 0s for an unlimited wait."
        )
    selected_nodes = tuple(
        str(node or "").strip() for node in node_names if str(node or "").strip()
    )

    def _wait_until_timeout(timeout_seconds: int) -> tuple[AffectedSlurmJob, ...]:
        with _external_upgrade_slurm_prompt_paused(interactive_prompt_pause):
            return _wait_for_external_upgrade_slurm_jobs_until_timeout(
                command_runner=command_runner,
                kube_context=kube_context,
                node_names=selected_nodes,
                timeout_seconds=timeout_seconds,
                refresh_interval_seconds=refresh_interval_seconds,
            )

    def _wait_then_cancel() -> list[str]:
        _record(
            "wait-then-cancel-wait-started",
            timeout_seconds=wait_timeout_seconds,
            refresh_interval_seconds=refresh_interval_seconds,
        )
        remaining = _wait_until_timeout(wait_timeout_seconds)
        if not remaining:
            _record("wait-then-cancel-wait-completed")
            return ["Slurm job preflight: waited for affected jobs to finish."]
        remaining = _external_upgrade_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            node_names=selected_nodes,
        )
        if not remaining:
            _record("wait-then-cancel-cleared-after-timeout-refresh")
            return ["Slurm job preflight: waited for affected jobs to finish."]
        selected = tuple(job.job_id for job in remaining)
        _record(
            "wait-then-cancel-timeout",
            timeout_seconds=wait_timeout_seconds,
            job_ids=selected,
            jobs=[job.__dict__ for job in remaining],
        )
        _console.print(
            "Slurm job wait timeout reached; cancelling still-affected displayed jobs: "
            + ", ".join(selected),
            soft_wrap=True,
        )
        _external_upgrade_cancel_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            job_ids=selected,
        )
        _record(
            "wait-then-cancel-auto-cancel",
            job_ids=selected,
            clear_timeout_seconds=_EXTERNAL_UPGRADE_CANCEL_CLEAR_TIMEOUT_SECONDS,
        )
        still_remaining = _wait_until_timeout(_EXTERNAL_UPGRADE_CANCEL_CLEAR_TIMEOUT_SECONDS)
        if still_remaining:
            remaining_ids = tuple(job.job_id for job in still_remaining)
            _record(
                "wait-then-cancel-clear-timeout",
                job_ids=remaining_ids,
                jobs=[job.__dict__ for job in still_remaining],
            )
            raise RuntimeError(
                "Affected Slurm jobs remain after automatic cancellation: "
                + ", ".join(remaining_ids)
                + ". Clear them manually before rerunning the upgrade."
            )
        _record("wait-then-cancel-cleared", job_ids=selected)
        return ["Slurm job preflight: waited, cancelled timed-out affected jobs, and cleared."]

    jobs = _external_upgrade_slurm_jobs(
        command_runner=command_runner,
        kube_context=kube_context,
        node_names=selected_nodes,
    )
    if not jobs:
        _record("no-blocking-jobs", policy=resolved_policy, node_names=selected_nodes)
        return ["Slurm job preflight: no affected jobs in the upgrade scope."]
    _record(
        "blocking-jobs-detected",
        policy=resolved_policy,
        node_names=selected_nodes,
        jobs=[job.__dict__ for job in jobs],
    )
    if resolved_policy == "interactive":
        while True:
            action, selected_ids = _prompt_external_upgrade_slurm_job_control(
                jobs,
                prompt_pause=interactive_prompt_pause,
                jobs_provider=lambda: _external_upgrade_slurm_jobs(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    node_names=selected_nodes,
                ),
                wait_timeout_seconds=wait_timeout_seconds,
                refresh_interval_seconds=refresh_interval_seconds,
            )
            _record("operator-selected", selection=action, job_ids=selected_ids)
            if action == "refresh":
                jobs = _external_upgrade_slurm_jobs(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    node_names=selected_nodes,
                )
                if not jobs:
                    _record("no-blocking-jobs-after-refresh", policy=resolved_policy)
                    return ["Slurm job preflight: no affected jobs remain after refresh."]
                continue
            if action == SLURM_JOB_CONTROL_WAIT_COMPLETED:
                _record(
                    "wait-started",
                    timeout_seconds=wait_timeout_seconds,
                    refresh_interval_seconds=refresh_interval_seconds,
                )
                _record("wait-completed")
                return ["Slurm job preflight: waited for affected jobs to finish."]
            if action == SLURM_JOB_CONTROL_WAIT_TIMEOUT:
                _record(
                    "wait-started",
                    timeout_seconds=wait_timeout_seconds,
                    refresh_interval_seconds=refresh_interval_seconds,
                )
                raise RuntimeError(
                    "Timed out waiting for Slurm jobs to finish. Rerun with a longer "
                    "--job-wait-timeout, cancel, requeue, or requeue-hold selected jobs, "
                    "or use --job-policy wait-then-cancel."
                )
            if action == "wait-to-finish":
                _record(
                    "wait-started",
                    timeout_seconds=wait_timeout_seconds,
                    refresh_interval_seconds=refresh_interval_seconds,
                )
                with _external_upgrade_slurm_prompt_paused(interactive_prompt_pause):
                    _wait_for_external_upgrade_slurm_jobs(
                        command_runner=command_runner,
                        kube_context=kube_context,
                        node_names=selected_nodes,
                        timeout_seconds=wait_timeout_seconds,
                        refresh_interval_seconds=refresh_interval_seconds,
                    )
                _record("wait-completed")
                return ["Slurm job preflight: waited for affected jobs to finish."]
            if action == "cancel-selected":
                selected = selected_display_job_ids(jobs, selected_ids, action=action)
                if not selected:
                    _console.print("[yellow]Select at least one displayed job to cancel.[/yellow]")
                    continue
                _record("cancel-selected", job_ids=selected)
                _external_upgrade_cancel_slurm_jobs(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    job_ids=selected,
                )
                jobs = _external_upgrade_slurm_jobs(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    node_names=selected_nodes,
                )
                if not jobs:
                    return ["Slurm job preflight: selected jobs cancelled."]
                continue
            if action == "cancel-all":
                selected = tuple(job.job_id for job in jobs)
                _record("cancel-all", job_ids=selected)
                _external_upgrade_cancel_slurm_jobs(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    job_ids=selected,
                )
                jobs = _external_upgrade_slurm_jobs(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    node_names=selected_nodes,
                )
                if not jobs:
                    return ["Slurm job preflight: all affected jobs cancelled."]
                continue
            if action == "requeue-selected":
                try:
                    selected = ensure_requeueable_slurm_jobs(jobs, selected_ids, action=action)
                except RuntimeError as exc:
                    _console.print(f"[yellow]{exc}[/yellow]")
                    continue
                if not selected:
                    _console.print("[yellow]Select at least one displayed job to requeue.[/yellow]")
                    continue
                _record("requeue-selected", job_ids=selected)
                _external_upgrade_requeue_slurm_jobs(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    job_ids=selected,
                )
                jobs = _wait_for_external_upgrade_requeued_jobs_to_leave_nodes(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    node_names=selected_nodes,
                    job_ids=selected,
                    timeout_seconds=wait_timeout_seconds,
                    refresh_interval_seconds=refresh_interval_seconds,
                )
                if not jobs:
                    return ["Slurm job preflight: selected jobs requeued."]
                continue
            if action == "requeue-all":
                selected = tuple(selected_ids) or tuple(job.job_id for job in jobs)
                try:
                    selected = ensure_requeueable_slurm_jobs(jobs, selected, action=action)
                except RuntimeError as exc:
                    _console.print(f"[yellow]{exc}[/yellow]")
                    continue
                _record("requeue-all", job_ids=selected)
                _external_upgrade_requeue_slurm_jobs(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    job_ids=selected,
                )
                jobs = _wait_for_external_upgrade_requeued_jobs_to_leave_nodes(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    node_names=selected_nodes,
                    job_ids=selected,
                    timeout_seconds=wait_timeout_seconds,
                    refresh_interval_seconds=refresh_interval_seconds,
                )
                if not jobs:
                    return ["Slurm job preflight: all affected active jobs requeued."]
                continue
            if action == "requeue-hold-selected":
                try:
                    selected = ensure_requeueable_slurm_jobs(jobs, selected_ids, action=action)
                except RuntimeError as exc:
                    _console.print(f"[yellow]{exc}[/yellow]")
                    continue
                if not selected:
                    _console.print(
                        "[yellow]Select at least one displayed job to requeue and hold.[/yellow]"
                    )
                    continue
                _record("requeue-hold-selected", job_ids=selected)
                _external_upgrade_requeue_slurm_jobs(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    job_ids=selected,
                    hold=True,
                )
                jobs = _wait_for_external_upgrade_requeued_jobs_to_leave_nodes(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    node_names=selected_nodes,
                    job_ids=selected,
                    timeout_seconds=wait_timeout_seconds,
                    refresh_interval_seconds=refresh_interval_seconds,
                )
                if not jobs:
                    return ["Slurm job preflight: selected jobs requeued and held."]
                continue
            if action == "requeue-hold-all":
                selected = tuple(selected_ids) or tuple(job.job_id for job in jobs)
                try:
                    selected = ensure_requeueable_slurm_jobs(jobs, selected, action=action)
                except RuntimeError as exc:
                    _console.print(f"[yellow]{exc}[/yellow]")
                    continue
                _record("requeue-hold-all", job_ids=selected)
                _external_upgrade_requeue_slurm_jobs(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    job_ids=selected,
                    hold=True,
                )
                jobs = _wait_for_external_upgrade_requeued_jobs_to_leave_nodes(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    node_names=selected_nodes,
                    job_ids=selected,
                    timeout_seconds=wait_timeout_seconds,
                    refresh_interval_seconds=refresh_interval_seconds,
                )
                if not jobs:
                    return ["Slurm job preflight: all affected active jobs requeued and held."]
                continue
            if action == "abort":
                _record("abort")
                raise SoperatorMigrationPhasePending(
                    "External Soperator upgrade stopped by operator while affected Slurm jobs are still present."
                )
            _console.print(
                "[yellow]Unknown action; choose refresh, wait-to-finish, cancel-selected, cancel-all, "
                "requeue-selected, requeue-all, requeue-hold-selected, requeue-hold-all, or abort.[/yellow]"
            )
    if resolved_policy == "fail":
        with _external_upgrade_slurm_prompt_paused(interactive_prompt_pause):
            _print_external_upgrade_jobs_table(jobs)
        _record("fail", job_count=len(jobs))
        raise SoperatorMigrationPhasePending(
            "Affected Slurm jobs exist for the upgrade scope. Use --job-policy wait-to-finish, "
            "--job-policy wait-then-cancel, --job-policy cancel-selected with "
            "--cancel-job, --job-policy cancel-all, --job-policy requeue-selected "
            "with --requeue-job, --job-policy requeue-all, --job-policy "
            "requeue-hold-selected with --requeue-job, or --job-policy "
            "requeue-hold-all."
        )
    if resolved_policy == "wait-then-cancel":
        return _wait_then_cancel()
    if resolved_policy == "wait-to-finish":
        _record(
            "wait-started",
            timeout_seconds=wait_timeout_seconds,
            refresh_interval_seconds=refresh_interval_seconds,
        )
        with _external_upgrade_slurm_prompt_paused(interactive_prompt_pause):
            _wait_for_external_upgrade_slurm_jobs(
                command_runner=command_runner,
                kube_context=kube_context,
                node_names=selected_nodes,
                timeout_seconds=wait_timeout_seconds,
                refresh_interval_seconds=refresh_interval_seconds,
            )
        _record("wait-completed")
        return ["Slurm job preflight: waited for affected jobs to finish."]
    remaining: tuple[AffectedSlurmJob, ...] | None = None
    if resolved_policy == "cancel-selected":
        selected = selected_display_job_ids(jobs, cancel_job_ids, action=resolved_policy)
        if not selected:
            raise RuntimeError("--job-policy cancel-selected requires at least one --cancel-job.")
        _record("cancel-selected", job_ids=selected)
        _external_upgrade_cancel_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            job_ids=selected,
        )
    elif resolved_policy == "cancel-all":
        selected = tuple(job.job_id for job in jobs)
        _record("cancel-all", job_ids=selected)
        _external_upgrade_cancel_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            job_ids=selected,
        )
    elif resolved_policy == "requeue-selected":
        selected = ensure_requeueable_slurm_jobs(jobs, requeue_job_ids, action=resolved_policy)
        if not selected:
            raise RuntimeError("--job-policy requeue-selected requires at least one --requeue-job.")
        _record("requeue-selected", job_ids=selected)
        _external_upgrade_requeue_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            job_ids=selected,
        )
        remaining = _wait_for_external_upgrade_requeued_jobs_to_leave_nodes(
            command_runner=command_runner,
            kube_context=kube_context,
            node_names=selected_nodes,
            job_ids=selected,
            timeout_seconds=wait_timeout_seconds,
            refresh_interval_seconds=refresh_interval_seconds,
        )
    elif resolved_policy == "requeue-all":
        selected = tuple(job.job_id for job in jobs)
        selected = ensure_requeueable_slurm_jobs(jobs, selected, action=resolved_policy)
        _record("requeue-all", job_ids=selected)
        _external_upgrade_requeue_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            job_ids=selected,
        )
        remaining = _wait_for_external_upgrade_requeued_jobs_to_leave_nodes(
            command_runner=command_runner,
            kube_context=kube_context,
            node_names=selected_nodes,
            job_ids=selected,
            timeout_seconds=wait_timeout_seconds,
            refresh_interval_seconds=refresh_interval_seconds,
        )
    elif resolved_policy == "requeue-hold-selected":
        selected = ensure_requeueable_slurm_jobs(jobs, requeue_job_ids, action=resolved_policy)
        if not selected:
            raise RuntimeError(
                "--job-policy requeue-hold-selected requires at least one --requeue-job."
            )
        _record("requeue-hold-selected", job_ids=selected)
        _external_upgrade_requeue_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            job_ids=selected,
            hold=True,
        )
        remaining = _wait_for_external_upgrade_requeued_jobs_to_leave_nodes(
            command_runner=command_runner,
            kube_context=kube_context,
            node_names=selected_nodes,
            job_ids=selected,
            timeout_seconds=wait_timeout_seconds,
            refresh_interval_seconds=refresh_interval_seconds,
        )
    elif resolved_policy == "requeue-hold-all":
        selected = tuple(job.job_id for job in jobs)
        selected = ensure_requeueable_slurm_jobs(jobs, selected, action=resolved_policy)
        _record("requeue-hold-all", job_ids=selected)
        _external_upgrade_requeue_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            job_ids=selected,
            hold=True,
        )
        remaining = _wait_for_external_upgrade_requeued_jobs_to_leave_nodes(
            command_runner=command_runner,
            kube_context=kube_context,
            node_names=selected_nodes,
            job_ids=selected,
            timeout_seconds=wait_timeout_seconds,
            refresh_interval_seconds=refresh_interval_seconds,
        )
    if remaining is None:
        remaining = _external_upgrade_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            node_names=selected_nodes,
        )
    if remaining:
        _record("blocking-jobs-remain", job_count=len(remaining))
        raise SoperatorMigrationPhasePending(
            f"{len(remaining)} affected Slurm job(s) remain after job policy "
            f"{resolved_policy!r}. Rerun after they finish or cancel them explicitly."
        )
    _record("blocking-jobs-cleared", policy=resolved_policy)
    return [f"Slurm job preflight: cleared affected jobs with policy {resolved_policy}."]


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


def _active_external_node_template_rollout_statuses(
    *,
    nebius_api: SoperatorMigrationNebiusApi,
    checkpoint: Mapping[str, Any],
    phase_id: str,
) -> tuple[tuple[str, ...], bool]:
    if phase_id != _EXTERNAL_NODE_TEMPLATE_PHASE_ID:
        return (), False
    phase = _mapping(_mapping(checkpoint.get("phase_state")).get(phase_id))
    node_groups = _mapping(phase.get("node_groups"))
    details: list[str] = []
    rollout_active = False
    for source_group, raw_state in node_groups.items():
        state = _mapping(raw_state)
        if str(state.get("status", "") or "").strip().lower() != "updating":
            continue
        node_group_id = str(state.get("node_group_id", "") or "").strip()
        label = (
            str(state.get("node_group_name", "") or "").strip()
            or str(state.get("source_group", "") or "").strip()
            or str(source_group)
        )
        if not node_group_id:
            details.append(f"{label}:status unavailable (missing node group id)")
            rollout_active = True
            continue
        try:
            node_group = _node_group_payload_by_id(
                nebius_api=nebius_api,
                node_group_id=node_group_id,
            )
        except Exception as exc:
            details.append(f"{label}:status unavailable ({exc})")
            rollout_active = True
            continue
        status = _mapping(node_group.get("status"))
        ready = _int_or_none(_first_mapping_value(status, "ready_node_count", "readyNodeCount"))
        target = _int_or_none(_first_mapping_value(status, "target_node_count", "targetNodeCount"))
        outdated = _int_or_none(
            _first_mapping_value(status, "outdated_node_count", "outdatedNodeCount")
        )
        reconciling = bool(status.get("reconciling", False))
        status_state = str(status.get("state", "") or "").strip()
        ready_text = f"ready={ready}/{target}" if ready is not None and target is not None else ""
        event_code = ""
        for event in reversed(_sequence_of_mappings(status.get("events"))):
            occurrence = _mapping(event.get("last_occurrence", event.get("lastOccurrence")))
            event_code = str(occurrence.get("code", "") or event.get("code", "") or "").strip()
            if event_code:
                break
        parts = [item for item in (status_state, ready_text) if item]
        if event_code:
            parts.append(f"event={event_code}")
        if outdated is not None:
            parts.append(f"outdated={outdated}")
        if reconciling:
            parts.append("reconciling")
        ready_rollout, _readiness_summary = _node_group_readiness_summary(node_group)
        if not ready_rollout:
            rollout_active = True
        details.append(f"{label}:" + (",".join(parts) if parts else "status present"))
    return tuple(details), rollout_active


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
    nebius_api: SoperatorMigrationNebiusApi,
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
    rollout_details, rollout_active = _active_external_node_template_rollout_statuses(
        nebius_api=nebius_api,
        checkpoint=checkpoint,
        phase_id=phase_id,
    )
    if rollout_details:
        group_parts.append(
            "updating "
            + _format_problem_node_details(
                rollout_details,
                max_items=_STATUS_MAX_NODE_GROUP_DETAILS,
            )
        )
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
    elif ready < total or cordoned or rollout_active:
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
    for key in _soperator_storage_keys_for_target(payload=payload, target_ref=target_ref):
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
    expected_storage_keys = _soperator_storage_keys_for_target(
        payload=payload,
        target_ref=target_ref,
    )
    aligned_summary = f"aligned SFS {len(filesystems)}/{len(expected_storage_keys)}"
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
            args=("sinfo", "-N", "-h", "-o", "%N %T"),
            check=False,
            timeout_seconds=_SLURM_FAST_PROBE_TIMEOUT_SECONDS,
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
            timeout_seconds=_SLURM_FAST_PROBE_TIMEOUT_SECONDS,
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
    return SoperatorMigrationStatusSignal(
        "Slurm Workers", state, f"{node_summary}; {queue_summary}"
    )


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


def _collect_populate_jail_status(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
) -> SoperatorMigrationStatusSignal:
    snapshot = inspect_populate_jail(
        command_runner,
        namespace=_SOPERATOR_NAMESPACE,
        target_ref=target_ref,
        kube_context=kube_context,
        timeout_seconds=30,
    )
    if snapshot.job_failed:
        state = "degraded"
        job_state = "failed"
    elif snapshot.job_complete:
        state = "serving"
        job_state = "complete"
    elif snapshot.status == "collected":
        state = "draining"
        job_state = "running"
    else:
        state = "unknown"
        job_state = snapshot.status or "unknown"
    details = [f"job={snapshot.job_name or 'unknown'} {job_state}"]
    if snapshot.job_image:
        details.append(f"image={snapshot.job_image}")
    if snapshot.active_consumer_pods:
        details.append(
            "waiting for consumers "
            + ", ".join(snapshot.active_consumer_pods[:4])
            + ("..." if len(snapshot.active_consumer_pods) > 4 else "")
        )
    if snapshot.detail and state == "unknown":
        details.append(snapshot.detail)
    return SoperatorMigrationStatusSignal("Populate-jail", state, "; ".join(details))


def _status_scope_for_phase(phase_id: str, phase_ids: Sequence[str]) -> Mapping[str, bool]:
    storage_planned = any(
        phase in phase_ids for phase in ("create-aligned-sfs", "online-bulk-data-sync")
    )
    compute_planned = "rolling-compute-migration" in phase_ids
    storage = phase_id in _STATUS_PHASES_WITH_STORAGE and storage_planned
    compute = phase_id in _STATUS_PHASES_WITH_COMPUTE and compute_planned
    mk8s_only = phase_id in _STATUS_PHASES_WITH_MK8S_ONLY
    populate_jail_refresh = phase_id == POPULATE_JAIL_REFRESH_PHASE_ID
    continuity = storage or compute
    return {
        "storage": storage,
        "mk8s": continuity or mk8s_only or populate_jail_refresh,
        "slurm": continuity or populate_jail_refresh,
        "soperator": continuity or populate_jail_refresh,
        "populate_jail": populate_jail_refresh,
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
        nebius_api: SoperatorMigrationNebiusApi,
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
        self._nebius_api = nebius_api
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
                        nebius_api=self._nebius_api,
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
            if scope.get("populate_jail"):
                signals.append(
                    _collect_populate_jail_status(
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
            f"External Soperator upgrade status [{elapsed}] phase {phase_id} "
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
    node_names: Sequence[str],
    job_policy: str,
    cancel_job_ids: Sequence[str],
    requeue_job_ids: Sequence[str],
    job_wait_timeout_seconds: int,
    job_refresh_interval_seconds: int,
    slurm_decision_recorder: Callable[[Mapping[str, Any]], None] | None = None,
    allow_missing_login_recovery: bool = False,
    interactive_prompt_pause: Callable[[], Any] | None = None,
    allow_resolved_interactive_job_policy: bool = False,
) -> list[str]:
    try:
        job_lines = _handle_external_upgrade_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            node_names=node_names,
            policy=job_policy,
            cancel_job_ids=cancel_job_ids,
            requeue_job_ids=requeue_job_ids,
            wait_timeout_seconds=job_wait_timeout_seconds,
            refresh_interval_seconds=job_refresh_interval_seconds,
            decision_recorder=slurm_decision_recorder,
            interactive_prompt_pause=interactive_prompt_pause,
            allow_resolved_interactive_job_policy=allow_resolved_interactive_job_policy,
        )
    except RuntimeError as exc:
        detail = str(exc)
        if allow_missing_login_recovery and "login pod not found" in detail.lower():
            return [
                "Slurm quiet window check skipped for partial cutover recovery: "
                "no live SlurmCluster/login pod remains to inspect before target "
                "chart reconciliation."
            ]
        raise
    drain = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("scontrol", "update", "PartitionName=ALL", "State=DRAIN"),
        check=False,
        timeout_seconds=120,
    )
    if drain.returncode != 0:
        return [
            *job_lines,
            "Slurm partition drain command was not supported by the source release.",
        ]
    try:
        remaining = _external_upgrade_slurm_jobs(
            command_runner=command_runner,
            kube_context=kube_context,
            node_names=node_names,
        )
    except Exception:
        _resume_slurm_partitions(
            command_runner=command_runner,
            kube_context=kube_context,
        )
        raise
    if remaining:
        _resume_slurm_partitions(
            command_runner=command_runner,
            kube_context=kube_context,
        )
        raise SoperatorMigrationPhasePending(
            f"{len(remaining)} affected Slurm job(s) started or remained after "
            "Slurm partition drain. Rerun after they finish or cancel them explicitly."
        )
    return [*job_lines, "Slurm partitions set to DRAIN for compute cutover."]


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
    namespace: str = _SOPERATOR_NAMESPACE,
) -> tuple[bool, Mapping[str, Any]]:
    result = command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            namespace,
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
    namespace: str = _SOPERATOR_NAMESPACE,
) -> None:
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            namespace,
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
    namespace: str = _SOPERATOR_NAMESPACE,
) -> None:
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            namespace,
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
    namespace: str = _SOPERATOR_NAMESPACE,
) -> None:
    command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            namespace,
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
    return _non_negative_int(spec.get("replicas"), fallback=default)


def _mariadb_suspend_value(payload: Mapping[str, Any]) -> bool:
    spec = _mapping(payload.get("spec"))
    return _bool_value(spec.get("suspend"), fallback=False)


def _slurm_node_size(payload: Mapping[str, Any], *, role: str) -> int:
    spec = _mapping(payload.get("spec"))
    slurm_nodes = _mapping(spec.get("slurmNodes"))
    role_spec = _mapping(slurm_nodes.get(role))
    return _non_negative_int(role_spec.get("size"), fallback=1)


def _slurm_node_enabled(payload: Mapping[str, Any], *, role: str) -> bool:
    spec = _mapping(payload.get("spec"))
    slurm_nodes = _mapping(spec.get("slurmNodes"))
    role_spec = _mapping(slurm_nodes.get(role))
    return _bool_value(role_spec.get("enabled"), fallback=True)


def _select_slurmcluster_for_role(
    payload: Mapping[str, Any],
    *,
    role: str,
) -> Mapping[str, Any]:
    def _has_role(item: Mapping[str, Any]) -> bool:
        spec = _mapping(item.get("spec"))
        slurm_nodes = _mapping(spec.get("slurmNodes"))
        return role in slurm_nodes

    items = _sequence_of_mappings(payload.get("items"))
    if not items:
        return payload if _has_role(payload) else {}
    for item in items:
        if _has_role(item):
            return item
    return {}


def _namespace_resource_ref(payload: Mapping[str, Any], *, default: str) -> str:
    metadata = _mapping(payload.get("metadata"))
    name = str(metadata.get("name", "") or "").strip()
    if not name:
        return default
    kind = str(payload.get("kind", "") or "").strip().lower()
    if kind == "slurmcluster" or default.startswith("slurmcluster"):
        return f"slurmcluster/{name}"
    return default


def _quiesce_scale_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource: str,
    namespace: str = _SOPERATOR_NAMESPACE,
    replicas: int = 0,
    action: str = "scale",
) -> dict[str, Any]:
    exists, payload = _kubectl_get_namespace_resource(
        command_runner=command_runner,
        kube_context=kube_context,
        resource=resource,
        namespace=namespace,
    )
    original_replicas = _resource_replicas(payload)
    item = {
        "action": action,
        "namespace": namespace,
        "resource": resource,
        "exists": exists,
        "replicas": original_replicas,
        "target_replicas": replicas,
    }
    if exists and original_replicas != replicas:
        _kubectl_scale_namespace_resource(
            command_runner=command_runner,
            kube_context=kube_context,
            resource=resource,
            namespace=namespace,
            replicas=replicas,
        )
    return item


def _quiesce_scale_down_one_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource: str,
    namespace: str,
) -> dict[str, Any]:
    exists, payload = _kubectl_get_namespace_resource(
        command_runner=command_runner,
        kube_context=kube_context,
        resource=resource,
        namespace=namespace,
    )
    original_replicas = _resource_replicas(payload)
    target_replicas = max(original_replicas - 1, 1) if original_replicas > 1 else original_replicas
    item = {
        "action": "scale-down-one",
        "namespace": namespace,
        "resource": resource,
        "exists": exists,
        "replicas": original_replicas,
        "target_replicas": target_replicas,
    }
    if exists and original_replicas != target_replicas:
        _kubectl_scale_namespace_resource(
            command_runner=command_runner,
            kube_context=kube_context,
            resource=resource,
            namespace=namespace,
            replicas=target_replicas,
        )
    return item


def _quiesce_slurm_node_size_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource: str,
    role: str,
) -> dict[str, Any]:
    exists, payload = _kubectl_get_namespace_resource(
        command_runner=command_runner,
        kube_context=kube_context,
        resource=resource,
    )
    selected = _select_slurmcluster_for_role(payload, role=role) if exists else {}
    selected_exists = bool(selected)
    selected_resource = (
        _namespace_resource_ref(selected, default=resource) if selected_exists else resource
    )
    original_size = _slurm_node_size(selected, role=role) if selected_exists else None
    target_size = (
        max(original_size - 1, 1)
        if original_size is not None and original_size > 1
        else original_size
    )
    item = {
        "action": "slurm-node-size",
        "resource": selected_resource,
        "role": role,
        "exists": selected_exists,
        "size": original_size,
        "target_size": target_size,
    }
    if selected_exists and target_size != original_size:
        _kubectl_patch_namespace_resource(
            command_runner=command_runner,
            kube_context=kube_context,
            resource=selected_resource,
            patch={"spec": {"slurmNodes": {role: {"size": target_size}}}},
        )
    return item


def _quiesce_slurm_node_enabled_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource: str,
    role: str,
) -> dict[str, Any]:
    exists, payload = _kubectl_get_namespace_resource(
        command_runner=command_runner,
        kube_context=kube_context,
        resource=resource,
    )
    selected = _select_slurmcluster_for_role(payload, role=role) if exists else {}
    selected_exists = bool(selected)
    selected_resource = (
        _namespace_resource_ref(selected, default=resource) if selected_exists else resource
    )
    original_enabled = _slurm_node_enabled(selected, role=role) if selected_exists else None
    item = {
        "action": "slurm-node-enabled",
        "resource": selected_resource,
        "role": role,
        "exists": selected_exists,
        "enabled": original_enabled,
        "target_enabled": False,
    }
    if selected_exists and original_enabled is not False:
        _kubectl_patch_namespace_resource(
            command_runner=command_runner,
            kube_context=kube_context,
            resource=selected_resource,
            patch={"spec": {"slurmNodes": {role: {"enabled": False}}}},
        )
    return item


def _quiesce_mariadb_resource(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    resource: str,
    namespace: str = _SOPERATOR_NAMESPACE,
) -> dict[str, Any]:
    exists, payload = _kubectl_get_namespace_resource(
        command_runner=command_runner,
        kube_context=kube_context,
        resource=resource,
        namespace=namespace,
    )
    item = {
        "action": "mariadb-suspend",
        "namespace": namespace,
        "resource": resource,
        "exists": exists,
        "suspend": _mariadb_suspend_value(payload),
    }
    if exists:
        _kubectl_patch_namespace_resource(
            command_runner=command_runner,
            kube_context=kube_context,
            resource=resource,
            namespace=namespace,
            patch={"spec": {"suspend": True}},
        )
    return item


def _external_service_role_quiesce_resources(role: str) -> tuple[tuple[str, str, str], ...]:
    drain_blockers = (
        (
            "scale-down-one",
            _SECURITY_PROFILES_OPERATOR_WEBHOOK_RESOURCE,
            _SECURITY_PROFILES_OPERATOR_NAMESPACE,
        ),
    )
    if role == "system":
        return drain_blockers
    if role == "controller":
        return (
            ("scale", "statefulsets.apps.kruise.io/controller", _SOPERATOR_NAMESPACE),
            *drain_blockers,
        )
    if role == "login":
        return (
            ("slurm-node-size", "slurmclusters", _SOPERATOR_NAMESPACE),
            ("scale-down-one", "statefulsets.apps.kruise.io/login", _SOPERATOR_NAMESPACE),
            *drain_blockers,
        )
    if role == "accounting":
        return (
            ("slurm-node-enabled", "slurmclusters", _SOPERATOR_NAMESPACE),
            ("scale", "deployment/accounting", _SOPERATOR_NAMESPACE),
            (
                "mariadb-suspend",
                "mariadb.k8s.mariadb.com/soperator-acct-db",
                _SOPERATOR_NAMESPACE,
            ),
            ("scale", "statefulset.apps/soperator-acct-db", _SOPERATOR_NAMESPACE),
            ("delete", "pod/soperator-acct-db-0", _SOPERATOR_NAMESPACE),
            *drain_blockers,
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
    for action, resource, namespace in _external_service_role_quiesce_resources(role):
        if action == "scale":
            resources.append(
                _quiesce_scale_resource(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    resource=resource,
                    namespace=namespace,
                )
            )
        elif action == "scale-down-one":
            resources.append(
                _quiesce_scale_down_one_resource(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    resource=resource,
                    namespace=namespace,
                )
            )
        elif action == "slurm-node-size":
            resources.append(
                _quiesce_slurm_node_size_resource(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    resource=resource,
                    role=role,
                )
            )
        elif action == "slurm-node-enabled":
            resources.append(
                _quiesce_slurm_node_enabled_resource(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    resource=resource,
                    role=role,
                )
            )
        elif action == "mariadb-suspend":
            resources.append(
                _quiesce_mariadb_resource(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    resource=resource,
                    namespace=namespace,
                )
            )
        elif action == "delete":
            _kubectl_delete_namespace_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource=resource,
                namespace=namespace,
            )
            resources.append(
                {
                    "action": "delete",
                    "namespace": namespace,
                    "resource": resource,
                    "exists": True,
                }
            )
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
        namespace = str(item.get("namespace", "") or "").strip() or _SOPERATOR_NAMESPACE
        action = str(item.get("action", "") or "").strip()
        if action in {"scale", "scale-down-one"}:
            _kubectl_scale_namespace_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource=resource,
                namespace=namespace,
                replicas=_positive_int(item.get("replicas"), fallback=1),
            )
        elif action == "slurm-node-size":
            restored_role = str(item.get("role", "") or "").strip()
            if not restored_role:
                continue
            restored_size = _non_negative_int(item.get("size"), fallback=1)
            target_size = item.get("target_size")
            if (
                target_size is not None
                and _non_negative_int(target_size, fallback=restored_size) == restored_size
            ):
                continue
            _kubectl_patch_namespace_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource=resource,
                namespace=namespace,
                patch={
                    "spec": {
                        "slurmNodes": {restored_role: {"size": restored_size}}
                    }
                },
            )
        elif action == "slurm-node-enabled":
            restored_role = str(item.get("role", "") or "").strip()
            if not restored_role:
                continue
            restored_enabled = _bool_value(item.get("enabled"), fallback=True)
            target_enabled = item.get("target_enabled")
            if target_enabled is not None and _bool_value(
                target_enabled,
                fallback=restored_enabled,
            ) == restored_enabled:
                continue
            _kubectl_patch_namespace_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource=resource,
                namespace=namespace,
                patch={
                    "spec": {
                        "slurmNodes": {restored_role: {"enabled": restored_enabled}}
                    }
                },
            )
        elif action == "mariadb-suspend":
            _kubectl_patch_namespace_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource=resource,
                namespace=namespace,
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


def _ensure_child_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    child = parent.get(key)
    if not isinstance(child, dict):
        child = {}
        parent[key] = child
    return child


def _patch_target_kube_rbac_proxy_images(values: dict[str, Any]) -> None:
    image_paths = (
        ("controllerManager", "kubeRbacProxy", "image"),
        ("soperator-checks", "checks", "kubeRbacProxy", "image"),
    )
    for path in image_paths:
        node = values
        for key in path:
            node = _ensure_child_mapping(node, key)
        node["repository"] = _TARGET_KUBE_RBAC_PROXY_REPOSITORY
        node["tag"] = _TARGET_KUBE_RBAC_PROXY_TAG


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
    _preserve_live_storage_sizes(values, live_snapshot=live_snapshot)
    rolling_state = _mapping(
        _mapping(checkpoint.get("phase_state")).get("rolling-compute-migration")
    )
    values["k8sNodeFilters"] = _target_k8s_node_filters()
    _patch_target_operator_affinity(values)
    _patch_target_kube_rbac_proxy_images(values)
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
    _patch_target_slurm_runtime(values)
    ensure_soperator_gpu_driver_jail_values(
        values,
        context=f"External Soperator upgrade target {target_ref}",
    )
    return values


def _source_worker_nodeset_values(
    source_report: Mapping[str, Any],
    *,
    live_snapshot: Mapping[str, Any] | None = None,
    topology_by_nodeset: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    source_snapshot, _report = _source_report_payload(source_report)
    source_groups_by_nodeset = _source_worker_node_groups_by_nodeset(source_report)
    sole_source_group = (
        next(iter(source_groups_by_nodeset.values()))
        if len(source_groups_by_nodeset) == 1
        else None
    )
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
            source_group=source_groups_by_nodeset.get(name) or sole_source_group,
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
    static = str(_mapping(nodeset.get("nodeConfig")).get("static", "") or "")
    match = re.search(r"(?:^|\s)Gres=gpu:(?P<count>[0-9]+)(?:\s|$)", static, flags=re.IGNORECASE)
    if match:
        return _positive_int(match.group("count"), fallback=0) or None
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
        "slurm-configs-jail",
        "slurm-scripts",
        "slurm-scripts-jail",
    }
)
_TARGET_NODESET_RESERVED_CUSTOM_INIT_CONTAINER_NAMES = frozenset(
    {
        "cxcli-slurm-config-jail",
        SOPERATOR_GPU_DRIVER_JAIL_INIT_CONTAINER_NAME,
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
    tokens: list[str] = []
    normalized_cpu_count = topology_cpu_count or _positive_int(cpu_count, fallback=0)
    if normalized_cpu_count:
        tokens.append(f"CPUs={normalized_cpu_count}")
    tokens.extend(_worker_topology_static_tokens(topology))
    tokens.append(f"Gres=gpu:{gpu_count}")
    tokens.extend(_static_without_normalized_keys(existing_static))
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
        node_config["static"] = _gpu_worker_static(
            cpu_count or 0,
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


def _strip_reserved_nodeset_custom_init_containers(nodeset: dict[str, Any]) -> None:
    containers = nodeset.get("customInitContainers")
    if not isinstance(containers, Sequence) or isinstance(containers, (str, bytes, bytearray)):
        return
    kept: list[Any] = []
    changed = False
    for container in containers:
        name = str(_mapping(container).get("name", "") or "").strip()
        if name in _TARGET_NODESET_RESERVED_CUSTOM_INIT_CONTAINER_NAMES:
            changed = True
            continue
        kept.append(container)
    if not changed:
        return
    if kept:
        nodeset["customInitContainers"] = kept
    else:
        nodeset.pop("customInitContainers", None)


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


def _parse_slurmd_c_topology(stdout: str) -> dict[str, Any]:
    node_line = next(
        (line.strip() for line in stdout.splitlines() if line.strip().startswith("NodeName=")),
        "",
    )
    if not node_line:
        return {}
    fields: dict[str, str] = {}
    for token in node_line.split():
        key, separator, value = token.partition("=")
        if separator:
            fields[key.lower()] = value
    topology = {
        "cpus": _positive_int(fields.get("cpus"), fallback=0),
        "boards": _positive_int(fields.get("boards"), fallback=1),
        "sockets": _positive_int(fields.get("socketsperboard"), fallback=0),
        "cores_per_socket": _positive_int(fields.get("corespersocket"), fallback=0),
        "threads_per_core": _positive_int(fields.get("threadspercore"), fallback=0),
    }
    if not all(
        _positive_int(topology.get(key), fallback=0)
        for key in ("cpus", "sockets", "cores_per_socket", "threads_per_core")
    ):
        return {}
    parameters = str(fields.get("parameters", "") or "").strip()
    if parameters:
        topology["parameters"] = parameters
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
    slurmd_result = command_runner(
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
            "slurmd",
            "-C",
            f"--parameters={_TARGET_GPU_GRES_AFFINITY_PARAMETER}",
        ],
        timeout_seconds=120,
        check=False,
    )
    if slurmd_result.returncode == 0:
        topology = _parse_slurmd_c_topology(slurmd_result.stdout)
        if topology:
            topology["source_pod"] = pod_name
            return topology
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
    _strip_reserved_nodeset_custom_init_containers(nodeset)
    _strip_reserved_nodeset_custom_volume_mounts(nodeset)
    normalize_soperator_gpu_driver_jail_mounts(
        nodeset,
        context=f"source Soperator NodeSet {nodeset.get('name') or 'worker'}",
    )
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
        if str(_mapping(resource.get("status")).get("phase", "") or "").strip().lower() == "ready"
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
    if _target_values_have_gpu_gres_nodeset(values) and not _slurm_config_has_key(
        lines,
        "SlurmdParameters",
    ):
        lines.append(f"SlurmdParameters={_TARGET_GPU_GRES_AFFINITY_PARAMETER}")
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


def _slurm_config_has_key(lines: Sequence[str], key: str) -> bool:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=", flags=re.IGNORECASE)
    return any(pattern.match(line) for line in lines)


def _target_values_have_gpu_gres_nodeset(values: Mapping[str, Any]) -> bool:
    nodesets = values.get("nodesets")
    if not isinstance(nodesets, Sequence) or isinstance(nodesets, (str, bytes, bytearray)):
        return False
    return any(
        isinstance(nodeset, Mapping) and bool(_nodeset_gpu_count(nodeset, None))
        for nodeset in nodesets
    )


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
            _mapping(_mapping(spec.get("resources")).get("requests")).get("storage", "") or ""
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
        "--force-conflicts",
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
                if not pending_operation_cleared and _clear_pending_helm_release_operation(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    release_name=release_name,
                    namespace=namespace,
                ):
                    pending_operation_cleared = True
                    continue
                raise RuntimeError(
                    f"Helm upgrade for target GPU stack chart {namespace}/{release_name} "
                    f"timed out after {exc.timeout} seconds and the live release is not ready yet. "
                    "Rerun the same `nebius-cxcli ext-soperator upgrade ... --execute --approve` "
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
        effective_values = dict(copy.deepcopy(to_plain_data(values)))
        _patch_target_kube_rbac_proxy_images(effective_values)
        values_text = json.dumps(effective_values, sort_keys=True)
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
        status.get("readyReplicas", status.get("replicas")),
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
                for group in (normalize_component_token(item) for item in worker_node_groups)
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
            raise RuntimeError(
                f"{_command_text(result.args)} returned invalid JSON: {exc}"
            ) from exc
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
    } or any(normalized.startswith(prefix) for prefix in _soperator_source_release_selectors()[2])


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
            "External Soperator upgrade checkpoint rolling-compute-migration."
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
            "External Soperator upgrade checkpoint rolling-compute-migration."
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
            "External Soperator upgrade checkpoint rolling-compute-migration."
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
            "takeover: " + ", ".join(changed) + ".",
        ]
    if known:
        return webhook_changed, [
            *webhook_lines,
            "Old source Soperator controller deployments already scaled down: "
            + ", ".join(sorted(dict.fromkeys(known)))
            + ".",
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


def _target_worker_pod_instance_ids(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> dict[str, str]:
    payload = _json_from_command(
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
        check=False,
    )
    items = payload.get("items", [])
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return {}
    result: dict[str, str] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        metadata = _mapping(item.get("metadata"))
        labels = _mapping(metadata.get("labels"))
        name = str(metadata.get("name", "") or "").strip()
        phase = str(_mapping(item.get("status")).get("phase", "") or "").strip()
        instance_id = str(_mapping(item.get("spec")).get("nodeName", "") or "").strip()
        nodeset_name = str(labels.get("slurm.nebius.ai/nodeset", "") or "")
        worker_label = str(labels.get("slurm.nebius.ai/worker", "") or "").lower()
        is_worker = (
            worker_label == "true"
            or nodeset_name.startswith(_SOURCE_WORKER_NODESET_PREFIX)
            or name.startswith(f"{_SOURCE_WORKER_NODESET_PREFIX}-")
        )
        if name and instance_id and phase == "Running" and is_worker:
            result[name] = instance_id
    return result


def _reconcile_slurm_worker_runtime_identity(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
) -> list[str]:
    worker_instances = _target_worker_pod_instance_ids(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    if not worker_instances:
        return []
    service_suffix = f"{target_ref}-nodeset-svc.{_SOPERATOR_NAMESPACE}.svc.cluster.local"
    script_lines = [
        "set -euo pipefail",
        f"service_suffix={shlex.quote(service_suffix)}",
        "changed=0",
    ]
    for node_name, instance_id in sorted(worker_instances.items()):
        script_lines.extend(
            [
                f"node={shlex.quote(node_name)}",
                f"expected_instance={shlex.quote(instance_id)}",
                'expected_addr="${node}.${service_suffix}"',
                "node_state=\"$(scontrol show node \"${node}\" | tr '\\n' ' ' || true)\"",
                (
                    'current_addr="$(printf "%s" "${node_state}" '
                    "| sed -n 's/.* NodeAddr=\\([^ ]*\\).*/\\1/p')\""
                ),
                (
                    'current_instance="$(printf "%s" "${node_state}" '
                    "| sed -n 's/.* InstanceId=\\([^ ]*\\).*/\\1/p')\""
                ),
                'if [[ -n "${current_addr}" && "${current_addr}" != "${expected_addr}" ]]; then',
                '  scontrol update NodeName="${node}" NodeAddr="${expected_addr}"',
                '  printf "%s NodeAddr %s -> %s\\n" "${node}" "${current_addr}" "${expected_addr}"',
                "  changed=1",
                "fi",
                (
                    'if [[ -n "${current_instance}" '
                    '&& "${current_instance}" != "${expected_instance}" ]]; then'
                ),
                '  scontrol update NodeName="${node}" InstanceId="${expected_instance}"',
                (
                    '  printf "%s InstanceId %s -> %s\\n" '
                    '"${node}" "${current_instance}" "${expected_instance}"'
                ),
                "  changed=1",
                "fi",
            ]
        )
    script_lines.extend(
        [
            'if (( changed == 0 )); then printf "Slurm worker runtime identity already aligned.\\n"; fi',
        ]
    )
    result = command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "exec",
            _SOPERATOR_CONTROLLER_POD,
            "-c",
            _SOPERATOR_CONTROLLER_CONTAINER,
            "--",
            "bash",
            "-lc",
            "\n".join(script_lines),
        ],
        check=False,
        timeout_seconds=180,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Could not reconcile Soperator Slurm worker runtime identity after "
            f"target cutover: {_command_detail(result)}"
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _resume_slurm_after_cutover(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
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
    identity_lines = _reconcile_slurm_worker_runtime_identity(
        command_runner=command_runner,
        kube_context=kube_context,
        target_ref=target_ref,
    )
    if identity_lines:
        lines.append(
            "Slurm worker runtime identity reconciled after target Soperator cutover: "
            + "; ".join(identity_lines)
        )
    return lines


def _scale_node_group(
    *,
    nebius_api: SoperatorMigrationNebiusApi,
    node_group_id: str,
    count: int,
) -> None:
    nebius_api.scale_node_group(node_group_id=node_group_id, count=count, timeout_seconds=3000)


def _delete_node_group(
    *,
    nebius_api: SoperatorMigrationNebiusApi,
    node_group_id: str,
) -> None:
    nebius_api.delete_node_group(node_group_id=node_group_id, timeout_seconds=3000)


def _command_not_found(result: SoperatorMigrationCommandResult) -> bool:
    detail = " ".join(
        part.strip() for part in (result.stderr, result.stdout) if part and part.strip()
    )
    normalized = re.sub(r"\s+", " ", detail).strip().lower()
    if normalized in {"not found", "notfound", "resource not found"}:
        return True
    if "statuscode.not_found" in normalized:
        return True
    if "error from server (notfound)" in normalized:
        return True
    return bool(
        re.search(r"\brequest error\s+not[_ -]?found\b", normalized)
        or re.search(r"\brpc error:\s*code\s*=\s*not[_ -]?found\b", normalized)
    )


def _ensure_live_nodes_ready(snapshot: Mapping[str, Any]) -> None:
    groups = _mapping(snapshot.get("node_groups"))
    if not groups:
        raise RuntimeError("External Soperator upgrade validation found no Kubernetes node groups.")
    empty = [
        str(name)
        for name, group in groups.items()
        if isinstance(group, Mapping) and int(group.get("node_count", 0) or 0) <= 0
    ]
    if empty:
        raise RuntimeError(
            "External Soperator upgrade validation found empty node groups: " + ", ".join(empty)
        )


def _execute_external_node_template_upgrade_phase(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    worker_node_groups: Sequence[str],
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
    checkpoint_writer: Callable[[], None] | None = None,
    rollout: SoperatorExternalNodeTemplateRollout,
    job_policy: str,
    cancel_job_ids: Sequence[str],
    requeue_job_ids: Sequence[str],
    job_wait_timeout_seconds: int,
    job_refresh_interval_seconds: int,
    login_session_policy: str = EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY,
    login_session_drain_timeout_seconds: int = EXTERNAL_LOGIN_SESSION_DRAIN_TIMEOUT_SECONDS,
    slurm_decision_recorder: Callable[[Mapping[str, Any]], None] | None = None,
    interactive_prompt_pause: Callable[[], Any] | None = None,
    allow_resolved_interactive_job_policy: bool = False,
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
        nebius_api=nebius_api,
    )
    phase["cluster_id"] = cluster_id
    mutation_performed = False
    lines: list[str] = []

    control_plane = phase.setdefault("control_plane", {})
    if not isinstance(control_plane, dict):
        raise RuntimeError(
            "External Soperator upgrade checkpoint external-node-template-upgrade.control_plane "
            "must be a mapping."
        )
    cluster = _cluster_payload_by_id(nebius_api=nebius_api, cluster_id=cluster_id)
    current_version = _minor_version_text_or_empty(_cluster_control_plane_version(cluster))
    if not current_version:
        raise SoperatorMigrationPhasePending(
            "external-node-template-upgrade could not detect the live MK8s control-plane "
            "version. Rerun onboarding after confirming Nebius API access works."
        )
    control_plane["current_version"] = current_version
    control_plane["target_version"] = target.k8s_version
    _ensure_external_node_template_k8s_not_downgrade(current_version, target.k8s_version)
    try:
        hops = require_single_minor_hop(current_version, target.k8s_version)
    except ValueError as exc:
        raise RuntimeError(str(exc).replace("--to-version", "--to-k8s-version")) from exc
    hop_state = control_plane.setdefault("hops", {})
    if not isinstance(hop_state, dict):
        raise RuntimeError(
            "External Soperator upgrade checkpoint external-node-template-upgrade.control_plane.hops "
            "must be a mapping."
        )
    if not hops:
        control_plane["status"] = "already-current"
        lines.append(f"External MK8s control plane already at Kubernetes {target.k8s_version}.")
    for hop in hops:
        state = hop_state.setdefault(hop.to_version, {})
        if not isinstance(state, dict):
            raise RuntimeError(
                "External Soperator upgrade checkpoint external-node-template-upgrade "
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
                nebius_api=nebius_api,
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
            "External Soperator upgrade checkpoint external-node-template-upgrade.node_groups "
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
                "External Soperator upgrade checkpoint external-node-template-upgrade "
                f"node group {group_name} must be a mapping."
            )
        previous_status = str(group_state.get("status", "") or "")
        node_group_id = _source_group_node_group_id(raw_group)
        if not node_group_id:
            raise SoperatorMigrationPhasePending(
                "external-node-template-upgrade requires a Nebius node group id for "
                f"source group '{group_name}'. Rerun `nebius-cxcli ext-soperator onboard` "
                "against a Nebius MK8s target."
            )
        node_group = _node_group_payload_by_id(
            nebius_api=nebius_api,
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
            group_state["strategy_restore_required"] = tuple(original_strategy_args) != tuple(
                str(item) for item in strategy_args
            )
        elif isinstance(stored_original_strategy_args, Sequence) and not isinstance(
            stored_original_strategy_args,
            str,
        ):
            original_strategy_args = tuple(str(item) for item in stored_original_strategy_args)
            group_state.setdefault(
                "strategy_restore_required",
                tuple(original_strategy_args) != tuple(str(item) for item in strategy_args),
            )
        else:
            raise RuntimeError(
                "External Soperator upgrade checkpoint external-node-template-upgrade "
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
                "rollout": rollout.to_manifest_dict()
                if worker_group
                else {
                    "strategy": strategy_label,
                    "service_role_group_strategy": {
                        "max_surge_count": rollout.service_role_max_surge_count,
                        "max_unavailable_count": rollout.service_role_max_unavailable_count,
                        "drain_timeout": rollout.service_role_drain_timeout,
                    },
                },
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
                nebius_api.update_node_group(
                    node_group_id=node_group_id,
                    original_node_group=current_node_group,
                    update_args=(),
                    strategy_args=original_strategy_args,
                    clear_template_gpu_settings=False,
                    timeout_seconds=timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                _reconcile_node_group_update_timeout(
                    nebius_api,
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
            group_state["strategy_restored"] = strategy_restored
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
            "previous_status": previous_status,
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
                "External Soperator upgrade checkpoint external-node-template-upgrade "
                f"node group {group_name} must be a mapping."
            )
        work_lines: list[str] = []
        service_role = _source_group_service_quiesce_role(group_name, raw_group)
        service_quiesce_state: dict[str, Any] | None = None
        if (
            allow_service_quiesce
            and service_role
            and _zero_surge_service_quiesce_required(service_role, raw_group)
        ):
            raw_quiesce_state = group_state.setdefault("service_quiesce", {})
            if not isinstance(raw_quiesce_state, dict):
                raise RuntimeError(
                    "External Soperator upgrade checkpoint external-node-template-upgrade "
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
        if service_role == "login":
            login_pods_before_update = _login_pod_names(
                command_runner=command_runner,
                kube_context=kube_context,
            )
            group_state["login_pods_before_node_update"] = list(login_pods_before_update)
            group_state["login_service_ready_before_node_update"] = (
                wait_for_login_service_ready_endpoints(
                    command_runner,
                    namespace=_SOPERATOR_NAMESPACE,
                    target_ref=target_ref,
                    kube_context=kube_context,
                    timeout_seconds=300,
                    poll_interval_seconds=5,
                )
            )
            work_lines.append(
                "External login node-template guard: login Service has ready endpoints "
                "before node-group update."
            )
            work_lines.extend(
                _wait_for_login_session_policy(
                    phase=phase,
                    command_runner=command_runner,
                    kube_context=kube_context,
                    policy=login_session_policy,
                    timeout_seconds=login_session_drain_timeout_seconds,
                    pod_names=login_pods_before_update,
                )
            )
            if write_progress and checkpoint_writer is not None:
                checkpoint_writer()
        try:
            _update_node_group_with_temporary_strategy(
                nebius_api=nebius_api,
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
        if service_role == "login":
            group_state["login_service_ready_after_node_update"] = (
                wait_for_login_service_ready_endpoints(
                    command_runner,
                    namespace=_SOPERATOR_NAMESPACE,
                    target_ref=target_ref,
                    kube_context=kube_context,
                    timeout_seconds=300,
                    poll_interval_seconds=5,
                )
            )
            work_lines.append(
                "External login node-template guard: login Service has ready endpoints "
                "after node-group update."
            )
            if write_progress and checkpoint_writer is not None:
                checkpoint_writer()
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
            allow_service_quiesce=work.get("strategy_label") == "zero-surge",
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
    phase["worker_waves"] = [[str(work["group_name"]) for work in wave] for wave in worker_waves]
    for wave_index, wave in enumerate(worker_waves, start=1):
        phase["active_worker_wave"] = wave_index
        fresh_wave = any(
            str(work.get("previous_status", "") or "") not in {"updating", "waiting-rollout"}
            for work in wave
        )
        if fresh_wave:
            wave_worker_groups = tuple(str(work["group_name"]) for work in wave)
            lines.extend(
                _run_soperator_worker_rollout_live_preflight(
                    source_report=source_report,
                    worker_node_groups=wave_worker_groups,
                    command_runner=command_runner,
                    kube_context=kube_context,
                    rollout=rollout,
                    job_policy=job_policy,
                    cancel_job_ids=cancel_job_ids,
                    requeue_job_ids=requeue_job_ids,
                    job_wait_timeout_seconds=job_wait_timeout_seconds,
                    job_refresh_interval_seconds=job_refresh_interval_seconds,
                    slurm_decision_recorder=slurm_decision_recorder,
                    interactive_prompt_pause=interactive_prompt_pause,
                    allow_resolved_interactive_job_policy=allow_resolved_interactive_job_policy,
                )
            )
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
        if rollout.strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE and len(wave) > 1:
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
    service_strategy = (
        "safe-surge"
        if rollout.service_role_strategy == SOPERATOR_WORKER_ROLLOUT_STRATEGY_SAFE_SURGE
        else "lower-continuity zero-surge"
    )
    lines.append(
        "External node-template strategy: service-role groups use "
        f"{service_strategy} ({_effective_service_role_strategy_label(rollout)}); "
        "worker groups use "
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
    checkpoint_writer: Callable[[], None] | None = None,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, _TARGET_GPU_STACK_PHASE_ID)
    rows = _target_gpu_stack_app_rows(payload, target_ref)
    if not rows:
        phase["missing_app_rows"] = list(_TARGET_GPU_STACK_APP_ORDER)
        raise SoperatorMigrationPhasePending(
            "target GPU stack reconciliation requires target-scoped GPU Operator or "
            "Network Operator app rows. Rerun `nebius-cxcli ext-soperator onboard` so "
            "the accepted config carries the reconciliation app rows before executing the upgrade."
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
        phase["charts"] = [dict(item) for item in applied]
        if checkpoint_writer is not None:
            checkpoint_writer()
        version = f"@{result['version']}" if result.get("version") else ""
        lines.append(
            "Applied target GPU stack chart: "
            f"{result['id']}={result['release_name']} "
            f"({result['namespace']}, {result['chart_ref']}{version})"
        )
        if result.get("timeout_recovered") == "true":
            timeout_suffix = (
                f" after {result['timeout_seconds']}s" if result.get("timeout_seconds") else ""
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
    if checkpoint_writer is not None:
        checkpoint_writer()
    return True, lines


def _execute_create_aligned_sfs_phase(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    target_ref: str,
    worker_node_groups: Sequence[str],
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[bool, list[str]]:
    project_id = _nebius_project_id(payload)
    specs = _aligned_filesystem_specs(payload=payload, target_ref=target_ref)
    specs_by_key = {spec.key: spec for spec in specs}
    phase = _phase_state(checkpoint, "create-aligned-sfs")
    filesystems = phase.setdefault("filesystems", {})
    if not isinstance(filesystems, dict):
        raise RuntimeError(
            "External Soperator upgrade checkpoint create-aligned-sfs.filesystems must be a mapping."
        )
    mutation_performed = False
    lines: list[str] = []
    filesystem_ids_by_key: dict[str, str] = {}
    for spec in specs:
        existing = _get_filesystem_by_name(
            nebius_api=nebius_api,
            project_id=project_id,
            name=spec.name,
        )
        created = False
        filesystem = existing
        if not _filesystem_id(filesystem):
            filesystem = _create_filesystem(
                nebius_api=nebius_api,
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
        nebius_api=nebius_api,
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
            "External Soperator upgrade checkpoint online-bulk-data-sync.jobs must be a mapping."
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
    for key in _soperator_storage_keys_for_target(payload=payload, target_ref=target_ref):
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
        source_path, target_path = _copy_job_paths_for_storage_key(
            payload=payload,
            target_ref=target_ref,
            key=key,
        )
        manifests.append(
            _copy_job_manifest(
                key=key,
                source_pvc=source_pvc,
                target_pvc=target_pvc,
                source_path=source_path,
                target_path=target_path,
            )
        )
        jobs[key] = {
            "source_pvc": source_pvc,
            "target_pvc": target_pvc,
            "source_path": source_path,
            "target_path": target_path,
        }
    if missing_pvcs:
        phase["missing_pvcs"] = missing_pvcs
        raise SoperatorMigrationPhasePending(
            "online-bulk-data-sync requires existing source and target PVCs before copy Jobs run. "
            "Missing PVCs: " + ", ".join(missing_pvcs) + "."
        )
    if not manifests:
        phase["skipped_reason"] = "no source PVC to target PVC copy pairs were detected"
        return False, lines or ["Data sync skipped: no PVC copy pairs were detected."]
    for manifest in manifests:
        name = str(_mapping(manifest.get("metadata")).get("name", "") or "").strip()
        if name:
            _delete_failed_job_before_reapply(
                command_runner=command_runner,
                kube_context=kube_context,
                name=name,
            )
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
        _wait_for_job_complete_or_failed(
            command_runner=command_runner,
            kube_context=kube_context,
            name=name,
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
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
    checkpoint_writer: Callable[[], None] | None = None,
    job_policy: str | None = None,
    populate_jail_refresh: str = "auto",
    cancel_job_ids: Sequence[str] = (),
    requeue_job_ids: Sequence[str] = (),
    job_wait_timeout_seconds: int = _EXTERNAL_UPGRADE_DEFAULT_JOB_WAIT_TIMEOUT_SECONDS,
    job_refresh_interval_seconds: int = _EXTERNAL_UPGRADE_DEFAULT_JOB_REFRESH_INTERVAL_SECONDS,
    login_session_policy: str = EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY,
    login_session_drain_timeout_seconds: int = EXTERNAL_LOGIN_SESSION_DRAIN_TIMEOUT_SECONDS,
    slurm_decision_recorder: Callable[[Mapping[str, Any]], None] | None = None,
    interactive_prompt_pause: Callable[[], Any] | None = None,
    allow_resolved_interactive_job_policy: bool = False,
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
        nebius_api=nebius_api,
        command_runner=command_runner,
    )
    if checkpoint_writer is not None:
        checkpoint_writer()
    live_source_slurmcluster_present = _live_source_slurmcluster_present(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        target_ref=target_ref,
    )
    phase["live_source_slurmcluster_present"] = live_source_slurmcluster_present
    live_worker_slurm_nodes = _external_upgrade_worker_nodeset_slurm_nodes(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    quiet_nodes = live_worker_slurm_nodes or nodes
    phase["slurm_job_scope_nodes"] = list(quiet_nodes)
    phase["slurm_job_scope"] = (
        "live-worker-nodesets" if live_worker_slurm_nodes else "source-worker-node-groups"
    )
    quiet_lines = _ensure_slurm_quiet(
        command_runner=command_runner,
        kube_context=kube_context,
        node_names=quiet_nodes,
        job_policy=job_policy,
        cancel_job_ids=cancel_job_ids,
        requeue_job_ids=requeue_job_ids,
        job_wait_timeout_seconds=job_wait_timeout_seconds,
        job_refresh_interval_seconds=job_refresh_interval_seconds,
        slurm_decision_recorder=slurm_decision_recorder,
        allow_missing_login_recovery=not live_source_slurmcluster_present,
        interactive_prompt_pause=interactive_prompt_pause,
        allow_resolved_interactive_job_policy=allow_resolved_interactive_job_policy,
    )
    try:
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
        source_login_pod_names = _login_pod_names(
            command_runner=command_runner,
            kube_context=kube_context,
        )
        service_identity_before = _login_service_identities(
            command_runner=command_runner,
            kube_context=kube_context,
            required=True,
        )
        service_identity_before, allocation_decisions, allocation_lines = (
            stabilize_soperator_login_load_balancer_allocations(
                command_runner=command_runner,
                kube_context=kube_context,
                project_id=_nebius_project_id(payload),
                nebius_api=nebius_api,
                values=values,
                service_identities=service_identity_before,
            )
        )
        lines.extend(allocation_lines)
        _assert_login_service_stable_load_balancer_preconditions(service_identity_before)
        phase["login_continuity"] = {
            **dict(_mapping(phase.get("login_continuity"))),
            "service_identity_before_handoff": [
                dict(to_plain_data(item)) for item in service_identity_before
            ],
            "login_load_balancer_allocation": [
                decision.as_payload() for decision in allocation_decisions
            ],
            "source_login_pods_before_handoff": list(source_login_pod_names),
            "session_policy": login_session_policy,
            "session_drain_timeout_seconds": login_session_drain_timeout_seconds,
        }
        if checkpoint_writer is not None:
            checkpoint_writer()
        _helm_upgrade_target_soperator(
            command_runner=command_runner,
            kube_context=kube_context,
            values=values,
            expected_version=str(
                _mapping(source_report.get("report")).get("target_version", "") or ""
            ),
            wait=False,
        )
        lines.extend(
            _ensure_rolling_login_continuity(
                phase=phase,
                command_runner=command_runner,
                kube_context=kube_context,
                target_ref=target_ref,
                service_identity_before=service_identity_before,
                source_login_pod_names=source_login_pod_names,
                login_session_policy=login_session_policy,
                login_session_drain_timeout_seconds=login_session_drain_timeout_seconds,
            )
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
            target_version=str(
                _mapping(source_report.get("report")).get("target_version", "") or ""
            ),
            profile_group=_source_report_migration_profile_group(source_report),
        )
        mutation_performed = mutation_performed or scaled_source_controller
        lines.extend(scale_lines)
        if checkpoint_writer is not None:
            checkpoint_writer()
        _delete_conflicting_source_slurm_resources(
            command_runner=command_runner,
            kube_context=kube_context,
            source_report=source_report,
            target_ref=target_ref,
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
        target_ref=target_ref,
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
    nebius_api: SoperatorMigrationNebiusApi,
    login_session_policy: str = EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY,
    login_session_drain_timeout_seconds: int = EXTERNAL_LOGIN_SESSION_DRAIN_TIMEOUT_SECONDS,
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
    source_login_pod_names = _login_pod_names(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    service_identity_before = _login_service_identities(
        command_runner=command_runner,
        kube_context=kube_context,
        required=True,
    )
    service_identity_before, allocation_decisions, allocation_lines = (
        stabilize_soperator_login_load_balancer_allocations(
            command_runner=command_runner,
            kube_context=kube_context,
            project_id=_nebius_project_id(payload),
            nebius_api=nebius_api,
            values=values,
            service_identities=service_identity_before,
        )
    )
    lines.extend(allocation_lines)
    _assert_login_service_stable_load_balancer_preconditions(service_identity_before)
    phase["login_continuity"] = {
        **dict(_mapping(phase.get("login_continuity"))),
        "service_identity_before_handoff": [
            dict(to_plain_data(item)) for item in service_identity_before
        ],
        "login_load_balancer_allocation": [
            decision.as_payload() for decision in allocation_decisions
        ],
        "source_login_pods_before_handoff": list(source_login_pod_names),
        "session_policy": login_session_policy,
        "session_drain_timeout_seconds": login_session_drain_timeout_seconds,
    }
    _helm_upgrade_target_soperator(
        command_runner=command_runner,
        kube_context=kube_context,
        values=values,
        expected_version=str(_mapping(source_report.get("report")).get("target_version", "") or ""),
        wait=False,
    )
    lines.extend(
        _ensure_rolling_login_continuity(
            phase=phase,
            command_runner=command_runner,
            kube_context=kube_context,
            target_ref=target_ref,
            service_identity_before=service_identity_before,
            source_login_pod_names=source_login_pod_names,
            login_session_policy=login_session_policy,
            login_session_drain_timeout_seconds=login_session_drain_timeout_seconds,
        )
    )
    lines.extend(
        _suspend_legacy_flux_helmreleases(
            command_runner=command_runner,
            kube_context=kube_context,
            phase=phase,
        )
    )
    _scaled_source_controller, scale_lines = _scale_down_legacy_soperator_controllers(
        command_runner=command_runner,
        kube_context=kube_context,
        phase=phase,
        target_version=str(_mapping(source_report.get("report")).get("target_version", "") or ""),
        profile_group=_source_report_migration_profile_group(source_report),
    )
    lines.extend(scale_lines)
    _delete_conflicting_source_slurm_resources(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        target_ref=target_ref,
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
            target_ref=target_ref,
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
    nebius_api: SoperatorMigrationNebiusApi,
    login_session_policy: str = EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY,
    login_session_drain_timeout_seconds: int = EXTERNAL_LOGIN_SESSION_DRAIN_TIMEOUT_SECONDS,
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
            nebius_api=nebius_api,
            login_session_policy=login_session_policy,
            login_session_drain_timeout_seconds=login_session_drain_timeout_seconds,
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
    _delete_failed_job_before_reapply(
        command_runner=command_runner,
        kube_context=kube_context,
        name=job_name,
    )
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
    _wait_for_job_complete_or_failed(
        command_runner=command_runner,
        kube_context=kube_context,
        name=job_name,
        timeout_seconds=720,
        job_label="Soperator controller-spool cleanup Job",
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
            "Final cutover skipped: no Slurm custom resources were detected; Soperator manager deployment is healthy."
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


def _home_mount_probe_check(name: str, result: SoperatorMigrationCommandResult) -> Mapping[str, str]:
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        return _fast_verification_check(name, "failed", detail)
    line = next((item.strip() for item in (result.stdout or "").splitlines() if item.strip()), "")
    parts = line.split(maxsplit=2)
    target = parts[0] if parts else ""
    source = parts[1] if len(parts) > 1 else "unknown"
    fstype = parts[2] if len(parts) > 2 else "unknown"
    normalized_target = "/" + target.strip().strip("/")
    if normalized_target == "//":
        normalized_target = "/"
    if normalized_target == "/home" or normalized_target.endswith("/home"):
        return _fast_verification_check(
            name,
            "passed",
            f"{normalized_target} mounted from {source} ({fstype})",
        )
    return _fast_verification_check(
        name,
        "failed",
        f"/home resolves through mount target {normalized_target or 'unknown'} "
        f"from {source} ({fstype})",
    )


def _login_home_mount_probe_check(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> Mapping[str, str]:
    result = _kubectl_exec_login(
        command_runner=command_runner,
        kube_context=kube_context,
        args=("/bin/sh", "-ceu", _HOME_MOUNT_PROBE_SCRIPT),
        check=False,
        timeout_seconds=120,
    )
    return _home_mount_probe_check("/home login pod mount", result)


def _running_worker_pod_for_home_probe(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> str:
    payload = _json_from_command(
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
        timeout_seconds=60,
        check=False,
    )
    names: list[str] = []
    for item in _sequence_of_mappings(_mapping(payload).get("items")):
        if str(_mapping(item.get("status")).get("phase", "") or "") != "Running":
            continue
        metadata = _mapping(item.get("metadata"))
        name = str(metadata.get("name", "") or "").strip()
        labels = _mapping(metadata.get("labels"))
        label_values = tuple(str(value or "").strip().lower() for value in labels.values())
        nodeset_name = ""
        for label_key in _SOPERATOR_NODESET_LABEL_KEYS:
            nodeset_name = str(labels.get(label_key, "") or "").strip()
            if nodeset_name:
                break
        is_worker = (
            str(labels.get("slurm.nebius.ai/worker", "") or "").strip().lower() == "true"
            or normalize_component_token(nodeset_name).startswith(_SOURCE_WORKER_NODESET_PREFIX)
            or "worker" in label_values
            or name.startswith(f"{_SOURCE_WORKER_NODESET_PREFIX}-")
        )
        if name and is_worker:
            names.append(name)
    return sorted(dict.fromkeys(names))[0] if names else ""


def _worker_home_mount_probe_check(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
) -> Mapping[str, str]:
    pod_name = _running_worker_pod_for_home_probe(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    if not pod_name:
        return _fast_verification_check(
            "/home worker pod mount",
            "failed",
            "worker pod not found",
        )
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
            "/bin/sh",
            "-ceu",
            _HOME_MOUNT_PROBE_SCRIPT,
        ],
        check=False,
        timeout_seconds=120,
    )
    return _home_mount_probe_check("/home worker pod mount", result)


def _live_home_mount_probe_checks(
    *,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[Mapping[str, str], ...]:
    return (
        _login_home_mount_probe_check(
            command_runner=command_runner,
            kube_context=kube_context,
        ),
        _worker_home_mount_probe_check(
            command_runner=command_runner,
            kube_context=kube_context,
        ),
    )


def _persistent_mount_overwrite_checks(
    *,
    checkpoint: Mapping[str, Any],
    values: Mapping[str, Any],
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[Mapping[str, str], ...]:
    status = jail_persistent_mount_status(values)
    _ = (checkpoint, kube_context, command_runner)
    return (
        _fast_verification_check(
            "persistent jail mounts",
            "passed" if status.verified else "failed",
            status.reason,
        ),
    )


def _nodeset_replicas(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    name: str,
) -> tuple[bool, int]:
    payload = _json_from_command(
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
    if not _mapping(payload.get("metadata")):
        return False, 0
    return True, _resource_replicas(payload, default=0)


def _hold_persistent_migration_writers(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    values: Mapping[str, Any],
    state: dict[str, Any],
) -> list[str]:
    if state.get("status") == "held":
        return []
    resources: list[dict[str, Any]] = []
    login_state = _quiesce_scale_resource(
        command_runner=command_runner,
        kube_context=kube_context,
        resource="statefulsets.apps.kruise.io/login",
        namespace=_SOPERATOR_NAMESPACE,
        replicas=0,
        action="scale",
    )
    resources.append({"kind": "login", **login_state})
    for name in _target_worker_nodeset_names(values):
        exists, replicas = _nodeset_replicas(
            command_runner=command_runner,
            kube_context=kube_context,
            name=name,
        )
        item = {
            "kind": "nodeset",
            "name": name,
            "exists": exists,
            "replicas": replicas,
            "target_replicas": 0,
        }
        if exists and replicas != 0:
            _kubectl_patch_namespace_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource=f"nodeset/{name}",
                patch={"spec": {"replicas": 0}},
            )
        resources.append(item)
    state.update(
        {
            "status": "held",
            "held_at": state.get("held_at") or _utc_now(),
            "resources": resources,
        }
    )
    return ["Held login and worker consumers for one-time persistent mount migration."]


def _restore_persistent_migration_writers(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    state: dict[str, Any],
) -> list[str]:
    if state.get("status") != "held":
        return []
    for item in reversed(_sequence_of_mappings(state.get("resources"))):
        if not _bool_value(item.get("exists"), fallback=False):
            continue
        kind = str(item.get("kind") or "").strip()
        if kind == "nodeset":
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            _kubectl_patch_namespace_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource=f"nodeset/{name}",
                patch={"spec": {"replicas": _non_negative_int(item.get("replicas"), fallback=0)}},
            )
        elif kind == "login":
            resource = str(item.get("resource") or "statefulsets.apps.kruise.io/login")
            namespace = str(item.get("namespace") or _SOPERATOR_NAMESPACE)
            _kubectl_scale_namespace_resource(
                command_runner=command_runner,
                kube_context=kube_context,
                resource=resource,
                namespace=namespace,
                replicas=_non_negative_int(item.get("replicas"), fallback=1),
            )
    state["status"] = "restored"
    state["restored_at"] = _utc_now()
    return ["Restored login and worker consumers after persistent mount migration."]


def _ensure_persistent_migration_login_hold_allowed(
    *,
    phase: dict[str, Any],
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    policy: str,
    timeout_seconds: int,
) -> list[str]:
    state = phase.setdefault("persistent_migration_login_hold_policy", {})
    if not isinstance(state, dict):
        raise RuntimeError(
            "populate-jail-refresh.persistent_migration_login_hold_policy must be a mapping."
        )
    state["session_policy"] = policy
    state["session_drain_timeout_seconds"] = timeout_seconds
    if policy == EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY:
        state["status"] = "pending"
        state["reason"] = (
            "first-adoption persistent mount migration must temporarily stop login "
            "writers and cannot preserve continuous SSH endpoints under target-ready"
        )
        raise SoperatorMigrationPhasePending(
            "first-adoption persistent mount migration must temporarily stop login "
            "writers before copying customer rootfs paths. The target-ready login "
            "session policy refuses that SSH gap; rerun with --login-session-policy "
            "wait-active or grace-period during an approved maintenance window, or "
            "perform the persistent path migration manually."
        )
    login_pods = _login_pod_names(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    state["login_pods_before_hold"] = list(login_pods)
    session_phase = {"login_continuity": dict(state)}
    lines = _wait_for_login_session_policy(
        phase=session_phase,
        command_runner=command_runner,
        kube_context=kube_context,
        policy=policy,
        timeout_seconds=timeout_seconds,
        pod_names=login_pods,
    )
    state.update(dict(_mapping(session_phase.get("login_continuity"))))
    state["status"] = "allowed"
    return [
        "Persistent mount migration login hold allowed by "
        f"{policy} session policy.",
        *lines,
    ]


def _execute_legacy_persistent_mount_migration(
    *,
    checkpoint: dict[str, Any],
    phase: dict[str, Any],
    command_runner: SoperatorMigrationCommandRunner,
    kube_context: str,
    target_ref: str,
    image: str,
    jail_pvc: str,
    entries: Sequence[Mapping[str, Any]],
    scheduling: Mapping[str, Any] | None,
    checkpoint_writer: Callable[[], None] | None = None,
) -> tuple[bool, list[str]]:
    migration_state = phase.setdefault("legacy_persistent_mount_migration", {})
    if not isinstance(migration_state, dict):
        raise RuntimeError(
            "populate-jail-refresh.legacy_persistent_mount_migration must be a mapping."
        )
    if not entries:
        migration_state["status"] = "not_required"
        return False, []
    if migration_state.get("status") == "completed":
        return False, ["Persistent mount migration already completed in checkpoint."]
    manifest = _persistent_mount_migration_job_manifest(
        target_ref=target_ref,
        image=image,
        jail_pvc=jail_pvc,
        entries=entries,
        scheduling=scheduling,
    )
    job_name = str(_mapping(manifest.get("metadata")).get("name") or "")
    migration_state.update(
        {
            "status": "running",
            "job": {
                "name": job_name,
                "pvc": jail_pvc,
                "image": image,
            },
            "entries": [dict(to_plain_data(entry)) for entry in entries],
        }
    )
    if checkpoint_writer is not None:
        checkpoint_writer()
    if job_name:
        _delete_failed_job_before_reapply(
            command_runner=command_runner,
            kube_context=kube_context,
            name=job_name,
        )
    _kubectl_apply_objects(
        command_runner=command_runner,
        kube_context=kube_context,
        objects=(manifest,),
        timeout_seconds=300,
    )
    _wait_for_job_complete_or_failed(
        command_runner=command_runner,
        kube_context=kube_context,
        name=job_name,
        timeout_seconds=3900,
        job_label="Soperator persistent mount migration Job",
    )
    log_result = command_runner(
        [
            "kubectl",
            "--context",
            kube_context,
            "-n",
            _SOPERATOR_NAMESPACE,
            "logs",
            f"job/{job_name}",
        ],
        timeout_seconds=120,
        check=False,
    )
    copy_status_by_mount = _persistent_mount_migration_copy_status_by_log(
        log_result.stdout
    )
    for entry in migration_state["entries"]:
        if isinstance(entry, dict):
            mount_path = str(entry.get("mount_path") or "")
            source_status = str(entry.get("source_status") or "unknown")
            copy_status = copy_status_by_mount.get(mount_path)
            if not copy_status and source_status == "absent":
                copy_status = "source_missing"
            entry["copy_status"] = copy_status or "completed"
            entry["status"] = "completed"
    migration_state["status"] = "completed"
    migration_state["completed_at"] = _utc_now()
    _sync_persistent_mount_decisions_from_migration_entries(
        checkpoint,
        _sequence_of_mappings(migration_state.get("entries")),
    )
    if checkpoint_writer is not None:
        checkpoint_writer()
    copied = sum(
        1
        for entry in migration_state["entries"]
        if isinstance(entry, Mapping) and entry.get("copy_status") == "copied"
    )
    missing = sum(
        1
        for entry in migration_state["entries"]
        if isinstance(entry, Mapping) and entry.get("copy_status") == "source_missing"
    )
    skipped = sum(
        1
        for entry in migration_state["entries"]
        if isinstance(entry, Mapping) and entry.get("copy_status") == "skipped"
    )
    detail = f"copied={copied}, source_missing={missing}, skipped={skipped}"
    return True, [f"Persistent mount migration job completed: {job_name} ({detail})."]


def _execute_populate_jail_refresh_phase(
    *,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
    populate_jail_refresh: str,
    job_policy: str,
    cancel_job_ids: Sequence[str],
    requeue_job_ids: Sequence[str],
    job_wait_timeout_seconds: int,
    job_refresh_interval_seconds: int,
    login_session_policy: str = EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY,
    login_session_drain_timeout_seconds: int = EXTERNAL_LOGIN_SESSION_DRAIN_TIMEOUT_SECONDS,
    slurm_decision_recorder: Callable[[Mapping[str, Any]], None] | None = None,
    interactive_prompt_pause: Callable[[], Any] | None = None,
    allow_resolved_interactive_job_policy: bool = False,
    checkpoint_writer: Callable[[], None] | None = None,
    jail_sfs_resize_handler: JailSfsResizeHandler | None = None,
) -> tuple[bool, list[str]]:
    phase = _phase_state(checkpoint, POPULATE_JAIL_REFRESH_PHASE_ID)
    before = inspect_populate_jail(
        command_runner,
        namespace=_SOPERATOR_NAMESPACE,
        target_ref=target_ref,
        kube_context=kube_context,
    )
    plan = plan_populate_jail_refresh(
        mode=populate_jail_refresh,
        chart_changed=True,
        before=before,
        after_chart=before,
        namespace=_SOPERATOR_NAMESPACE,
    )
    phase["plan"] = plan.as_payload()
    if checkpoint_writer is not None:
        checkpoint_writer()
    if not plan.required:
        result = skipped_populate_jail_refresh_result(plan)
        phase["result"] = result.as_payload()
        phase["completed_at"] = _utc_now()
        if checkpoint_writer is not None:
            checkpoint_writer()
        return False, [f"Jail Upgrade skipped: {result.reason}."]
    if plan.mode == "manual":
        result = manual_populate_jail_refresh_result(plan)
        phase["result"] = result.as_payload()
        phase["pending_reason"] = result.detail
        if checkpoint_writer is not None:
            checkpoint_writer()
        raise SoperatorMigrationPhasePending(result.detail)

    topology_lines: list[str] = []
    if _mapping(source_report.get("snapshot")) and _mapping(source_report.get("report")):
        topology_lines = _ensure_worker_nodeset_topology_checkpoint(
            checkpoint=checkpoint,
            source_report=source_report,
            kube_context=kube_context,
            command_runner=command_runner,
        )
    if topology_lines and checkpoint_writer is not None:
        checkpoint_writer()
    values = _patch_target_values_for_compute(
        checkpoint=checkpoint,
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        live_snapshot=live_snapshot,
    )
    persistent_mount_checks = _persistent_mount_overwrite_checks(
        checkpoint=checkpoint,
        values=values,
        kube_context=kube_context,
        command_runner=command_runner,
    )
    phase["persistent_jail_mounts"] = {
        "checks": list(persistent_mount_checks),
        "status": "failed" if _fast_verification_failed(persistent_mount_checks) else "verified",
    }
    if _fast_verification_failed(persistent_mount_checks):
        if checkpoint_writer is not None:
            checkpoint_writer()
        raise SoperatorMigrationPhasePending(
            "jail rootfs refresh is blocked until persistent jail mounts are verified. "
            "Review the populate-jail-refresh.persistent_jail_mounts checks in the checkpoint."
        )
    slots = active_passive_jail_rootfs_slots(values)
    job_scheduling = active_passive_populate_jail_job_scheduling(
        values,
        target_ref=target_ref,
    )
    phase["rootfs_strategy"] = "activePassive"
    phase["rootfs_slots"] = slots.as_payload()
    legacy_active_rootfs = jail_rootfs_uses_legacy_active_source(values)
    legacy_migration_entries = _legacy_persistent_mount_migration_entries(values)
    legacy_active_rootfs = bool(legacy_active_rootfs or legacy_migration_entries)
    phase["legacy_active_rootfs"] = legacy_active_rootfs
    existing_migration_state = dict(_mapping(phase.get("legacy_persistent_mount_migration")))
    migration_status = str(existing_migration_state.get("status") or "").strip()
    if migration_status != "completed":
        migration_status = "planned" if legacy_migration_entries else "not_required"
    phase["legacy_persistent_mount_migration"] = {
        **existing_migration_state,
        "status": migration_status,
        "entries": existing_migration_state.get("entries")
        if migration_status == "completed"
        else [dict(to_plain_data(entry)) for entry in legacy_migration_entries],
    }
    checkpoint["populate_jail_refresh"] = {
        **dict(_mapping(checkpoint.get("populate_jail_refresh"))),
        "legacy_persistent_mount_migration": dict(
            _mapping(phase.get("legacy_persistent_mount_migration"))
        ),
    }
    target_version = str(_mapping(source_report.get("report")).get("target_version", "") or "")
    populate_image = before.image or plan.after_chart.image
    if not populate_image:
        raise SoperatorMigrationPhasePending(
            "Could not resolve a populate-jail image for passive-slot rootfs population."
        )
    source_probe_entries: tuple[dict[str, Any], ...] = ()
    if legacy_migration_entries and migration_status != "completed":
        source_probe_entries = _probe_legacy_persistent_mount_sources(
            command_runner=command_runner,
            kube_context=kube_context,
            target_ref=target_ref,
            image=populate_image,
            jail_pvc=_target_pvc_name_for_storage_key(payload, target_ref, "jail"),
            entries=legacy_migration_entries,
            scheduling=job_scheduling,
        )
        source_by_mount = {
            str(entry.get("mount_path") or ""): entry
            for entry in source_probe_entries
            if str(entry.get("mount_path") or "")
        }
        updated_entries: list[dict[str, Any]] = []
        for entry in legacy_migration_entries:
            next_entry = dict(to_plain_data(entry))
            probe = source_by_mount.get(str(next_entry.get("mount_path") or ""))
            if probe:
                next_entry["source_status"] = str(probe.get("source_status") or "unknown")
                next_entry["marker_status"] = str(probe.get("marker_status") or "unknown")
                next_entry["marker_present"] = bool(probe.get("marker_present"))
            else:
                next_entry["source_status"] = "unknown"
            updated_entries.append(next_entry)
        legacy_migration_entries = tuple(updated_entries)
        phase["legacy_persistent_mount_source_probe"] = {
            "status": "completed",
            "probed_at": _utc_now(),
            "entries": [dict(entry) for entry in source_probe_entries],
        }
        phase["legacy_persistent_mount_migration"] = {
            **dict(_mapping(phase.get("legacy_persistent_mount_migration"))),
            "entries": [dict(entry) for entry in legacy_migration_entries],
        }
        _sync_persistent_mount_decisions_from_migration_entries(
            checkpoint,
            legacy_migration_entries,
        )
        checkpoint["populate_jail_refresh"] = {
            **dict(_mapping(checkpoint.get("populate_jail_refresh"))),
            "legacy_persistent_mount_source_probe": dict(
                _mapping(phase.get("legacy_persistent_mount_source_probe"))
            ),
            "legacy_persistent_mount_migration": dict(
                _mapping(phase.get("legacy_persistent_mount_migration"))
            ),
        }
        if checkpoint_writer is not None:
            checkpoint_writer()

    def _probe_jail_capacity() -> JailCapacityPreflight:
        copy_required_entries = tuple(
            entry
            for entry in legacy_migration_entries
            if str(entry.get("source_status") or "unknown") != "absent"
        )
        active_pvc = (
            _target_pvc_name_for_storage_key(payload, target_ref, "jail")
            if legacy_active_rootfs
            else slots.active_pvc
        )
        passive_pvc = active_pvc if legacy_active_rootfs else slots.passive_pvc
        active_rootfs_path = JAIL_LEGACY_ROOT_PATH if legacy_active_rootfs else ""
        exclude_paths = (
            *jail_persistent_mount_exclude_paths(values),
            *(str(entry.get("source_path") or "") for entry in legacy_migration_entries),
        )
        extra_required_paths = (
            ()
            if str(
                _mapping(phase.get("legacy_persistent_mount_migration")).get("status") or ""
            ).strip()
            == "completed"
            else tuple(str(entry.get("source_path") or "") for entry in copy_required_entries)
        )
        return probe_active_passive_jail_capacity(
            command_runner,
            namespace=_SOPERATOR_NAMESPACE,
            target_ref=target_ref,
            image=populate_image,
            active_pvc=active_pvc,
            passive_pvc=passive_pvc,
            active_rootfs_path=active_rootfs_path,
            exclude_paths=exclude_paths,
            extra_required_paths=extra_required_paths,
            scheduling=job_scheduling,
            kube_context=kube_context,
        )

    capacity_preflight = _probe_jail_capacity()
    phase["capacity_preflight"] = capacity_preflight.as_payload()
    checkpoint["populate_jail_refresh"] = {
        **dict(_mapping(checkpoint.get("populate_jail_refresh"))),
        "capacity_preflight": capacity_preflight.as_payload(),
    }
    if checkpoint_writer is not None:
        checkpoint_writer()
    if not capacity_preflight.sufficient:
        if jail_sfs_resize_handler is None:
            phase["capacity_preflight_check"] = dict(
                capacity_preflight_check_payload(capacity_preflight)
            )
            if checkpoint_writer is not None:
                checkpoint_writer()
            raise SoperatorMigrationPhasePending(
                "passive jail rootfs slot does not have enough free space. "
                "Review populate-jail-refresh.capacity_preflight in the checkpoint "
                "and resize the existing Nebius jail SFS before retrying."
            )
        capacity_preflight = jail_sfs_resize_handler(
            capacity_preflight,
            _probe_jail_capacity,
            phase,
            checkpoint,
            checkpoint_writer,
        )
    phase["capacity_preflight"] = capacity_preflight.as_payload()
    phase["capacity_preflight_check"] = dict(capacity_preflight_check_payload(capacity_preflight))
    checkpoint["populate_jail_refresh"] = {
        **dict(_mapping(checkpoint.get("populate_jail_refresh"))),
        "capacity_preflight": capacity_preflight.as_payload(),
    }
    if checkpoint_writer is not None:
        checkpoint_writer()
    if not capacity_preflight.sufficient:
        raise SoperatorMigrationPhasePending(
            "passive jail rootfs slot does not have enough free space after resize recheck."
        )
    checkpoint_worker_groups = tuple(
        str(group or "") for group in checkpoint.get("worker_node_groups", []) or []
    )
    live_source_slurmcluster_present = _live_source_slurmcluster_present(
        command_runner=command_runner,
        kube_context=kube_context,
        source_report=source_report,
        target_ref=target_ref,
    )
    live_worker_slurm_nodes = _external_upgrade_worker_nodeset_slurm_nodes(
        command_runner=command_runner,
        kube_context=kube_context,
    )
    source_worker_nodes = _nodes_for_worker_groups(
        source_report=source_report,
        worker_node_groups=checkpoint_worker_groups,
    )
    quiet_nodes = live_worker_slurm_nodes or source_worker_nodes
    phase["slurm_job_scope_nodes"] = list(quiet_nodes)
    phase["slurm_job_scope"] = (
        "live-worker-nodesets" if live_worker_slurm_nodes else "source-worker-node-groups"
    )
    quiet_lines = _ensure_slurm_quiet(
        command_runner=command_runner,
        kube_context=kube_context,
        node_names=quiet_nodes,
        job_policy=job_policy,
        cancel_job_ids=cancel_job_ids,
        requeue_job_ids=requeue_job_ids,
        job_wait_timeout_seconds=job_wait_timeout_seconds,
        job_refresh_interval_seconds=job_refresh_interval_seconds,
        slurm_decision_recorder=slurm_decision_recorder,
        allow_missing_login_recovery=not live_source_slurmcluster_present,
        interactive_prompt_pause=interactive_prompt_pause,
        allow_resolved_interactive_job_policy=allow_resolved_interactive_job_policy,
    )
    phase["slurm_quiet_at"] = _utc_now()
    phase["slurm_quiet_lines"] = list(quiet_lines)
    if checkpoint_writer is not None:
        checkpoint_writer()
    mutation_performed = False
    migration_lines: list[str] = []
    writer_hold_state = phase.setdefault("persistent_migration_writer_hold", {})
    if not isinstance(writer_hold_state, dict):
        raise RuntimeError("populate-jail-refresh.persistent_migration_writer_hold must be a mapping.")

    def _restore_after_refresh_failure() -> None:
        cleanup_errors: list[str] = []
        migration_completed = (
            str(
                _mapping(phase.get("legacy_persistent_mount_migration")).get("status")
                or ""
            ).strip()
            == "completed"
        )
        keep_writers_held = bool(
            legacy_migration_entries
            and writer_hold_state.get("status") == "held"
            and migration_completed
        )
        if keep_writers_held:
            phase["persistent_migration_failure_boundary"] = {
                "status": "writers_held",
                "reason": (
                    "persistent mount migration already completed; keeping legacy "
                    "writers stopped so the shared copy cannot become stale"
                ),
            }
        if (
            legacy_migration_entries
            and writer_hold_state.get("status") == "held"
            and not keep_writers_held
        ):
            try:
                migration_lines.extend(
                    _restore_persistent_migration_writers(
                        command_runner=command_runner,
                        kube_context=kube_context,
                        state=writer_hold_state,
                    )
                )
                phase["persistent_migration_writer_hold"] = dict(writer_hold_state)
            except Exception as exc:  # pragma: no cover - best-effort operational cleanup
                cleanup_errors.append(f"persistent writer restore failed: {exc}")
        if phase.get("slurm_quiet_at") and not phase.get("slurm_resumed_at"):
            if keep_writers_held:
                phase["slurm_resume_after_failure"] = {
                    "status": "skipped",
                    "reason": (
                        "persistent mount migration already completed and legacy writers "
                        "remain stopped"
                    ),
                }
            else:
                try:
                    _resume_slurm_partitions(
                        command_runner=command_runner,
                        kube_context=kube_context,
                    )
                    phase["slurm_resumed_after_failure_at"] = _utc_now()
                except Exception as exc:  # pragma: no cover - best-effort operational cleanup
                    cleanup_errors.append(f"Slurm resume after failure failed: {exc}")
        if cleanup_errors:
            phase["refresh_failure_cleanup_errors"] = cleanup_errors
        if checkpoint_writer is not None:
            checkpoint_writer()

    if legacy_migration_entries:
        migration_lines.extend(
            _ensure_persistent_migration_login_hold_allowed(
                phase=phase,
                command_runner=command_runner,
                kube_context=kube_context,
                policy=login_session_policy,
                timeout_seconds=login_session_drain_timeout_seconds,
            )
        )
        if checkpoint_writer is not None:
            checkpoint_writer()
        migration_lines.extend(
            _hold_persistent_migration_writers(
                command_runner=command_runner,
                kube_context=kube_context,
                values=values,
                state=writer_hold_state,
            )
        )
        if checkpoint_writer is not None:
            checkpoint_writer()
        try:
            migration_mutation, lines = _execute_legacy_persistent_mount_migration(
                checkpoint=checkpoint,
                phase=phase,
                command_runner=command_runner,
                kube_context=kube_context,
                target_ref=target_ref,
                image="ubuntu:24.04",
                jail_pvc=_target_pvc_name_for_storage_key(payload, target_ref, "jail"),
                entries=legacy_migration_entries,
                scheduling=job_scheduling,
                checkpoint_writer=checkpoint_writer,
            )
        except Exception:
            _restore_after_refresh_failure()
            raise
        mutation_performed = mutation_performed or migration_mutation
        migration_lines.extend(lines)
        checkpoint["populate_jail_refresh"] = {
            **dict(_mapping(checkpoint.get("populate_jail_refresh"))),
            "legacy_persistent_mount_migration": dict(
                _mapping(phase.get("legacy_persistent_mount_migration"))
            ),
        }
        if checkpoint_writer is not None:
            checkpoint_writer()
    try:
        maintenance_restored = True
        refresh_values = populate_jail_refresh_values(values)
        _helm_upgrade_target_soperator(
            command_runner=command_runner,
            kube_context=kube_context,
            values=refresh_values,
            expected_version=target_version,
            wait=False,
        )
        mutation_performed = True
        phase["passive_slot_refresh_values_applied_at"] = _utc_now()
        if checkpoint_writer is not None:
            checkpoint_writer()
        manifest = active_passive_populate_jail_job_manifest(
            namespace=_SOPERATOR_NAMESPACE,
            target_ref=target_ref,
            image=populate_image,
            passive_slot=slots.passive_slot,
            passive_pvc=slots.passive_pvc,
            image_pull_policy=str(
                _mapping(values.get("populateJail")).get("imagePullPolicy") or "IfNotPresent"
            ),
            scheduling=job_scheduling,
        )
        job_name = str(_mapping(manifest.get("metadata")).get("name", "") or "")
        if job_name:
            command_runner(
                [
                    "kubectl",
                    "--context",
                    kube_context,
                    "-n",
                    _SOPERATOR_NAMESPACE,
                    "delete",
                    "job",
                    job_name,
                    "--ignore-not-found",
                    "--wait=false",
                ],
                timeout_seconds=300,
            )
        _kubectl_apply_objects(
            command_runner=command_runner,
            kube_context=kube_context,
            objects=(manifest,),
            timeout_seconds=300,
        )
        phase["passive_slot_populate_job"] = {
            "name": job_name,
            "slot": slots.passive_slot,
            "pvc": slots.passive_pvc,
            "image": populate_image,
        }
        if checkpoint_writer is not None:
            checkpoint_writer()
        refreshed = wait_for_active_passive_populate_jail_job(
            command_runner,
            namespace=_SOPERATOR_NAMESPACE,
            job_name=job_name,
            expected_image=populate_image,
            kube_context=kube_context,
        )
        phase["job_completed_at"] = _utc_now()
        if checkpoint_writer is not None:
            checkpoint_writer()
        if legacy_migration_entries:
            phase["login_service_ready_before_switch"] = {
                "status": "skipped",
                "reason": "login consumers are held during one-time persistent mount migration",
            }
        else:
            phase["login_service_ready_before_switch"] = wait_for_login_service_ready_endpoints(
                command_runner,
                namespace=_SOPERATOR_NAMESPACE,
                target_ref=target_ref,
                kube_context=kube_context,
            )
        if checkpoint_writer is not None:
            checkpoint_writer()
        switched_values = switch_active_passive_jail_rootfs_values(values)
        _helm_upgrade_target_soperator(
            command_runner=command_runner,
            kube_context=kube_context,
            values=switched_values,
            expected_version=target_version,
            wait=False,
        )
        phase["consumer_switch_applied_at"] = _utc_now()
        phase["rollback_slot"] = "legacy-rootfs" if legacy_active_rootfs else slots.active_slot
        phase["active_slot"] = slots.passive_slot
        if legacy_migration_entries:
            migration_lines.extend(
                _restore_persistent_migration_writers(
                    command_runner=command_runner,
                    kube_context=kube_context,
                    state=writer_hold_state,
                )
            )
            phase["persistent_migration_writer_hold"] = dict(writer_hold_state)
            if checkpoint_writer is not None:
                checkpoint_writer()
        phase["login_service_ready_after_switch"] = (
            wait_for_login_statefulset_rollout_with_ready_endpoint_guard(
                command_runner,
                namespace=_SOPERATOR_NAMESPACE,
                target_ref=target_ref,
                kube_context=kube_context,
            )
        )
        if checkpoint_writer is not None:
            checkpoint_writer()
        _kubectl_rollout_status(
            command_runner=command_runner,
            kube_context=kube_context,
            namespace=_SOPERATOR_NAMESPACE,
            resource="deployment/soperator-manager",
            timeout="15m",
        )
        _resume_slurm_partitions(
            command_runner=command_runner,
            kube_context=kube_context,
        )
        phase["slurm_resumed_at"] = _utc_now()
    except Exception:
        _restore_after_refresh_failure()
        raise
    result = completed_populate_jail_refresh_result(
        mode=plan.mode,
        reason=plan.reason,
        snapshot=refreshed,
        maintenance_restored=maintenance_restored,
    )
    phase["result"] = result.as_payload()
    phase["completed_at"] = _utc_now()
    if checkpoint_writer is not None:
        checkpoint_writer()
    return mutation_performed, [
        *topology_lines,
        *migration_lines,
        "Slurm partitions resumed after active/passive jail rootfs refresh.",
        (
            "Jail rootfs passive slot populated and consumers switched "
            f"from {slots.active_slot} to {slots.passive_slot}."
        ),
    ]


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


def _upgrade_report_json_path(config_path: Path) -> Path:
    return _migration_validation_reports_dir(config_path) / UPGRADE_REPORT_JSON_FILENAME


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
        item["check_old_source_flux"] = True
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


def _migration_safety_baseline_from_checkpoint(
    checkpoint: Mapping[str, Any],
) -> ProtectedCustomerState | None:
    safety = checkpoint.get("upgrade_safety")
    safety_map = safety if isinstance(safety, Mapping) else {}
    protected = safety_map.get("protected_customer_state")
    protected_map = protected if isinstance(protected, Mapping) else {}
    before = protected_map.get("before")
    return protected_customer_state_from_payload(before if isinstance(before, Mapping) else None)


def _capture_external_upgrade_protected_state(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    payload: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
) -> ProtectedCustomerState:
    state = capture_protected_customer_state(
        command_runner=command_runner,
        target_ref=target_ref,
        namespace=_SOPERATOR_NAMESPACE,
        kube_context=kube_context,
        source_payload=payload,
    )
    if not state.complete:
        details = "; ".join(state.warnings) or "unknown protected-state capture failure"
        raise RuntimeError(
            "External Soperator upgrade protected-state capture is incomplete; refusing to mutate. "
            + details
        )
    return state


def _run_external_upgrade_safety_verification(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    checkpoint: Mapping[str, Any],
    payload: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    approve_remediation: bool,
) -> Any:
    before_state = _migration_safety_baseline_from_checkpoint(checkpoint)
    result = run_post_upgrade_fast_verification(
        command_runner=command_runner,
        target_ref=target_ref,
        namespace=_SOPERATOR_NAMESPACE,
        kube_context=kube_context,
        before_state=before_state,
        source_payload=payload,
        external_cluster=True,
        remediation_approved=approve_remediation,
    )
    return result


def external_soperator_upgrade_protected_comparison_passed(
    *,
    config_path: Path,
    target_ref: str,
) -> bool:
    checkpoint = _load_checkpoint(soperator_migration_checkpoint_path(config_path, target_ref))
    if checkpoint is None:
        return False
    return _checkpoint_protected_comparison_passed(checkpoint)


def _checkpoint_protected_comparison_passed(checkpoint: Mapping[str, Any]) -> bool:
    safety = checkpoint.get("upgrade_safety")
    safety_map = safety if isinstance(safety, Mapping) else {}
    verification = safety_map.get("post_upgrade_verification")
    verification_map = verification if isinstance(verification, Mapping) else {}
    comparison = verification_map.get("comparison")
    comparison_map = comparison if isinstance(comparison, Mapping) else {}
    protected = safety_map.get("protected_customer_state")
    protected_map = protected if isinstance(protected, Mapping) else {}
    return (
        bool(verification_map.get("passed"))
        and int(comparison_map.get("blocked_count") or 0) == 0
        and str(protected_map.get("after_hash", "") or "").strip() != ""
    )


def _failed_safety_check_names(safety_result: Any) -> list[str]:
    return [
        str(check.get("name") or "check")
        for check in safety_result.checks
        if isinstance(check, Mapping) and check.get("status") == "failed"
    ]


def _store_external_upgrade_safety_verification(
    *,
    checkpoint: dict[str, Any],
    safety_result: Any,
    approve_remediation: bool,
) -> None:
    checkpoint["upgrade_safety"] = update_safety_payload_with_verification(
        checkpoint.get("upgrade_safety")
        if isinstance(checkpoint.get("upgrade_safety"), Mapping)
        else None,
        safety_result,
        remediation_approved=approve_remediation,
    )
    phase = _phase_state(checkpoint, "validation-and-rollback-hold")
    phase["shared_safety_verification"] = safety_result.as_payload()


def _ensure_external_upgrade_safety_verified(
    *,
    command_runner: SoperatorMigrationCommandRunner,
    checkpoint: dict[str, Any],
    payload: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    approve_remediation: bool,
) -> list[str]:
    if _checkpoint_protected_comparison_passed(checkpoint):
        return ["Shared Soperator upgrade safety verification already passed."]
    safety_result = _run_external_upgrade_safety_verification(
        command_runner=command_runner,
        checkpoint=checkpoint,
        payload=payload,
        target_ref=target_ref,
        kube_context=kube_context,
        approve_remediation=approve_remediation,
    )
    _store_external_upgrade_safety_verification(
        checkpoint=checkpoint,
        safety_result=safety_result,
        approve_remediation=approve_remediation,
    )
    if not safety_result.passed:
        failed = _failed_safety_check_names(safety_result)
        raise SoperatorMigrationPhasePending(
            "Shared Soperator upgrade safety verification failed"
            + (": " + ", ".join(failed) if failed else ".")
        )
    return ["Shared Soperator upgrade safety verification completed: passed."]


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
            str(reports_dir / str(spec.get("report_file") or "deploy-smoke-report.json"))
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
        f"- External Soperator upgrade target: `{target_ref}`",
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
        "External Soperator upgrade report will include the MK8s GPU validation rollup.",
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
    approve_remediation: bool = False,
    require_target_soperator_helm: bool = True,
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
        require_target_release=require_target_soperator_helm,
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
    safety_lines = _ensure_external_upgrade_safety_verified(
        command_runner=command_runner,
        checkpoint=checkpoint,
        payload=payload,
        target_ref=target_ref,
        kube_context=kube_context,
        approve_remediation=approve_remediation,
    )
    phase["validation_contract_revision"] = _VALIDATION_HOLD_REVISION
    phase["validated_at"] = _utc_now()
    return bool(phase.get("mk8s_gpu_validations") or phase.get("soperator_cluster_validations")), [
        "Validation hold passed: nodes are present and Soperator manager deployment is healthy.",
        *helm_state_lines,
        *validation_lines,
        *soperator_validation_lines,
        *safety_lines,
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
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
    checkpoint_writer: Callable[[], None] | None = None,
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
        if checkpoint_writer is not None:
            checkpoint_writer()
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
        phase["old_nodes_cordoned_at"] = _utc_now()
        if checkpoint_writer is not None:
            checkpoint_writer()
        _uncordon_or_drain_nodes(
            command_runner=command_runner,
            kube_context=kube_context,
            nodes=old_nodes,
            action="drain",
        )
        phase["old_nodes_drained_at"] = _utc_now()
        if checkpoint_writer is not None:
            checkpoint_writer()
    retired: list[dict[str, str]] = []
    for group_name, item in old_groups_raw.items():
        node_group_id = str(_mapping(item).get("id", "") or "").strip()
        if not node_group_id:
            continue
        _scale_node_group(
            nebius_api=nebius_api,
            node_group_id=node_group_id,
            count=0,
        )
        phase["last_scaled_node_group_id"] = node_group_id
        if checkpoint_writer is not None:
            checkpoint_writer()
        _delete_node_group(
            nebius_api=nebius_api,
            node_group_id=node_group_id,
        )
        retired.append({"source_group": str(group_name), "node_group_id": node_group_id})
        phase["retired_node_groups"] = retired
        if checkpoint_writer is not None:
            checkpoint_writer()
    if not retired:
        raise SoperatorMigrationPhasePending(
            "retire-old-resources found replaced service-role groups in the checkpoint, but no "
            "Nebius node group ids were recorded."
        )
    phase["retired_node_groups"] = retired
    if _snapshot_storage(source_report):
        phase["storage_retirement"] = "held"
    if checkpoint_writer is not None:
        checkpoint_writer()
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
        raise RuntimeError(
            f"External Soperator upgrade checkpoint is invalid JSON: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"External Soperator upgrade checkpoint must be a JSON object: {path}")
    if payload.get("schema") != SOPERATOR_MIGRATION_EXECUTION_SCHEMA:
        raise RuntimeError(_unsupported_checkpoint_schema_message(path))
    if _checkpoint_has_progress_only_locked_path(payload):
        raise RuntimeError(_locked_upgrade_path_repair_message())
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
        charts = _sequence_of_mappings(phase.get("charts"))
        return f"target GPU stack charts applied or verified: {len(charts)}."
    if phase_id == "create-aligned-sfs":
        filesystems = _mapping(phase.get("filesystems"))
        expected = len(_soperator_storage_keys_for_target(payload={}, target_ref=""))
        return f"aligned SFS filesystems recorded: {len(filesystems)}/{expected}."
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
    if phase_id == POPULATE_JAIL_REFRESH_PHASE_ID:
        result = _mapping(phase.get("result"))
        status = str(result.get("status", "") or "not_run")
        image = str(result.get("job_image") or result.get("target_image") or "").strip()
        reason = str(result.get("reason", "") or "").strip()
        if image:
            return f"Jail Upgrade status={status}; image={image}."
        return f"Jail Upgrade status={status}{f'; {reason}' if reason else ''}."
    if phase_id == "validation-and-rollback-hold":
        gpu_count = _positive_int(phase.get("mk8s_gpu_validation_count"), fallback=0)
        soperator_count = _positive_int(
            phase.get("soperator_cluster_validation_count"),
            fallback=0,
        )
        safety = _mapping(phase.get("shared_safety_verification"))
        safety_status = str(safety.get("status", "") or "not_run")
        safety_passed = safety.get("passed")
        if safety_passed is True:
            safety_result = "passed"
        elif safety_passed is False:
            safety_result = "failed"
        else:
            safety_result = "unknown"
        return (
            f"validation checks recorded: MK8s GPU={gpu_count}, "
            f"Soperator/Slurm={soperator_count}; shared safety={safety_status}/{safety_result}."
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


def _external_upgrade_status_summary(
    *,
    pending_phase: str,
    pending_reason: str,
    mutation_performed: bool,
) -> str:
    normalized_phase = str(pending_phase or "").strip() or "none"
    reason = str(pending_reason or "").strip()
    if normalized_phase == "none":
        return "completed." if mutation_performed else "complete; no upgrade mutation was required."
    if normalized_phase == "customer-approval":
        return "waiting for customer approval; no upgrade mutation was performed."
    if "stopped by operator" in reason.lower():
        mutation_state = "after upgrade mutation" if mutation_performed else "before upgrade mutation"
        return (
            f"stopped by operator {mutation_state}; rerun the same command "
            "after choosing how to handle affected Slurm jobs."
        )
    if mutation_performed:
        return "pending after upgrade mutation; rerun the same command to resume."
    return "pending before upgrade mutation; rerun the same command after resolving the gate."


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
    json_report_path = _upgrade_report_json_path(config_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    phase_state = _mapping(checkpoint.get("phase_state"))
    report_phase_ids = _external_upgrade_report_phase_ids(
        phase_ids=phase_ids,
        checkpoint=checkpoint,
    )
    generated_at = _utc_now()
    locked_upgrade_path = _checkpoint_locked_upgrade_path(checkpoint)
    upgrade_path_progress = _checkpoint_upgrade_path_progress(checkpoint)
    segment_history = _locked_upgrade_path_segment_history(
        config_path=config_path,
        target_ref=target_ref,
        checkpoint=checkpoint,
        locked_upgrade_path=locked_upgrade_path,
    )
    locked_soperator_chart = _mapping(locked_upgrade_path.get("soperator_chart"))
    locked_jail_rootfs = _mapping(locked_upgrade_path.get("jail_rootfs"))
    locked_chart_current = str(locked_soperator_chart.get("current_version", "") or "").strip()
    locked_chart_target = str(locked_soperator_chart.get("target_version", "") or "").strip()
    locked_jail_current = str(locked_jail_rootfs.get("current_version", "") or "").strip()
    locked_jail_target = str(locked_jail_rootfs.get("target_version", "") or "").strip()
    locked_jail_refresh = locked_jail_rootfs.get("refresh_required") is True
    lines = [
        "# External Soperator Upgrade Report",
        "",
        f"- Target: `{target_ref}`",
        f"- Source version: `{source_version or 'unknown'}`",
        f"- Target version: `{target_version or 'unknown'}`",
        f"- Generated at: `{generated_at}`",
        f"- Checkpoint: `{checkpoint_path}`",
        f"- JSON report: `{json_report_path}`",
        "- Upgrade status: `"
        + _external_upgrade_status_summary(
            pending_phase=pending_phase,
            pending_reason=pending_reason,
            mutation_performed=mutation_performed,
        )
        + "`",
        f"- Pending phase: `{pending_phase or 'none'}`",
        f"- Pending reason: `{pending_reason or 'none'}`",
        "- Upgrade performed: `" + ("yes" if mutation_performed else "no") + "`",
        "",
    ]
    if locked_upgrade_path:
        completed_count = len(upgrade_path_progress.get("completed_segment_ids", []) or [])
        current_segment_id = str(upgrade_path_progress.get("current_segment_id", "") or "")
        remaining_count = max(len(segment_history) - completed_count, 0)
        lines.extend(
            [
                "## Locked Upgrade Path",
                "",
                f"- Path fingerprint: `{upgrade_path_progress.get('fingerprint') or 'unknown'}`",
                f"- Current segment: `{current_segment_id or 'none'}`",
                f"- Completed segments: `{completed_count}`",
                f"- Remaining segments: `{remaining_count}`",
                "- Soperator chart: `"
                + (locked_chart_current or "unknown")
                + "` -> `"
                + (locked_chart_target or "unknown")
                + "`",
                "- Jail rootfs: `"
                + (locked_jail_current or "unknown")
                + "` -> `"
                + (locked_jail_target or "unknown")
                + "` ("
                + ("refresh required" if locked_jail_refresh else "no refresh required")
                + ")",
                "",
                "| Segment | Status | Snapshot | Backup |",
                "| --- | --- | --- | --- |",
            ]
        )
        for segment in segment_history:
            snapshot = str(segment.get("report_path", "") or "")
            backup = str(segment.get("backup_path", "") or "")
            lines.append(
                "| "
                + str(segment.get("title", "") or segment.get("id", "segment"))
                + " | `"
                + str(segment.get("status", "remaining"))
                + "` | `"
                + (snapshot or "pending")
                + "` | `"
                + (backup or "pending")
                + "` |"
            )
        lines.append("")
    lines.extend(["## Upgrade Steps", ""])
    phase_reports: list[dict[str, Any]] = []
    stage_verification_reports: list[dict[str, Any]] = []
    for phase_id in report_phase_ids:
        if phase_id in _POST_UPGRADE_CHECK_PHASE_IDS:
            continue
        status = _phase_report_status(
            phase_id,
            completed_phases=completed_phases,
            pending_phase=pending_phase,
        )
        phase = _mapping(phase_state.get(phase_id))
        fast_verification = stage_fast_verification_report(phase_id, phase)
        top_level_stage = external_soperator_upgrade_top_level_stage(phase_id)
        if phase_id in _FAST_STAGE_VERIFICATION_PHASE_IDS:
            stage_verification_reports.append(fast_verification)
        phase_reports.append(
            {
                "id": phase_id,
                "top_level_stage": top_level_stage,
                "status": status_label(status),
                "summary": _phase_report_summary(phase_id, phase),
                "fast_verification": fast_verification,
                "state": to_plain_data(phase),
            }
        )
        lines.extend([f"### {phase_id}", "", f"- Status: `{status_label(status)}`"])
        lines.append(f"- Top-level stage: `{top_level_stage}`")
        lines.append(f"- Summary: {_phase_report_summary(phase_id, phase)}")
        if phase_id in _FAST_STAGE_VERIFICATION_PHASE_IDS:
            lines.append(
                "- Fast verification: `"
                + status_label(str(fast_verification.get("status", "") or "not_run"))
                + "` - "
                + str(fast_verification.get("summary", "") or "No summary recorded.")
            )
        lines.append("")
    for phase_id in _POST_UPGRADE_CHECK_PHASE_IDS:
        phase = _mapping(phase_state.get(phase_id))
        fast_verification = stage_fast_verification_report(phase_id, phase)
        stage_verification_reports.append(fast_verification)
        verification_status = str(fast_verification.get("status", "") or "not_run")
        verification_summary = str(fast_verification.get("summary", "") or "No summary recorded.")
        top_level_stage = external_soperator_upgrade_top_level_stage(phase_id)
        phase_reports.append(
            {
                "id": phase_id,
                "top_level_stage": top_level_stage,
                "status": status_label(verification_status),
                "summary": verification_summary,
                "fast_verification": fast_verification,
                "state": to_plain_data(phase),
            }
        )
        lines.extend(
            [
                f"### {phase_id}",
                "",
                f"- Status: `{status_label(verification_status)}`",
                f"- Top-level stage: `{top_level_stage}`",
                f"- Summary: {verification_summary}",
                "- Fast verification: `"
                + status_label(verification_status)
                + "` - "
                + verification_summary,
                "",
            ]
        )
    lines.extend(stage_fast_verification_markdown_lines(stage_verification_reports))
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
    lines.extend(safety_report_markdown_lines(_mapping(checkpoint.get("upgrade_safety"))))
    lines.append("")
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
    upgrade_safety = _mapping(checkpoint.get("upgrade_safety"))
    json_report = {
        "schema": SOPERATOR_MIGRATION_REPORT_SCHEMA,
        "target_ref": target_ref,
        "source_version": source_version or "",
        "target_version": target_version or "",
        "generated_at": generated_at,
        "checkpoint_path": str(checkpoint_path),
        "markdown_report": str(report_path),
        "json_report": str(json_report_path),
        "pending_phase": pending_phase or "none",
        "pending_reason": pending_reason or "",
        "upgrade_performed": mutation_performed,
        "completed_phases": list(_ordered_phase_list(completed_phases, report_phase_ids)),
        "phases": phase_reports,
        "stage_verification": stage_verification_reports,
        "backup": to_plain_data(_mapping(checkpoint.get("backup"))),
        "locked_upgrade_path": to_plain_data(locked_upgrade_path),
        "upgrade_path_progress": to_plain_data(upgrade_path_progress),
        "segment_history": to_plain_data(segment_history),
        "slurm": to_plain_data(_mapping(checkpoint.get("slurm"))),
        "mk8s": to_plain_data(
            _mapping(_mapping(phase_state).get(_EXTERNAL_NODE_TEMPLATE_PHASE_ID))
        ),
        "helm": to_plain_data(_mapping(checkpoint.get("helm"))),
        "validation": to_plain_data(validation_phase),
        "events": [to_plain_data(event) for event in events],
        "upgrade_safety": to_plain_data(upgrade_safety),
        "protected_customer_state": to_plain_data(
            _mapping(upgrade_safety.get("protected_customer_state"))
        ),
        "remediation_approvals": to_plain_data(
            upgrade_safety.get("remediation_approvals", [])
            if isinstance(upgrade_safety.get("remediation_approvals"), list)
            else []
        ),
        "post_upgrade_verification": to_plain_data(
            _mapping(upgrade_safety.get("post_upgrade_verification"))
        ),
        "fast_smoke": to_plain_data(_mapping(upgrade_safety.get("fast_smoke"))),
        "heavy_validation_followups": to_plain_data(
            upgrade_safety.get("heavy_validation_followups", [])
            if isinstance(upgrade_safety.get("heavy_validation_followups"), list)
            else []
        ),
        "zero_downtime_eligibility": to_plain_data(
            _mapping(upgrade_safety.get("zero_downtime_eligibility"))
        ),
    }
    _write_text_atomic(
        json_report_path,
        json.dumps(json_report, indent=2, sort_keys=True) + "\n",
    )
    _write_text_atomic(report_path, "\n".join(lines).rstrip() + "\n")
    current_segment_id = str(checkpoint.get("current_segment_id", "") or "").strip()
    if locked_upgrade_path and current_segment_id:
        segment_report_path, segment_json_report_path = ext_soperator_upgrade_segment_report_paths(
            config_path,
            target_ref,
            current_segment_id,
        )
        snapshot_json_report = dict(json_report)
        snapshot_json_report["markdown_report"] = str(segment_report_path)
        snapshot_json_report["json_report"] = str(segment_json_report_path)
        snapshot_json_report["latest_markdown_report"] = str(report_path)
        snapshot_json_report["latest_json_report"] = str(json_report_path)
        _write_text_atomic(
            segment_json_report_path,
            json.dumps(snapshot_json_report, indent=2, sort_keys=True) + "\n",
        )
        _write_text_atomic(segment_report_path, "\n".join(lines).rstrip() + "\n")
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
    upgrade_path_fingerprint: str = "",
    upgrade_path_segment_id: str = "",
    locked_upgrade_path: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_report_refreshed = False
    previous_source_report_fingerprint = ""
    existing_planned_phases: tuple[str, ...] = ()
    preserved_locked_upgrade_path: dict[str, Any] | None = None
    preserved_completed_segment_ids: list[str] = []
    preserved_segment_state: dict[str, Any] = {}
    incoming_locked_path: dict[str, Any] = {}
    if locked_upgrade_path:
        plain_locked_path = to_plain_data(dict(locked_upgrade_path))
        incoming_locked_path = (
            dict(plain_locked_path) if isinstance(plain_locked_path, Mapping) else {}
        )
    if existing is not None:
        if str(existing.get("target_ref", "") or "") != target_ref:
            raise RuntimeError(
                "External Soperator upgrade checkpoint belongs to a different target."
            )
        if _checkpoint_has_progress_only_locked_path(existing):
            raise RuntimeError(_locked_upgrade_path_repair_message())
        existing_locked_path = _checkpoint_locked_upgrade_path(existing)
        existing_path_fingerprint = str(
            existing.get("upgrade_path_fingerprint", "") or ""
        ).strip()
        if (
            upgrade_path_fingerprint
            and existing_path_fingerprint
            and existing_path_fingerprint != upgrade_path_fingerprint
        ):
            raise RuntimeError(
                "External Soperator upgrade checkpoint was started with a different locked "
                "upgrade path. Review the checkpoint and accepted onboarding path before "
                "executing."
            )
        if (
            _checkpoint_run_complete(existing)
            and str(existing.get("source_report_fingerprint", "") or "")
            != source_report_fingerprint
        ):
            if upgrade_path_fingerprint and existing_path_fingerprint == upgrade_path_fingerprint:
                preserved_locked_upgrade_path = copy.deepcopy(
                    existing_locked_path or incoming_locked_path
                )
                preserved_completed_segment_ids = [
                    str(segment_id or "").strip()
                    for segment_id in existing.get("completed_segment_ids", []) or []
                    if str(segment_id or "").strip()
                ]
                segment_state = to_plain_data(_mapping(existing.get("segment_state")))
                preserved_segment_state = (
                    dict(segment_state) if isinstance(segment_state, Mapping) else {}
                )
            existing = None
    if existing is not None:
        existing_source_version = str(existing.get("source_version", "") or "").strip()
        existing_target_version = str(existing.get("target_version", "") or "").strip()
        existing_planned_phases = _normalized_phase_ids(
            existing.get("planned_phases", []) or []
        )
        if str(existing.get("source_report_fingerprint", "") or "") != source_report_fingerprint:
            same_resume_contract = (
                allow_source_report_refresh
                and _same_resume_checkpoint_plan(existing_planned_phases, phase_ids)
                and (not existing_source_version or existing_source_version == source_version)
                and (not existing_target_version or existing_target_version == target_version)
            )
            if not same_resume_contract:
                raise RuntimeError(
                    "External Soperator upgrade checkpoint is stale because the source discovery bundle changed. "
                    "Review the new bundle and remove the old checkpoint before executing."
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
                "External Soperator upgrade checkpoint contains completed phase(s) that this "
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
            "upgrade_safety": upgrade_safety_checkpoint_payload(),
        }
        if preserved_locked_upgrade_path is not None:
            checkpoint["locked_upgrade_path"] = preserved_locked_upgrade_path
            checkpoint["upgrade_path_fingerprint"] = upgrade_path_fingerprint
            checkpoint["completed_segment_ids"] = list(preserved_completed_segment_ids)
            checkpoint["segment_state"] = copy.deepcopy(preserved_segment_state)
    checkpoint.setdefault("upgrade_safety", upgrade_safety_checkpoint_payload())
    checkpoint.setdefault("pending_phase", "none")
    checkpoint.setdefault("pending_reason", "")
    checkpoint.setdefault("phase_state", {})
    durable_phase_ids = _checkpoint_phase_ids_for_run(
        existing_planned_phases=existing_planned_phases,
        phase_ids=phase_ids,
    )
    if upgrade_path_fingerprint:
        locked_path = _checkpoint_locked_upgrade_path(checkpoint) or incoming_locked_path
        if not locked_path:
            raise RuntimeError(_locked_upgrade_path_repair_message())
        checkpoint["locked_upgrade_path"] = copy.deepcopy(locked_path)
        if upgrade_path_segment_id:
            segment_state = checkpoint.setdefault("segment_state", {})
            if not isinstance(segment_state, dict):
                segment_state = {}
                checkpoint["segment_state"] = segment_state
            segment_entry = segment_state.setdefault(upgrade_path_segment_id, {})
            if isinstance(segment_entry, dict):
                segment_entry.setdefault("started_at", _utc_now())
                segment_entry["source_report_fingerprint"] = source_report_fingerprint
                segment_entry["planned_phases"] = list(durable_phase_ids)
        completed_segment_ids = checkpoint.get("completed_segment_ids")
        if not isinstance(completed_segment_ids, list):
            completed_segment_ids = []
            checkpoint["completed_segment_ids"] = completed_segment_ids
        checkpoint["upgrade_path_fingerprint"] = upgrade_path_fingerprint
        checkpoint["current_segment_id"] = upgrade_path_segment_id
        checkpoint["completed_segment_ids"] = list(completed_segment_ids)
    checkpoint["updated_at"] = _utc_now()
    checkpoint["planned_phases"] = list(durable_phase_ids)
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


def _checkpoint_run_complete(checkpoint: Mapping[str, Any]) -> bool:
    if str(checkpoint.get("pending_phase", "") or "").strip() != "none":
        return False
    planned = set(_checkpoint_planned_phase_ids(checkpoint))
    completed = {
        str(phase or "").strip()
        for phase in checkpoint.get("completed_phases", []) or []
        if str(phase or "").strip()
    }
    return bool(planned) and planned <= completed


def _checkpoint_planned_phase_ids(checkpoint: Mapping[str, Any] | None) -> tuple[str, ...]:
    return _normalized_phase_ids((checkpoint or {}).get("planned_phases", []) or [])


def _normalized_phase_ids(phases: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(phase or "").strip() for phase in phases if str(phase or "").strip())


def _checkpoint_phase_ids_for_run(
    *,
    existing_planned_phases: Sequence[str],
    phase_ids: Sequence[str],
) -> tuple[str, ...]:
    incoming = _normalized_phase_ids(phase_ids)
    existing = _normalized_phase_ids(existing_planned_phases)
    if not existing:
        return incoming
    if not incoming:
        return existing
    if existing == incoming:
        return incoming
    if incoming and existing[: len(incoming)] == incoming and all(
        phase in _RESUME_OPTIONAL_PLANNED_PHASE_IDS for phase in existing[len(incoming) :]
    ):
        return existing
    if existing and incoming[: len(existing)] == existing and all(
        phase in _RESUME_OPTIONAL_PLANNED_PHASE_IDS for phase in incoming[len(existing) :]
    ):
        return incoming
    return incoming


def _same_resume_checkpoint_plan(
    existing_planned_phases: Sequence[str],
    phase_ids: Sequence[str],
) -> bool:
    incoming = _normalized_phase_ids(phase_ids)
    existing = _normalized_phase_ids(existing_planned_phases)
    if existing == incoming:
        return True
    if incoming and existing[: len(incoming)] == incoming:
        return all(
            phase in _RESUME_OPTIONAL_PLANNED_PHASE_IDS for phase in existing[len(incoming) :]
        )
    if existing and incoming[: len(existing)] == existing:
        return all(
            phase in _RESUME_OPTIONAL_PLANNED_PHASE_IDS for phase in incoming[len(existing) :]
        )
    return False


def _checkpoint_has_mutating_progress(checkpoint: Mapping[str, Any] | None) -> bool:
    phase_state = _mapping((checkpoint or {}).get("phase_state"))
    for phase_id in _MUTATING_PHASE_IDS:
        state = phase_state.get(phase_id)
        if isinstance(state, Mapping) and bool(state):
            return True
    return False


def _checkpoint_mutating_progress_started(checkpoint: Mapping[str, Any] | None) -> bool:
    completed = {
        str(phase or "").strip()
        for phase in (checkpoint or {}).get("completed_phases", []) or []
        if str(phase or "").strip()
    }
    return bool(completed & _MUTATING_PHASE_IDS) or _checkpoint_has_mutating_progress(checkpoint)


def external_soperator_upgrade_resume_backup_metadata(
    config_path: Path,
    target_ref: str,
) -> dict[str, Any] | None:
    checkpoint = _load_checkpoint(soperator_migration_checkpoint_path(config_path, target_ref))
    if (
        checkpoint is None
        or _checkpoint_run_complete(checkpoint)
        or not _checkpoint_mutating_progress_started(checkpoint)
    ):
        return None
    backup = dict(_mapping(checkpoint.get("backup")))
    if not backup:
        raise RuntimeError(
            "External Soperator upgrade checkpoint has mutating progress but no "
            "restore-capable backup metadata. Do not create a fresh backup over a "
            "partially upgraded cluster; review recovery state and remove the checkpoint "
            "only after deciding to restart."
        )
    _validate_external_upgrade_backup_metadata(
        backup,
        config_path=config_path,
        verify_archive_hash=True,
    )
    plain_backup = to_plain_data(backup)
    return dict(plain_backup) if isinstance(plain_backup, Mapping) else backup


def _external_upgrade_backup_archive_path(
    config_path: Path,
    value: Any,
) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return config_path.parent / path


def _external_upgrade_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _external_upgrade_backup_expected_size(backup: Mapping[str, Any]) -> int | None:
    raw_size = backup.get("size_bytes")
    try:
        size = int(str(raw_size or "").strip())
    except ValueError:
        return None
    return size if size > 0 else None


def _validate_external_upgrade_backup_metadata(
    backup: Mapping[str, Any],
    *,
    config_path: Path | None = None,
    verify_archive_hash: bool = False,
) -> None:
    missing_fields = [
        field
        for field in _EXTERNAL_UPGRADE_BACKUP_REQUIRED_FIELDS
        if not str(backup.get(field, "") or "").strip()
        and field != "included_categories"
    ]
    categories_value = backup.get("included_categories")
    categories = {
        str(category or "").strip()
        for category in categories_value
        if str(category or "").strip()
    } if isinstance(categories_value, Sequence) and not isinstance(
        categories_value, (str, bytes, bytearray)
    ) else set()
    if not categories:
        missing_fields.append("included_categories")
    missing_categories = sorted(_EXTERNAL_UPGRADE_BACKUP_REQUIRED_CATEGORIES - categories)
    details: list[str] = []
    if missing_fields:
        details.append("missing fields: " + ", ".join(missing_fields))
    if missing_categories:
        details.append("missing archive categories: " + ", ".join(missing_categories))
    if details:
        raise RuntimeError(
            "External Soperator upgrade backup metadata is sparse and cannot prove "
            "recreation-grade coverage before mutation: "
            + "; ".join(details)
            + ". Create a fresh ext-soperator upgrade backup with this cxcli version."
        )
    if config_path is None:
        return
    archive_path = _external_upgrade_backup_archive_path(config_path, backup.get("path"))
    if archive_path is None or not archive_path.exists():
        raise RuntimeError(
            "External Soperator upgrade backup metadata points to a missing archive. "
            "Review the checkpoint before rerunning mutation."
        )
    if not verify_archive_hash:
        return
    expected_size = _external_upgrade_backup_expected_size(backup)
    if expected_size is not None:
        actual_size = archive_path.stat().st_size
        if actual_size != expected_size:
            raise RuntimeError(
                "External Soperator upgrade backup archive size does not match checkpoint "
                "metadata. Review the backup archive and checkpoint before rerunning mutation."
            )
        return
    expected_sha = str(backup.get("sha256") or "").strip()
    actual_sha = _external_upgrade_file_sha256(archive_path)
    if actual_sha != expected_sha:
        raise RuntimeError(
            "External Soperator upgrade backup archive SHA256 does not match checkpoint "
            "metadata. Review the backup archive and checkpoint before rerunning mutation."
        )


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
        chart_text == prefix or chart_text.startswith(f"{prefix}-") for prefix in chart_prefixes
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
        namespace = str(labels.get("kustomize.toolkit.fluxcd.io/namespace", "") or "").strip()
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
            release_name,
            revision,
        ) not in exact_stale_revisions and release_name not in stale_names_without_revision:
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
    require_target_release: bool = True,
) -> tuple[str, ...]:
    retirement_lines = _retire_stale_source_soperator_helm_releases(
        command_runner=command_runner,
        kube_context=kube_context,
        target_version=target_version,
    )
    target_releases = [
        release
        for release in list_helm_releases(
            command_runner=command_runner,
            kube_context=kube_context,
            namespace=_SOPERATOR_NAMESPACE,
            filter_regex=f"^{re.escape(_SOPERATOR_TARGET_RELEASE_NAME)}$",
        )
        if release.name == _SOPERATOR_TARGET_RELEASE_NAME
    ]
    if require_target_release or target_releases:
        target_readiness = verify_helm_chart_ready(
            command_runner=command_runner,
            kube_context=kube_context,
            release_name=_SOPERATOR_TARGET_RELEASE_NAME,
            namespace=_SOPERATOR_NAMESPACE,
            expected_version=target_version,
        )
        target_line = (
            "Verified target Soperator Helm chart readiness: " + target_readiness.summary()
        )
    else:
        target_line = (
            "Skipped target Soperator Helm chart readiness: no target Soperator Helm release "
            "is installed or expected for this adopted external upgrade segment."
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
            + "\nStale source Soperator Helm releases remain after upgrade:\n"
            + "\n".join(stale_lines)
            + "\nRetire or remove the remaining old source releases before considering "
            "the upgrade complete."
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
            + "\nActive old source Flux HelmReleases remain after upgrade:\n"
            + "\n".join(active_lines)
            + "\nSuspend or remove the remaining old source desired state before considering "
            "the upgrade complete."
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
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
) -> bool:
    onboarding = _target_onboarding(payload, target_ref)
    target = _external_node_template_target(onboarding)
    cluster_id = _external_migration_cluster_id(
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        nebius_api=nebius_api,
    )
    cluster = _cluster_payload_by_id(nebius_api=nebius_api, cluster_id=cluster_id)
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
            nebius_api=nebius_api,
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
            f"{name}: status not returned by Nebius API; rollout readiness cannot be verified",
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
            f"{summary}; status missing {', '.join(missing)}, rollout readiness cannot be verified",
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
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
) -> list[str]:
    cluster_id = _external_migration_cluster_id(
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        nebius_api=nebius_api,
    )
    cluster = _cluster_payload_by_id(nebius_api=nebius_api, cluster_id=cluster_id)
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
            nebius_api=nebius_api,
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
            nebius_api=nebius_api,
            node_group_id=node_group_id,
        )
        ready, readiness_summary = _node_group_readiness_summary(node_group)
        if not ready:
            errors.append(f"{group_name}: node group rollout is not ready: {readiness_summary}")
            continue
        verified_groups += 1
    if errors:
        raise RuntimeError(
            "External Soperator upgrade MK8s verification failed after execute:\n"
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
    nebius_api: SoperatorMigrationNebiusApi,
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
            nebius_api=nebius_api,
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
            "External Soperator upgrade MK8s node-template verification failed after execute:\n"
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
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
) -> bool:
    project_id = _nebius_project_id(payload)
    specs = _aligned_filesystem_specs(payload=payload, target_ref=target_ref)
    specs_by_key = {spec.key: spec for spec in specs}
    filesystem_ids_by_key: dict[str, str] = {}
    for spec in specs:
        filesystem = _get_filesystem_by_name(
            nebius_api=nebius_api,
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
            nebius_api=nebius_api,
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
        if str(_mapping(nodeset.get("status")).get("phase", "") or "").strip() != "Ready":
            return False
    return True


def _populate_jail_refresh_satisfied(
    *,
    checkpoint: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> bool:
    phase = _mapping(_mapping(checkpoint.get("phase_state")).get(POPULATE_JAIL_REFRESH_PHASE_ID))
    result = _mapping(phase.get("result"))
    status = str(result.get("status", "") or "").strip()
    if status == "skipped":
        return True
    if status != "refreshed" or result.get("maintenance_restored") is not True:
        return False
    snapshot = inspect_populate_jail(
        command_runner,
        namespace=_SOPERATOR_NAMESPACE,
        target_ref=target_ref,
        kube_context=kube_context,
    )
    expected_image = str(result.get("job_image") or result.get("target_image") or "").strip()
    if not snapshot.job_complete:
        return False
    return not expected_image or snapshot.job_image == expected_image


def _record_phase_fast_verification(
    *,
    checkpoint: dict[str, Any],
    phase_id: str,
    status: str,
    summary: str,
    checks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    payload = build_stage_fast_verification_payload(
        phase_id=phase_id,
        status=status,
        summary=summary,
        checks=checks,
    )
    phase = _phase_state(checkpoint, phase_id)
    phase["fast_verification"] = payload
    event_status = (
        "failed" if status == "failed" else "skipped" if status == "skipped" else "passed"
    )
    _append_event(
        checkpoint,
        "execute-phase-fast-verification-" + event_status,
        phase=phase_id,
        status=status,
        summary=summary,
    )
    return payload


def _phase_validation_summary_line(phase_id: str, verification: Mapping[str, Any]) -> str:
    return (
        f"Phase validation {phase_id}: "
        f"{status_label(str(verification.get('status', '') or 'not_run'))} - "
        f"{verification.get('summary') or 'No summary recorded.'}"
    )


def _fast_verification_check(name: str, status: str, summary: str) -> dict[str, str]:
    return stage_fast_verification_check(name, status, summary)


def _fast_verification_failed(checks: Sequence[Mapping[str, Any]]) -> bool:
    return stage_fast_verification_failed(checks)


def _online_bulk_data_sync_fast_verification_checks(
    *,
    checkpoint: Mapping[str, Any],
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[str, list[Mapping[str, str]]]:
    phase = _mapping(_mapping(checkpoint.get("phase_state")).get("online-bulk-data-sync"))
    skipped_reason = str(phase.get("skipped_reason", "") or "").strip()
    if skipped_reason:
        return (
            f"data sync skipped as expected: {skipped_reason}.",
            [
                _fast_verification_check(
                    "Data sync planning",
                    "skipped",
                    skipped_reason,
                )
            ],
        )
    jobs = _mapping(phase.get("jobs"))
    if not jobs:
        return (
            "data sync did not record any copy jobs or skip reason.",
            [
                _fast_verification_check(
                    "Data sync jobs",
                    "failed",
                    "no copy jobs or skip reason recorded",
                )
            ],
        )
    checks: list[Mapping[str, str]] = []
    completed = 0
    skipped = 0
    for key, raw_job in sorted(jobs.items()):
        job_state = _mapping(raw_job)
        if bool(job_state.get("skipped")):
            skipped += 1
            checks.append(
                _fast_verification_check(
                    f"Data sync {key}",
                    "skipped",
                    "source and target PVC already match",
                )
            )
            continue
        job_name = _copy_job_name_for_storage_key(str(key))
        exists, job = _kubectl_get_namespace_resource(
            command_runner=command_runner,
            kube_context=kube_context,
            resource=f"job/{job_name}",
        )
        if not exists:
            checks.append(
                _fast_verification_check(
                    f"Data sync {key}",
                    "failed",
                    f"copy Job {job_name} was not found",
                )
            )
            continue
        if _job_condition_true(job, "Failed"):
            checks.append(
                _fast_verification_check(
                    f"Data sync {key}",
                    "failed",
                    f"copy Job {job_name} reports Failed=True",
                )
            )
            continue
        if not _job_condition_true(job, "Complete"):
            checks.append(
                _fast_verification_check(
                    f"Data sync {key}",
                    "failed",
                    f"copy Job {job_name} has not completed",
                )
            )
            continue
        completed += 1
        checks.append(
            _fast_verification_check(
                f"Data sync {key}",
                "passed",
                f"copy Job {job_name} completed",
            )
        )
    if _fast_verification_failed(checks):
        return "one or more data sync jobs are missing, failed, or incomplete.", checks
    return f"data sync jobs verified complete: {completed}; skipped: {skipped}.", checks


def _rolling_compute_fast_verification_checks(
    *,
    checkpoint: Mapping[str, Any],
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    command_runner: SoperatorMigrationCommandRunner,
) -> tuple[str, list[Mapping[str, str]]]:
    phase = _mapping(_mapping(checkpoint.get("phase_state")).get("rolling-compute-migration"))
    skipped_reason = str(phase.get("skipped_reason", "") or "").strip()
    if skipped_reason:
        return (
            f"compute migration skipped as expected: {skipped_reason}.",
            [
                _fast_verification_check(
                    "Compute migration planning",
                    "skipped",
                    skipped_reason,
                )
            ],
        )
    checks: list[Mapping[str, str]] = []
    try:
        _kubectl_rollout_status(
            command_runner=command_runner,
            kube_context=kube_context,
            namespace=_SOPERATOR_NAMESPACE,
            resource="deployment/soperator-manager",
            timeout="2m",
        )
    except RuntimeError as exc:
        checks.append(
            _fast_verification_check(
                "Target Soperator manager rollout",
                "failed",
                str(exc).strip() or "rollout status failed",
            )
        )
    else:
        checks.append(
            _fast_verification_check(
                "Target Soperator manager rollout",
                "passed",
                "deployment/soperator-manager is rolled out",
            )
        )
    values = _patch_target_values_for_compute(
        checkpoint=checkpoint,
        payload=payload,
        target_ref=target_ref,
        source_report=source_report,
        live_snapshot=live_snapshot,
    )
    names = _target_worker_nodeset_names(values)
    for name in names:
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
            checks.append(
                _fast_verification_check(
                    f"Worker NodeSet {name}",
                    "failed",
                    "target worker NodeSet was not found",
                )
            )
            continue
        ready, detail = _worker_nodeset_ready_state(nodeset)
        checks.append(
            _fast_verification_check(
                f"Worker NodeSet {name}",
                "passed" if ready else "failed",
                detail,
            )
        )
    if _fast_verification_failed(checks):
        return "target Soperator manager or worker NodeSets are not ready.", checks
    return f"target Soperator manager and worker NodeSets are ready: {len(names)}.", checks


def _validation_hold_fast_verification_checks(
    *,
    checkpoint: Mapping[str, Any],
) -> tuple[str, list[Mapping[str, str]]]:
    phase = _mapping(_mapping(checkpoint.get("phase_state")).get("validation-and-rollback-hold"))
    revision = _validation_hold_revision(checkpoint)
    checks: list[Mapping[str, str]] = [
        _fast_verification_check(
            "Validation hold contract revision",
            "passed" if revision >= _VALIDATION_HOLD_REVISION else "failed",
            f"recorded={revision}, expected={_VALIDATION_HOLD_REVISION}",
        )
    ]
    safety = _mapping(phase.get("shared_safety_verification"))
    safety_status = str(safety.get("status", "") or "not_run")
    checks.append(
        _fast_verification_check(
            "Shared upgrade safety",
            "passed" if safety.get("passed") is True else "failed",
            f"status={safety_status}",
        )
    )
    soperator_count = _positive_int(phase.get("soperator_cluster_validation_count"), fallback=0)
    soperator_reports = [
        str(path)
        for path in phase.get("soperator_cluster_validation_reports", []) or []
        if str(path).strip()
    ]
    soperator_status = (
        "passed" if soperator_count > 0 and len(soperator_reports) >= soperator_count else "failed"
    )
    if soperator_count == 0:
        soperator_status = "skipped"
    checks.append(
        _fast_verification_check(
            "Soperator deployment snapshot report",
            soperator_status,
            f"reports={len(soperator_reports)}, expected={soperator_count}",
        )
    )
    gpu_count = _positive_int(phase.get("mk8s_gpu_validation_count"), fallback=0)
    gpu_reports = [
        str(path)
        for path in phase.get("mk8s_gpu_validation_reports", []) or []
        if str(path).strip()
    ]
    gpu_status = "passed" if len(gpu_reports) >= gpu_count else "failed"
    if gpu_count == 0:
        gpu_status = "skipped"
    checks.append(
        _fast_verification_check(
            "Configured MK8s GPU validation reports",
            gpu_status,
            f"reports={len(gpu_reports)}, expected={gpu_count}",
        )
    )
    if _fast_verification_failed(checks):
        return "validation hold artifacts are incomplete or failed.", checks
    return "validation hold artifacts and shared safety result are recorded.", checks


def _retire_old_resources_fast_verification_checks(
    *,
    checkpoint: Mapping[str, Any],
) -> tuple[str, list[Mapping[str, str]]]:
    phase = _mapping(_mapping(checkpoint.get("phase_state")).get("retire-old-resources"))
    skipped_reason = str(phase.get("skipped_reason", "") or "").strip()
    if skipped_reason:
        return (
            f"old-resource retirement skipped as expected: {skipped_reason}.",
            [
                _fast_verification_check(
                    "Old resource retirement",
                    "skipped",
                    skipped_reason,
                )
            ],
        )
    retired = _sequence_of_mappings(phase.get("retired_node_groups"))
    if retired:
        summary = f"retired replaced node groups: {len(retired)}."
        storage_retirement = str(phase.get("storage_retirement", "") or "").strip()
        if storage_retirement:
            summary += f" storage_retirement={storage_retirement}."
        return (
            summary,
            [
                _fast_verification_check(
                    "Replaced node-group retirement",
                    "passed",
                    summary,
                )
            ],
        )
    return (
        "old-resource retirement recorded neither retired node groups nor an expected skip.",
        [
            _fast_verification_check(
                "Old resource retirement",
                "failed",
                "no retired node groups or skip reason recorded",
            )
        ],
    )


def _run_external_upgrade_phase_fast_verification(
    *,
    checkpoint: dict[str, Any],
    phase_id: str,
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    live_snapshot: Mapping[str, Any],
    target_ref: str,
    kube_context: str,
    worker_node_groups: Sequence[str],
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
) -> list[str]:
    try:
        if phase_id == _EXTERNAL_NODE_TEMPLATE_PHASE_ID:
            verification_lines = _verify_completed_soperator_migration_mk8s_state(
                payload=payload,
                source_report=source_report,
                target_ref=target_ref,
                worker_node_groups=worker_node_groups,
                phase_ids=(phase_id,),
                nebius_api=nebius_api,
                command_runner=command_runner,
            )
            summary = verification_lines[0] if verification_lines else "node-template verified."
            checks = [
                _fast_verification_check(
                    "External MK8s node-template",
                    "passed",
                    summary,
                )
            ]
        elif phase_id == _TARGET_GPU_STACK_PHASE_ID:
            satisfied = _target_gpu_stack_remediation_satisfied(
                payload=payload,
                target_ref=target_ref,
                kube_context=kube_context,
                command_runner=command_runner,
            )
            summary = (
                "target GPU stack Helm releases and post-render patches are applied."
                if satisfied
                else "target GPU stack Helm releases or post-render patches are not ready."
            )
            checks = [
                _fast_verification_check(
                    "Target GPU stack",
                    "passed" if satisfied else "failed",
                    summary,
                )
            ]
        elif phase_id == "create-aligned-sfs":
            satisfied = _create_aligned_sfs_satisfied(
                payload=payload,
                source_report=source_report,
                target_ref=target_ref,
                worker_node_groups=worker_node_groups,
                nebius_api=nebius_api,
                command_runner=command_runner,
            )
            summary = (
                "aligned SFS filesystems and source node-group attachments are present."
                if satisfied
                else "aligned SFS filesystems or source node-group attachments are incomplete."
            )
            checks = [
                _fast_verification_check(
                    "Aligned SFS",
                    "passed" if satisfied else "failed",
                    summary,
                )
            ]
        elif phase_id == "online-bulk-data-sync":
            summary, checks = _online_bulk_data_sync_fast_verification_checks(
                checkpoint=checkpoint,
                kube_context=kube_context,
                command_runner=command_runner,
            )
        elif phase_id == "rolling-compute-migration":
            summary, checks = _rolling_compute_fast_verification_checks(
                checkpoint=checkpoint,
                payload=payload,
                source_report=source_report,
                live_snapshot=live_snapshot,
                target_ref=target_ref,
                kube_context=kube_context,
                command_runner=command_runner,
            )
        elif phase_id == "final-control-plane-cutover":
            phase = _mapping(_mapping(checkpoint.get("phase_state")).get(phase_id))
            skipped_reason = str(phase.get("skipped_reason", "") or "").strip()
            if skipped_reason:
                summary = f"final cutover skipped as expected: {skipped_reason}."
                checks = [
                    _fast_verification_check(
                        "Final control-plane cutover",
                        "skipped",
                        skipped_reason,
                    )
                ]
            else:
                satisfied = _final_cutover_satisfied(
                    checkpoint=checkpoint,
                    payload=payload,
                    source_report=source_report,
                    live_snapshot=live_snapshot,
                    target_ref=target_ref,
                    kube_context=kube_context,
                    command_runner=command_runner,
                )
                summary = (
                    "target SlurmCluster is Available and expected NodeSets are Ready."
                    if satisfied
                    else "target SlurmCluster is not Available or expected NodeSets are not Ready."
                )
                checks = [
                    _fast_verification_check(
                        "Final control-plane cutover",
                        "passed" if satisfied else "failed",
                        summary,
                    )
                ]
        elif phase_id == POPULATE_JAIL_REFRESH_PHASE_ID:
            phase = _mapping(_mapping(checkpoint.get("phase_state")).get(phase_id))
            result = _mapping(phase.get("result"))
            status = str(result.get("status", "") or "").strip()
            if status == "skipped":
                summary = (
                    f"Jail Upgrade skipped as expected: {result.get('reason') or 'not required'}."
                )
                checks = [
                    _fast_verification_check(
                        "Jail Upgrade",
                        "skipped",
                        str(result.get("reason") or "not required"),
                    )
                ]
            else:
                snapshot = inspect_populate_jail(
                    command_runner,
                    namespace=_SOPERATOR_NAMESPACE,
                    target_ref=target_ref,
                    kube_context=kube_context,
                )
                expected_image = str(
                    result.get("job_image") or result.get("target_image") or ""
                ).strip()
                image_ok = not expected_image or snapshot.job_image == expected_image
                maintenance_restored = result.get("maintenance_restored") is True
                checks = [
                    _fast_verification_check(
                        "Jail Upgrade Job completion",
                        "passed" if snapshot.job_complete else "failed",
                        f"job={snapshot.job_name or 'unknown'}",
                    ),
                    _fast_verification_check(
                        "Jail Upgrade image",
                        "passed" if image_ok else "failed",
                        f"expected={expected_image or 'unknown'}, actual={snapshot.job_image or 'unknown'}",
                    ),
                    _fast_verification_check(
                        "Jail Upgrade maintenance restore",
                        "passed" if maintenance_restored else "failed",
                        "steady-state maintenance values were reapplied"
                        if maintenance_restored
                        else "steady-state maintenance restoration was not recorded",
                    ),
                ]
                summary = (
                    f"Jail Upgrade verified with image {snapshot.job_image or expected_image}."
                    if not _fast_verification_failed(checks)
                    else "Jail Upgrade is incomplete or not using the target image."
                )
        elif phase_id == "validation-and-rollback-hold":
            summary, checks = _validation_hold_fast_verification_checks(checkpoint=checkpoint)
        elif phase_id == "retire-old-resources":
            summary, checks = _retire_old_resources_fast_verification_checks(checkpoint=checkpoint)
        else:
            summary = "no fast verifier is registered for this stage."
            checks = [
                _fast_verification_check(
                    "Fast stage verifier",
                    "failed",
                    summary,
                )
            ]
    except Exception as exc:
        summary = str(exc).strip() or "fast stage verification failed"
        checks = [
            _fast_verification_check(
                _STATUS_PHASE_LABELS.get(phase_id, phase_id),
                "failed",
                summary,
            )
        ]
    status = stage_fast_verification_status(checks)
    payload = _record_phase_fast_verification(
        checkpoint=checkpoint,
        phase_id=phase_id,
        status=status,
        summary=summary,
        checks=checks,
    )
    if status == "failed":
        raise SoperatorMigrationPhasePending(
            f"fast stage verification failed after {phase_id}: {summary}"
        )
    return [_phase_validation_summary_line(phase_id, payload)]


def _fast_check_result_satisfied(result: tuple[str, list[Mapping[str, str]]]) -> bool:
    _summary, checks = result
    return not _fast_verification_failed(checks)


def _completed_phase_probe_satisfied(probe: Callable[[], Any]) -> bool:
    try:
        probe()
    except Exception:
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
    nebius_api: SoperatorMigrationNebiusApi,
    command_runner: SoperatorMigrationCommandRunner,
) -> list[str]:
    require_target_soperator_helm = _target_soperator_helm_release_required(
        _target_onboarding(payload, target_ref)
    )
    checks: Mapping[str, Callable[[], bool]] = {
        _EXTERNAL_NODE_TEMPLATE_PHASE_ID: lambda: _external_node_template_upgrade_satisfied(
            payload=payload,
            source_report=source_report,
            target_ref=target_ref,
            worker_node_groups=worker_node_groups,
            nebius_api=nebius_api,
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
            nebius_api=nebius_api,
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
        "online-bulk-data-sync": lambda: _fast_check_result_satisfied(
            _online_bulk_data_sync_fast_verification_checks(
                checkpoint=checkpoint,
                kube_context=kube_context,
                command_runner=command_runner,
            )
        ),
        "rolling-compute-migration": lambda: _fast_check_result_satisfied(
            _rolling_compute_fast_verification_checks(
                checkpoint=checkpoint,
                payload=payload,
                source_report=source_report,
                live_snapshot=live_snapshot,
                target_ref=target_ref,
                kube_context=kube_context,
                command_runner=command_runner,
            )
        ),
        POPULATE_JAIL_REFRESH_PHASE_ID: lambda: _populate_jail_refresh_satisfied(
            checkpoint=checkpoint,
            target_ref=target_ref,
            kube_context=kube_context,
            command_runner=command_runner,
        ),
        "validation-and-rollback-hold": lambda: _fast_check_result_satisfied(
            _validation_hold_fast_verification_checks(checkpoint=checkpoint)
        ),
        "retire-old-resources": lambda: _fast_check_result_satisfied(
            _retire_old_resources_fast_verification_checks(checkpoint=checkpoint)
        ),
        "post-upgrade-mk8s-check": lambda: _completed_phase_probe_satisfied(
            lambda: _verify_completed_soperator_migration_mk8s_state(
                payload=payload,
                source_report=source_report,
                target_ref=target_ref,
                worker_node_groups=worker_node_groups,
                phase_ids=phase_ids,
                nebius_api=nebius_api,
                command_runner=command_runner,
            )
        ),
        "post-upgrade-helm-check": lambda: _completed_phase_probe_satisfied(
            lambda: _verify_completed_soperator_migration_helm_state(
                command_runner=command_runner,
                kube_context=kube_context,
                target_version=str(
                    _target_onboarding(payload, target_ref).get("target_version", "") or ""
                ),
                require_target_release=require_target_soperator_helm,
            )
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
    backup_metadata: Mapping[str, Any] | None = None,
    snapshot_collector: Callable[..., Mapping[str, Any]],
    approved: bool = False,
    approve_remediation: bool = False,
    nebius_api: SoperatorMigrationNebiusApi | None = None,
    command_runner: SoperatorMigrationCommandRunner | None = None,
    status_callback: Callable[[str], None] | None = None,
    status_poll_interval_seconds: float = 30.0,
    job_policy: str | None = None,
    populate_jail_refresh: str = "auto",
    jail_persistent_mounts: Sequence[str] = (),
    cancel_job_ids: Sequence[str] = (),
    requeue_job_ids: Sequence[str] = (),
    job_wait_timeout_seconds: int = _EXTERNAL_UPGRADE_DEFAULT_JOB_WAIT_TIMEOUT_SECONDS,
    job_refresh_interval_seconds: int = _EXTERNAL_UPGRADE_DEFAULT_JOB_REFRESH_INTERVAL_SECONDS,
    login_session_policy: str = EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY,
    login_session_drain_timeout_seconds: int = EXTERNAL_LOGIN_SESSION_DRAIN_TIMEOUT_SECONDS,
    jail_sfs_resize_handler: JailSfsResizeHandler | None = None,
    worker_rollout_strategy: str | None = None,
    worker_wave_groups: int | None = None,
    worker_wave_percent: int | None = None,
    max_parallel_worker_groups: int | None = None,
    strategy_max_surge_count: int | None = None,
    strategy_max_unavailable_count: int | None = None,
    strategy_drain_timeout: str | None = None,
    upgrade_path_fingerprint: str = "",
    upgrade_path_segment_id: str = "",
    locked_upgrade_path: Mapping[str, Any] | None = None,
) -> SoperatorMigrationExecutionResult:
    """Run checkpointed live external Soperator upgrade phases."""

    normalized_target = normalize_component_token(target_ref)
    if not normalized_target:
        raise RuntimeError("External Soperator upgrade execute requires a target ref.")
    legacy_checkpoint_path = legacy_soperator_migration_checkpoint_path(
        config_path,
        normalized_target,
    )
    if legacy_checkpoint_path.exists():
        raise RuntimeError(
            "Retired external Soperator checkpoint found: "
            f"{legacy_checkpoint_path}. Remove the old checkpoint after reviewing it; "
            "`nebius-cxcli ext-soperator upgrade` does not resume retired checkpoints."
        )
    with SoperatorMigrationExecutionLock(
        soperator_migration_lock_path(config_path, normalized_target)
    ):
        active_command_runner = command_runner or _default_command_runner
        attached_nebius_api = getattr(active_command_runner, "nebius_api", None)
        owned_nebius_api: SoperatorMigrationNebiusApi | None = None
        active_nebius_api = nebius_api or attached_nebius_api
        if active_nebius_api is None:
            owned_nebius_api = _SdkSoperatorMigrationNebiusApi(
                project_id=_nebius_project_id(payload)
            )
            active_nebius_api = owned_nebius_api
        try:
            return _execute_soperator_migration_unlocked(
                config_path=config_path,
                target_ref=normalized_target,
                payload=payload,
                source_report=source_report,
                backup_metadata=backup_metadata,
                snapshot_collector=snapshot_collector,
                approved=approved,
                approve_remediation=approve_remediation,
                nebius_api=active_nebius_api,
                command_runner=active_command_runner,
                status_callback=status_callback,
                status_poll_interval_seconds=status_poll_interval_seconds,
                job_policy=job_policy,
                populate_jail_refresh=populate_jail_refresh,
                jail_persistent_mounts=jail_persistent_mounts,
                cancel_job_ids=cancel_job_ids,
                requeue_job_ids=requeue_job_ids,
                job_wait_timeout_seconds=job_wait_timeout_seconds,
                job_refresh_interval_seconds=job_refresh_interval_seconds,
                login_session_policy=login_session_policy,
                login_session_drain_timeout_seconds=login_session_drain_timeout_seconds,
                jail_sfs_resize_handler=jail_sfs_resize_handler,
                worker_rollout_strategy=worker_rollout_strategy,
                worker_wave_groups=worker_wave_groups,
                worker_wave_percent=worker_wave_percent,
                max_parallel_worker_groups=max_parallel_worker_groups,
                strategy_max_surge_count=strategy_max_surge_count,
                strategy_max_unavailable_count=strategy_max_unavailable_count,
                strategy_drain_timeout=strategy_drain_timeout,
                upgrade_path_fingerprint=upgrade_path_fingerprint,
                upgrade_path_segment_id=upgrade_path_segment_id,
                locked_upgrade_path=locked_upgrade_path,
            )
        finally:
            if owned_nebius_api is not None:
                owned_nebius_api.close()


def _execute_soperator_migration_unlocked(
    *,
    config_path: Path,
    target_ref: str,
    payload: Mapping[str, Any],
    source_report: Mapping[str, Any],
    backup_metadata: Mapping[str, Any] | None = None,
    snapshot_collector: Callable[..., Mapping[str, Any]],
    approved: bool = False,
    approve_remediation: bool = False,
    nebius_api: SoperatorMigrationNebiusApi | None = None,
    command_runner: SoperatorMigrationCommandRunner | None = None,
    status_callback: Callable[[str], None] | None = None,
    status_poll_interval_seconds: float = 30.0,
    job_policy: str | None = None,
    populate_jail_refresh: str = "auto",
    jail_persistent_mounts: Sequence[str] = (),
    cancel_job_ids: Sequence[str] = (),
    requeue_job_ids: Sequence[str] = (),
    job_wait_timeout_seconds: int = _EXTERNAL_UPGRADE_DEFAULT_JOB_WAIT_TIMEOUT_SECONDS,
    job_refresh_interval_seconds: int = _EXTERNAL_UPGRADE_DEFAULT_JOB_REFRESH_INTERVAL_SECONDS,
    login_session_policy: str = EXTERNAL_LOGIN_SESSION_POLICY_TARGET_READY,
    login_session_drain_timeout_seconds: int = EXTERNAL_LOGIN_SESSION_DRAIN_TIMEOUT_SECONDS,
    jail_sfs_resize_handler: JailSfsResizeHandler | None = None,
    worker_rollout_strategy: str | None = None,
    worker_wave_groups: int | None = None,
    worker_wave_percent: int | None = None,
    max_parallel_worker_groups: int | None = None,
    strategy_max_surge_count: int | None = None,
    strategy_max_unavailable_count: int | None = None,
    strategy_drain_timeout: str | None = None,
    upgrade_path_fingerprint: str = "",
    upgrade_path_segment_id: str = "",
    locked_upgrade_path: Mapping[str, Any] | None = None,
) -> SoperatorMigrationExecutionResult:
    normalized_target = normalize_component_token(target_ref)
    if not normalized_target:
        raise RuntimeError("External Soperator upgrade execute requires a target ref.")
    active_command_runner = command_runner or _default_command_runner
    attached_nebius_api = getattr(active_command_runner, "nebius_api", None)
    active_nebius_api = nebius_api or attached_nebius_api
    if active_nebius_api is None:
        raise RuntimeError("External Soperator upgrade execution requires a Nebius API adapter.")
    try:
        resolved_login_session_policy = normalize_external_login_session_policy(
            login_session_policy
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if login_session_drain_timeout_seconds < 0:
        raise RuntimeError("--login-session-drain-timeout must be non-negative.")

    def _emit_phase_comment(phase_id: str, comment: str) -> None:
        if status_callback is None:
            return
        status_callback(
            f"External Soperator upgrade phase {phase_id}: {comment}"
        )

    _emit_phase_comment(
        "execute-preflight",
        "verifying onboarding, live source contract, checkpoint, and rollout plan.",
    )
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
            "Soperator source discovery bundle is missing its analysis fingerprint. "
            "Rerun `nebius-cxcli ext-soperator onboard` before executing the upgrade."
        )
    expected_source_contract = _execution_source_contract(source_snapshot)
    expected_source_contract_fingerprint = _fingerprint(expected_source_contract)
    target_version = str(
        onboarding.get("target_version", "") or report.get("target_version", "") or ""
    )
    kube_context = _target_kube_context(payload, normalized_target)
    actions = _onboarding_actions(onboarding)
    require_target_soperator_helm = _target_soperator_helm_release_required(onboarding)
    resolved_populate_jail_refresh = normalize_populate_jail_refresh_mode(
        populate_jail_refresh
    )
    phase_ids = _phase_ids_for_actions(
        report=report,
        onboarding=onboarding,
        populate_jail_refresh=resolved_populate_jail_refresh,
    )
    if not phase_ids:
        phase_ids = ("discovery-and-plan",)
    explicit_mount_requested = any(
        str(item or "").strip() for item in jail_persistent_mounts
    )
    jail_rootfs = report.get("jail_rootfs")
    jail_rootfs_refresh_required = (
        isinstance(jail_rootfs, Mapping) and jail_rootfs.get("refresh_required") is True
    )
    explicit_populate_jail_refresh = resolved_populate_jail_refresh in {"force", "manual"}
    auto_persistent_mounts = (
        explicit_mount_requested
        or explicit_populate_jail_refresh
        or jail_rootfs_refresh_required
    )
    payload, persistent_mount_state = _prepare_jail_persistent_mount_payload(
        payload=payload,
        target_ref=normalized_target,
        jail_persistent_mounts=jail_persistent_mounts,
        populate_jail_refresh=populate_jail_refresh,
        auto_persistent_mounts=auto_persistent_mounts,
    )
    onboarding = _target_onboarding(payload, normalized_target)
    actions = _onboarding_actions(onboarding)
    require_target_soperator_helm = _target_soperator_helm_release_required(onboarding)
    phase_ids = _phase_ids_with_jail_persistent_mount_prerequisites(
        phase_ids=phase_ids,
        payload=payload,
        target_ref=normalized_target,
    )

    checkpoint_path = soperator_migration_checkpoint_path(config_path, normalized_target)
    existing_checkpoint = _load_checkpoint(checkpoint_path)
    completed_prior_run = (
        existing_checkpoint is not None
        and _checkpoint_run_complete(existing_checkpoint)
        and str(existing_checkpoint.get("source_report_fingerprint", "") or "")
        != source_report_fingerprint
    )
    resume_checkpoint = None if completed_prior_run else existing_checkpoint
    mutating_progress_started = _checkpoint_mutating_progress_started(resume_checkpoint)
    if mutating_progress_started:
        checkpoint_phase_ids = _checkpoint_planned_phase_ids(resume_checkpoint)
        if checkpoint_phase_ids:
            unsupported_planned = sorted(set(checkpoint_phase_ids) - _SUPPORTED_EXECUTE_PHASE_IDS)
            if unsupported_planned:
                raise RuntimeError(
                    "External Soperator upgrade checkpoint contains planned phase(s) that this "
                    "executor cannot resume safely: "
                    + ", ".join(unsupported_planned)
                    + ". Review or remove the checkpoint before executing."
                )
            phase_ids = checkpoint_phase_ids
    requires_compute_executor = (
        ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION in actions
        or ONBOARDING_ACTION_UPGRADE_SOPERATOR in actions
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
            "Rerun `nebius-cxcli ext-soperator onboard` before executing the upgrade."
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
            "Rerun `nebius-cxcli ext-soperator onboard` before executing the upgrade."
        )

    execution_source_report = _source_report_with_execution_inventory(
        source_report=source_report,
        payload=payload,
        target_ref=normalized_target,
        kube_context=kube_context,
        nebius_api=active_nebius_api,
        command_runner=active_command_runner,
    )
    checkpoint = _checkpoint_for_run(
        existing=existing_checkpoint,
        target_ref=normalized_target,
        source_report_fingerprint=source_report_fingerprint,
        source_version=expected_source_version or live_source_version,
        target_version=target_version,
        phase_ids=phase_ids,
        # Premutation runs passed strict live source-contract validation above;
        # mutation resumes remain phase-checkpoint based. Refresh still requires
        # the same source version, target version, and phase plan.
        allow_source_report_refresh=True,
        upgrade_path_fingerprint=upgrade_path_fingerprint,
        upgrade_path_segment_id=upgrade_path_segment_id,
        locked_upgrade_path=locked_upgrade_path,
    )
    checkpoint_phase_ids = _checkpoint_planned_phase_ids(checkpoint)
    if checkpoint_phase_ids:
        phase_ids = checkpoint_phase_ids
    existing_mounts = dict(_mapping(checkpoint.get("persistent_jail_mounts")))
    checkpoint["persistent_jail_mounts"] = {
        **existing_mounts,
        **dict(to_plain_data(persistent_mount_state)),
    }
    resolved_job_policy = _external_upgrade_job_policy(
        job_policy,
        default_policy="fail",
        allow_resolved_interactive=job_policy == "interactive",
    )
    allow_resolved_interactive_job_policy = resolved_job_policy == "interactive"
    if resolved_job_policy == "wait-then-cancel" and job_wait_timeout_seconds <= 0:
        raise RuntimeError(
            "--job-policy wait-then-cancel requires a positive --job-wait-timeout. "
            "Use --job-policy wait-to-finish --job-wait-timeout 0s for an unlimited wait."
        )
    selected_cancel_job_ids = tuple(
        str(job_id or "").strip() for job_id in cancel_job_ids if str(job_id or "").strip()
    )
    selected_requeue_job_ids = tuple(
        str(job_id or "").strip() for job_id in requeue_job_ids if str(job_id or "").strip()
    )
    backup_state = dict(_mapping(checkpoint.get("backup")))
    incoming_backup: dict[str, Any] = {}
    if backup_metadata:
        plain_backup = to_plain_data(dict(backup_metadata))
        incoming_backup = dict(plain_backup) if isinstance(plain_backup, Mapping) else {}
    if mutating_progress_started:
        if not backup_state:
            raise RuntimeError(
                "External Soperator upgrade checkpoint has mutating progress but no "
                "restore-capable backup metadata. Do not create a fresh backup over a "
                "partially upgraded cluster; review recovery state and remove the checkpoint "
                "only after deciding to restart."
            )
        if incoming_backup and incoming_backup != backup_state:
            _append_event(
                checkpoint,
                "backup-metadata-preserved",
                reason="existing pre-mutation backup metadata kept during resume",
            )
    elif incoming_backup:
        backup_state.update(incoming_backup)
    checkpoint["backup"] = backup_state
    if incoming_backup:
        checkpoint["updated_at"] = _utc_now()
        _append_event(checkpoint, "backup-metadata-checkpointed")
        _write_checkpoint(checkpoint_path, checkpoint)
    slurm_state = dict(_mapping(checkpoint.get("slurm")))
    slurm_state.update(
        {
            "job_policy": resolved_job_policy,
            "cancel_job_ids": list(selected_cancel_job_ids),
            "requeue_job_ids": list(selected_requeue_job_ids),
            "wait_timeout_seconds": job_wait_timeout_seconds,
            "refresh_interval_seconds": job_refresh_interval_seconds,
        }
    )
    checkpoint["slurm"] = slurm_state
    checkpoint["login_continuity"] = {
        **dict(_mapping(checkpoint.get("login_continuity"))),
        "session_policy": resolved_login_session_policy,
        "session_drain_timeout_seconds": login_session_drain_timeout_seconds,
        "guarantee": (
            "new SSH connections require a ready target login endpoint before source "
            "login retirement; existing TCP sessions are best-effort unless session "
            "drain is selected"
        ),
    }
    checkpoint["populate_jail_refresh"] = {
        **dict(_mapping(checkpoint.get("populate_jail_refresh"))),
        "mode": resolved_populate_jail_refresh,
        "status": str(
            _mapping(checkpoint.get("populate_jail_refresh")).get("status", "planned")
            or "planned"
        ),
    }

    def _record_slurm_decision(decision: Mapping[str, Any]) -> None:
        slurm = checkpoint.setdefault("slurm", {})
        if not isinstance(slurm, dict):
            slurm = {}
            checkpoint["slurm"] = slurm
        decisions = slurm.setdefault("decisions", [])
        if not isinstance(decisions, list):
            decisions = []
            slurm["decisions"] = decisions
        decisions.append(to_plain_data(decision))
        _append_event(
            checkpoint,
            "slurm-job-policy",
            action=str(decision.get("action", "") or "unknown"),
        )

    status_prompt_pause = (
        getattr(status_callback, "pause", None) if status_callback is not None else None
    )
    if not callable(status_prompt_pause):
        status_prompt_pause = None

    rollout_manifest = rollout.to_manifest_dict()
    saved_rollout = _mapping(checkpoint.get("external_node_template_rollout"))
    if saved_rollout and dict(saved_rollout) != rollout_manifest:
        raise RuntimeError(
            "External Soperator upgrade checkpoint was started with different external "
            "node-template rollout settings. Resume with the same worker rollout "
            "strategy and budget, or remove the checkpoint only after deciding to "
            "restart the upgrade."
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
    protected_state_before = _migration_safety_baseline_from_checkpoint(checkpoint)
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
                nebius_api=active_nebius_api,
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
            _emit_phase_comment(
                _EXTERNAL_NODE_TEMPLATE_PHASE_ID,
                "checking affected Slurm jobs before external node-template rollout.",
            )
            try:
                worker_rollout_preflight_lines = _run_soperator_worker_rollout_live_preflight(
                    source_report=execution_source_report,
                    worker_node_groups=preflight_worker_groups,
                    command_runner=active_command_runner,
                    kube_context=kube_context,
                    rollout=rollout,
                    job_policy=resolved_job_policy,
                    cancel_job_ids=selected_cancel_job_ids,
                    requeue_job_ids=selected_requeue_job_ids,
                    job_wait_timeout_seconds=job_wait_timeout_seconds,
                    job_refresh_interval_seconds=job_refresh_interval_seconds,
                    slurm_decision_recorder=_record_slurm_decision,
                    interactive_prompt_pause=status_prompt_pause,
                    allow_resolved_interactive_job_policy=allow_resolved_interactive_job_policy,
                )
            except SoperatorMigrationPhasePending as exc:
                quota_preflight_pending_phase = _EXTERNAL_NODE_TEMPLATE_PHASE_ID
                quota_preflight_pending_reason = str(exc)
    else:
        quota_preflight_lines = [
            "Quota preflight: deferred until customer approval because no mutating phase will run."
        ]
    if effective_approval and not quota_preflight_pending_phase:
        if not backup_state and (set(phase_ids) & _MUTATING_PHASE_IDS):
            raise RuntimeError(
                "External Soperator upgrade requires restore-capable backup metadata before "
                "approved mutation. Run through `nebius-cxcli ext-soperator upgrade --execute "
                "--approve` so cxcli can create or reuse the pre-upgrade backup."
            )
        if backup_state and (set(phase_ids) & _MUTATING_PHASE_IDS):
            _emit_phase_comment(
                "backup",
                "checking restore-capable backup metadata before approved mutation.",
            )
            _validate_external_upgrade_backup_metadata(
                backup_state,
                config_path=config_path,
                verify_archive_hash=mutating_progress_started,
            )
        if protected_state_before is None:
            _emit_phase_comment(
                "protected-state-capture",
                "capturing protected customer state before external remediation or rollout.",
            )
            protected_state_before = _capture_external_upgrade_protected_state(
                command_runner=active_command_runner,
                payload=payload,
                target_ref=normalized_target,
                kube_context=kube_context,
            )
            checkpoint["upgrade_safety"] = update_safety_payload_with_before(
                checkpoint.get("upgrade_safety")
                if isinstance(checkpoint.get("upgrade_safety"), Mapping)
                else None,
                protected_state_before,
                backup=backup_state or {"status": "not-recorded"},
            )
            _append_event(checkpoint, "protected-state-captured")
            _write_checkpoint(checkpoint_path, checkpoint)
            _emit_phase_comment(
                "protected-state-capture",
                "protected customer-state baseline captured and checkpointed.",
            )
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

    def _record_phase_failure(
        phase_id: str,
        exc: BaseException,
    ) -> None:
        nonlocal pending_phase, pending_reason
        interrupted = isinstance(exc, (KeyboardInterrupt, EOFError))
        pending_phase = phase_id
        pending_reason = (
            "interrupted by user"
            if interrupted
            else (str(exc).strip() or exc.__class__.__name__)
        )
        checkpoint["pending_phase"] = pending_phase
        checkpoint["pending_reason"] = pending_reason
        _append_event(
            checkpoint,
            "execute-interrupted" if interrupted else "execute-phase-failed",
            phase=phase_id,
            error=pending_reason,
        )
        report_path = _migrate_report_path(config_path)
        json_report_path = _upgrade_report_json_path(config_path)
        checkpoint["upgrade_report"] = str(report_path)
        checkpoint["upgrade_report_json"] = str(json_report_path)
        _write_soperator_migrate_report(
            config_path=config_path,
            checkpoint_path=checkpoint_path,
            checkpoint=checkpoint,
            phase_ids=phase_ids,
            completed_phases=completed_phases,
            target_ref=normalized_target,
            source_version=live_source_version,
            target_version=target_version,
            pending_phase=pending_phase,
            pending_reason=pending_reason,
            mutation_performed=mutation_performed,
        )
        _checkpoint_progress()

    status_reporter: SoperatorMigrationStatusReporter | None = None
    if effective_approval and status_callback is not None:
        status_reporter = SoperatorMigrationStatusReporter(
            emit=status_callback,
            nebius_api=active_nebius_api,
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
                nebius_api=active_nebius_api,
                login_session_policy=resolved_login_session_policy,
                login_session_drain_timeout_seconds=login_session_drain_timeout_seconds,
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
            nebius_api=active_nebius_api,
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
                nebius_api=active_nebius_api,
                command_runner=active_command_runner,
                checkpoint_writer=_checkpoint_progress,
                rollout=rollout,
                job_policy=resolved_job_policy,
                cancel_job_ids=selected_cancel_job_ids,
                requeue_job_ids=selected_requeue_job_ids,
                job_wait_timeout_seconds=job_wait_timeout_seconds,
                job_refresh_interval_seconds=job_refresh_interval_seconds,
                login_session_policy=resolved_login_session_policy,
                login_session_drain_timeout_seconds=login_session_drain_timeout_seconds,
                slurm_decision_recorder=_record_slurm_decision,
                interactive_prompt_pause=status_prompt_pause,
                allow_resolved_interactive_job_policy=allow_resolved_interactive_job_policy,
            ),
            _TARGET_GPU_STACK_PHASE_ID: lambda: _execute_target_gpu_stack_remediation_phase(
                checkpoint=checkpoint,
                payload=payload,
                target_ref=normalized_target,
                kube_context=kube_context,
                command_runner=active_command_runner,
                checkpoint_writer=_checkpoint_progress,
            ),
            "create-aligned-sfs": lambda: _execute_create_aligned_sfs_phase(
                checkpoint=checkpoint,
                payload=payload,
                source_report=execution_source_report,
                target_ref=normalized_target,
                worker_node_groups=approved_worker_groups,
                nebius_api=active_nebius_api,
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
                nebius_api=active_nebius_api,
                command_runner=active_command_runner,
                checkpoint_writer=_checkpoint_progress,
                job_policy=resolved_job_policy,
                cancel_job_ids=selected_cancel_job_ids,
                requeue_job_ids=selected_requeue_job_ids,
                job_wait_timeout_seconds=job_wait_timeout_seconds,
                job_refresh_interval_seconds=job_refresh_interval_seconds,
                slurm_decision_recorder=_record_slurm_decision,
                interactive_prompt_pause=status_prompt_pause,
                allow_resolved_interactive_job_policy=allow_resolved_interactive_job_policy,
            ),
            "final-control-plane-cutover": lambda: _execute_final_cutover_phase(
                checkpoint=checkpoint,
                live_snapshot=live_snapshot,
                target_ref=normalized_target,
                kube_context=kube_context,
                command_runner=active_command_runner,
            ),
            POPULATE_JAIL_REFRESH_PHASE_ID: lambda: _execute_populate_jail_refresh_phase(
                checkpoint=checkpoint,
                payload=payload,
                source_report=execution_source_report,
                live_snapshot=live_snapshot,
                target_ref=normalized_target,
                kube_context=kube_context,
                command_runner=active_command_runner,
                populate_jail_refresh=resolved_populate_jail_refresh,
                job_policy=resolved_job_policy,
                cancel_job_ids=selected_cancel_job_ids,
                requeue_job_ids=selected_requeue_job_ids,
                job_wait_timeout_seconds=job_wait_timeout_seconds,
                job_refresh_interval_seconds=job_refresh_interval_seconds,
                login_session_policy=resolved_login_session_policy,
                login_session_drain_timeout_seconds=login_session_drain_timeout_seconds,
                slurm_decision_recorder=_record_slurm_decision,
                interactive_prompt_pause=status_prompt_pause,
                allow_resolved_interactive_job_policy=allow_resolved_interactive_job_policy,
                checkpoint_writer=_checkpoint_progress,
                jail_sfs_resize_handler=jail_sfs_resize_handler,
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
                approve_remediation=approve_remediation,
                require_target_soperator_helm=require_target_soperator_helm,
            ),
            "retire-old-resources": lambda: _execute_retire_old_resources_phase(
                checkpoint=checkpoint,
                source_report=execution_source_report,
                live_snapshot=live_snapshot,
                kube_context=kube_context,
                nebius_api=active_nebius_api,
                command_runner=active_command_runner,
                checkpoint_writer=_checkpoint_progress,
            ),
        }
        for phase_id in phase_ids:
            if phase_id in {"discovery-and-plan", "customer-approval"}:
                continue
            if phase_id in _POST_UPGRADE_CHECK_PHASE_IDS:
                continue
            if phase_id in completed_phases:
                continue
            handler = phase_handlers.get(phase_id)
            if handler is None:
                pending_phase = phase_id
                pending_reason = f"unsupported external Soperator upgrade phase '{phase_id}'."
                break
            status_started = False
            phase_mutation = False
            verification_lines: list[str] = []
            try:
                checkpoint["pending_phase"] = phase_id
                checkpoint["pending_reason"] = ""
                _append_event(checkpoint, "execute-phase-started", phase=phase_id)
                _checkpoint_progress()
                if status_reporter is not None:
                    _append_status_event(status_reporter.set_phase(phase_id), point="phase-start")
                    status_reporter.start()
                    status_started = True
                phase_mutation, lines = handler()
                mutation_performed = mutation_performed or phase_mutation
                phase_lines.extend([f"{phase_id}: {line}" for line in lines])
                if phase_id in _FAST_STAGE_VERIFICATION_PHASE_IDS:
                    _emit_phase_comment(phase_id, "running fast stage verification.")
                    verification_lines = _run_external_upgrade_phase_fast_verification(
                        checkpoint=checkpoint,
                        phase_id=phase_id,
                        payload=payload,
                        source_report=execution_source_report,
                        live_snapshot=live_snapshot,
                        target_ref=normalized_target,
                        kube_context=kube_context,
                        worker_node_groups=approved_worker_groups,
                        nebius_api=active_nebius_api,
                        command_runner=active_command_runner,
                    )
            except SoperatorMigrationPhasePending as exc:
                pending_phase = phase_id
                pending_reason = str(exc)
                checkpoint["pending_phase"] = pending_phase
                checkpoint["pending_reason"] = pending_reason
                verification = stage_fast_verification_report(
                    phase_id,
                    _mapping(_mapping(checkpoint.get("phase_state")).get(phase_id)),
                )
                if str(verification.get("status", "") or "not_run") == "not_run":
                    verification = _record_phase_fast_verification(
                        checkpoint=checkpoint,
                        phase_id=phase_id,
                        status="failed",
                        summary=pending_reason,
                        checks=[
                            _fast_verification_check(
                                _STATUS_PHASE_LABELS.get(phase_id, phase_id),
                                "failed",
                                pending_reason,
                            )
                        ],
                    )
                phase_lines.append(_phase_validation_summary_line(phase_id, verification))
                _checkpoint_progress()
                break
            except (KeyboardInterrupt, EOFError) as exc:
                _record_phase_failure(phase_id, exc)
                raise
            except Exception as exc:
                _record_phase_failure(phase_id, exc)
                raise
            finally:
                if status_reporter is not None:
                    _append_status_event(status_reporter.emit(force=True), point="phase-end")
                    if status_started:
                        status_reporter.stop()
            completed_phases.add(phase_id)
            _append_event(
                checkpoint,
                "execute-phase-completed",
                phase=phase_id,
                mutation_performed=phase_mutation,
            )
            _clear_completed_pending_phase(checkpoint, phase_id)
            phase_lines.extend(verification_lines)
            _checkpoint_progress()
            if phase_id in {
                _EXTERNAL_NODE_TEMPLATE_PHASE_ID,
                _TARGET_GPU_STACK_PHASE_ID,
                "create-aligned-sfs",
                "online-bulk-data-sync",
                "rolling-compute-migration",
                "final-control-plane-cutover",
                POPULATE_JAIL_REFRESH_PHASE_ID,
            }:
                live_snapshot = snapshot_collector(kube_context=kube_context)
                _append_event(
                    checkpoint,
                    "execute-live-snapshot-refreshed",
                    after_phase=phase_id,
                )
                _checkpoint_progress()

    if not pending_phase and effective_approval:
        _emit_phase_comment(
            "post-upgrade-mk8s-check",
            "verifying completed MK8s node-template and worker rollout state.",
        )
        checkpoint["pending_phase"] = "post-upgrade-mk8s-check"
        checkpoint["pending_reason"] = ""
        _append_event(checkpoint, "execute-phase-started", phase="post-upgrade-mk8s-check")
        _checkpoint_progress()
        mk8s_phase_lines: list[str] = []
        try:
            mk8s_state_lines = _verify_completed_soperator_migration_mk8s_state(
                payload=payload,
                source_report=execution_source_report,
                target_ref=normalized_target,
                worker_node_groups=approved_worker_groups,
                phase_ids=phase_ids,
                nebius_api=active_nebius_api,
                command_runner=active_command_runner,
            )
        except RuntimeError as exc:
            pending_phase = "post-upgrade-mk8s-check"
            pending_reason = str(exc).strip() or "post-upgrade MK8s check failed."
            payload = _record_phase_fast_verification(
                checkpoint=checkpoint,
                phase_id=pending_phase,
                status="failed",
                summary=pending_reason,
                checks=[
                    _fast_verification_check(
                        "Post-upgrade MK8s state",
                        "failed",
                        pending_reason,
                    )
                ],
            )
            phase_lines.append(_phase_validation_summary_line(pending_phase, payload))
            _checkpoint_progress()
        except (KeyboardInterrupt, EOFError) as exc:
            _record_phase_failure("post-upgrade-mk8s-check", exc)
            raise
        except Exception as exc:
            _record_phase_failure("post-upgrade-mk8s-check", exc)
            raise
        else:
            mk8s_summary = (
                mk8s_state_lines[0]
                if mk8s_state_lines
                else "completed MK8s node-template and worker rollout state verified."
            )
            payload = _record_phase_fast_verification(
                checkpoint=checkpoint,
                phase_id="post-upgrade-mk8s-check",
                status="passed",
                summary=mk8s_summary,
                checks=[
                    _fast_verification_check(
                        "Post-upgrade MK8s state",
                        "passed",
                        mk8s_summary,
                    )
                ],
            )
            mk8s_phase_lines = [f"post-upgrade-mk8s-check: {line}" for line in mk8s_state_lines]
            phase_lines.extend(mk8s_phase_lines)
            phase_lines.append(_phase_validation_summary_line("post-upgrade-mk8s-check", payload))
            completed_phases.add("post-upgrade-mk8s-check")
            _append_event(
                checkpoint,
                "execute-phase-completed",
                phase="post-upgrade-mk8s-check",
                mutation_performed=False,
            )
            _clear_completed_pending_phase(checkpoint, "post-upgrade-mk8s-check")
            _checkpoint_progress()
    if not pending_phase and effective_approval:
        _emit_phase_comment(
            "post-upgrade-helm-check",
            "verifying final Helm releases and target Soperator readiness.",
        )
        checkpoint["pending_phase"] = "post-upgrade-helm-check"
        checkpoint["pending_reason"] = ""
        _append_event(checkpoint, "execute-phase-started", phase="post-upgrade-helm-check")
        _checkpoint_progress()
        try:
            helm_state_lines = _verify_completed_soperator_migration_helm_state(
                command_runner=active_command_runner,
                kube_context=kube_context,
                target_version=target_version,
                require_target_release=require_target_soperator_helm,
            )
        except RuntimeError as exc:
            pending_phase = "post-upgrade-helm-check"
            pending_reason = str(exc).strip() or "post-upgrade Helm check failed."
            payload = _record_phase_fast_verification(
                checkpoint=checkpoint,
                phase_id=pending_phase,
                status="failed",
                summary=pending_reason,
                checks=[
                    _fast_verification_check(
                        "Post-upgrade Helm state",
                        "failed",
                        pending_reason,
                    )
                ],
            )
            phase_lines.append(_phase_validation_summary_line(pending_phase, payload))
            _checkpoint_progress()
        except (KeyboardInterrupt, EOFError) as exc:
            _record_phase_failure("post-upgrade-helm-check", exc)
            raise
        except Exception as exc:
            _record_phase_failure("post-upgrade-helm-check", exc)
            raise
        else:
            helm_summary = (
                helm_state_lines[0]
                if helm_state_lines
                else "final Helm releases and target Soperator readiness verified."
            )
            payload = _record_phase_fast_verification(
                checkpoint=checkpoint,
                phase_id="post-upgrade-helm-check",
                status="passed",
                summary=helm_summary,
                checks=[
                    _fast_verification_check(
                        "Post-upgrade Helm state",
                        "passed",
                        helm_summary,
                    )
                ],
            )
            mutation_performed = (
                mutation_performed
                or _helm_state_lines_include_retirement_mutation(helm_state_lines)
            )
            phase_lines.extend(f"post-upgrade-helm-check: {line}" for line in helm_state_lines)
            phase_lines.append(_phase_validation_summary_line("post-upgrade-helm-check", payload))
            completed_phases.add("post-upgrade-helm-check")
            _append_event(
                checkpoint,
                "execute-phase-completed",
                phase="post-upgrade-helm-check",
                mutation_performed=False,
            )
            _clear_completed_pending_phase(checkpoint, "post-upgrade-helm-check")
            _checkpoint_progress()

    if (
        not pending_phase
        and effective_approval
        and not _checkpoint_protected_comparison_passed(checkpoint)
    ):
        _emit_phase_comment(
            "validation-and-rollback-hold",
            "verifying shared protected-state before config refresh.",
        )
        try:
            safety_lines = _ensure_external_upgrade_safety_verified(
                command_runner=active_command_runner,
                checkpoint=checkpoint,
                payload=payload,
                target_ref=normalized_target,
                kube_context=kube_context,
                approve_remediation=approve_remediation,
            )
        except SoperatorMigrationPhasePending as exc:
            pending_phase = "validation-and-rollback-hold"
            pending_reason = str(exc)
            checkpoint["pending_phase"] = pending_phase
            checkpoint["pending_reason"] = pending_reason
            _checkpoint_progress()
        else:
            phase_lines.extend(f"validation-and-rollback-hold: {line}" for line in safety_lines)
            _checkpoint_progress()

    if pending_phase:
        checkpoint["pending_phase"] = pending_phase
        checkpoint["pending_reason"] = pending_reason
        _append_event(checkpoint, "execute-pending", pending_phase=pending_phase)
    else:
        checkpoint["pending_phase"] = "none"
        checkpoint["pending_reason"] = ""
        _append_event(checkpoint, "execute-completed")
    report_path = _migrate_report_path(config_path)
    json_report_path = _upgrade_report_json_path(config_path)
    checkpoint["upgrade_report"] = str(report_path)
    checkpoint["upgrade_report_json"] = str(json_report_path)
    if upgrade_path_fingerprint and upgrade_path_segment_id:
        if not _checkpoint_locked_upgrade_path(checkpoint):
            raise RuntimeError(_locked_upgrade_path_repair_message())
        completed_segment_ids = [
            str(segment_id or "").strip()
            for segment_id in checkpoint.get("completed_segment_ids", []) or []
            if str(segment_id or "").strip()
        ]
        segment_state = checkpoint.setdefault("segment_state", {})
        if not isinstance(segment_state, dict):
            segment_state = {}
            checkpoint["segment_state"] = segment_state
        segment_entry = segment_state.setdefault(upgrade_path_segment_id, {})
        if isinstance(segment_entry, dict):
            segment_report_path, segment_json_report_path = ext_soperator_upgrade_segment_report_paths(
                config_path,
                normalized_target,
                upgrade_path_segment_id,
            )
            segment_entry["live_source_version"] = live_source_version
            segment_entry["target_version"] = target_version
            segment_entry["completed_phases"] = _ordered_phase_list(completed_phases, phase_ids)
            segment_entry["report_path"] = str(report_path)
            segment_entry["json_report_path"] = str(json_report_path)
            segment_entry["segment_report_path"] = str(segment_report_path)
            segment_entry["segment_json_report_path"] = str(segment_json_report_path)
            backup_path = str(_mapping(checkpoint.get("backup")).get("path", "") or "").strip()
            if backup_path:
                segment_entry["backup_path"] = backup_path
            if str(checkpoint.get("pending_phase", "") or "") == "none":
                segment_entry["completed_at"] = _utc_now()
        if str(checkpoint.get("pending_phase", "") or "") == "none":
            if upgrade_path_segment_id not in completed_segment_ids:
                completed_segment_ids.append(upgrade_path_segment_id)
            checkpoint["completed_segment_ids"] = list(completed_segment_ids)
        checkpoint["segment_state"] = segment_state
        checkpoint["upgrade_path_fingerprint"] = upgrade_path_fingerprint
        checkpoint["current_segment_id"] = upgrade_path_segment_id
    _emit_phase_comment(
        "report",
        "writing external Soperator upgrade checkpoint and reports.",
    )
    _append_event(
        checkpoint,
        "upgrade-report-written",
        path=str(report_path),
        json_path=str(json_report_path),
    )
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
            "Upgrade status: "
            + _external_upgrade_status_summary(
                pending_phase=str(checkpoint["pending_phase"]),
                pending_reason=pending_reason,
                mutation_performed=mutation_performed,
            ),
            f"Pending phase: {checkpoint['pending_phase']}",
            f"Pending reason: {pending_reason or 'none'}",
            "Upgrade performed: " + ("yes." if mutation_performed else "no."),
            f"Upgrade report: {report_path}",
            f"Upgrade JSON report: {json_report_path}",
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
