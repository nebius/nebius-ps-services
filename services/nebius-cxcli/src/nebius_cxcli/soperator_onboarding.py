"""Read-only Soperator onboarding analysis helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .component_instances import normalize_component_token
from .runtime_config import to_plain_data
from .soperator_artifacts import (
    SoperatorClusterArtifactIdentity,
    soperator_cluster_artifact_identity_from_payload,
    soperator_cluster_report_dir,
)
from .soperator_discovery import (
    SOPERATOR_DISCOVERY_DIR_NAME,
    load_soperator_discovery_bundle,
    soperator_discovery_manifest_path,
    write_soperator_discovery_bundle,
)
from .soperator_populate_jail import POPULATE_JAIL_REFRESH_PHASE_ID
from .soperator_upgrade_campaign import SOPERATOR_UPGRADE_CAMPAIGN_SCHEMA

ONBOARDING_SCHEMA = "nebius-cxcli-soperator-onboarding/v2"
SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA = SOPERATOR_UPGRADE_CAMPAIGN_SCHEMA
EXT_SOPERATOR_ONBOARD_DESCRIPTION = (
    "Register a new target, report its active campaign, or propose the next campaign "
    "after completion."
)
ONBOARDING_REPORT_DIR = "reports"
SOURCE_SOPERATOR_DISCOVERY_REPORT_NAME = SOPERATOR_DISCOVERY_DIR_NAME
ONBOARDING_STATE_NO_SOPERATOR_DETECTED = "no-soperator-detected"
ONBOARDING_STATE_EXISTING_SOPERATOR_SUPPORTED = "existing-soperator-supported"
ONBOARDING_STATE_EXISTING_SOPERATOR_TARGET = "existing-soperator-target"
ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN = "existing-soperator-unknown"
ONBOARDING_STATE_EXISTING_SOPERATOR_NEWER = "existing-soperator-newer"
ONBOARDING_STATE_ANALYSIS_INCOMPLETE = "analysis-incomplete"
ONBOARDING_ACTION_INSTALL_SOPERATOR = "install-soperator"
ONBOARDING_ACTION_ADOPT_SOPERATOR = "adopt-soperator"
ONBOARDING_ACTION_UPGRADE_SOPERATOR = "upgrade-soperator"
ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE = "approve-external-soperator-upgrade"
ONBOARDING_ACTION_CONFIGURE_STORAGE = "configure-soperator-storage"
ONBOARDING_ACTION_CREATE_ALIGNED_SFS = "create-aligned-sfs"
ONBOARDING_ACTION_PLAN_DATA_MIGRATION = "plan-soperator-data-migration"
ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION = "plan-soperator-compute-migration"
ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK = "reconcile-target-gpu-stack"
ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE = "upgrade-external-node-template"
ONBOARDING_ACTION_ENABLE_TOPOLOGY = "enable-slurm-topology"
ONBOARDING_ACTION_IDS = frozenset(
    {
        ONBOARDING_ACTION_INSTALL_SOPERATOR,
        ONBOARDING_ACTION_ADOPT_SOPERATOR,
        ONBOARDING_ACTION_UPGRADE_SOPERATOR,
        ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE,
        ONBOARDING_ACTION_CONFIGURE_STORAGE,
        ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
        ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
        ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
        ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
        ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
        ONBOARDING_ACTION_ENABLE_TOPOLOGY,
    }
)
ONBOARDING_EXTERNAL_UPGRADE_ACTION_IDS = frozenset(
    {
        ONBOARDING_ACTION_UPGRADE_SOPERATOR,
        ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE,
        ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
        ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
        ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
        ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
    }
)
ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION = "1.34"
ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_OS = "ubuntu24.04"
ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_GPU_STACK_PRESET = "cuda13.0"
SOPERATOR_UPGRADE_SUPPORT_LAYER = "soperator-upgrade-support"
SOPERATOR_UPGRADE_SUPPORT_STATUS_SUPPORTED = "supported"
SOPERATOR_UPGRADE_SUPPORT_STATUS_SUPPORTED_WITH_WARNING = "supported_with_warning"
SOPERATOR_UPGRADE_SUPPORT_STATUS_UNSUPPORTED = "unsupported"
SOPERATOR_UPGRADE_SUPPORT_STATUS_NOT_VALIDATED = "not_validated"
SOPERATOR_UPGRADE_SUPPORT_REJECT_STATUSES = frozenset(
    {
        SOPERATOR_UPGRADE_SUPPORT_STATUS_UNSUPPORTED,
        SOPERATOR_UPGRADE_SUPPORT_STATUS_NOT_VALIDATED,
    }
)
ONBOARDING_STORAGE_MODE_KEEP_EXISTING = "keep-existing-storage"
ONBOARDING_STORAGE_MODE_CREATE_ALIGNED_SFS = "create-aligned-sfs"
ONBOARDING_COMPUTE_MODE_KEEP_EXISTING = "keep-existing-compute"
ONBOARDING_COMPUTE_MODE_CREATE_ALIGNED_NODE_GROUPS = "create-aligned-node-groups"
ONBOARDING_STORAGE_MODES = frozenset(
    {ONBOARDING_STORAGE_MODE_KEEP_EXISTING, ONBOARDING_STORAGE_MODE_CREATE_ALIGNED_SFS}
)
ONBOARDING_COMPUTE_MODES = frozenset(
    {
        ONBOARDING_COMPUTE_MODE_KEEP_EXISTING,
        ONBOARDING_COMPUTE_MODE_CREATE_ALIGNED_NODE_GROUPS,
    }
)
ONBOARDING_REQUIRED_STORAGE_KEYS = ("jail", "controller-spool", "accounting")
ONBOARDING_SERVICE_ROLES = ("system", "controller", "login", "accounting")
ONBOARDING_NODESET_LABEL_KEYS = (
    "slurm.nebius.ai/nodeset-name",
    "slurm.nebius.ai/nodeset",
)
ONBOARDING_WORKER_ROLE_PREFIX = "worker"
ONBOARDING_ACCEPTABLE_STATES = frozenset(
    {
        ONBOARDING_STATE_NO_SOPERATOR_DETECTED,
        ONBOARDING_STATE_EXISTING_SOPERATOR_SUPPORTED,
        ONBOARDING_STATE_EXISTING_SOPERATOR_TARGET,
    }
)
SOPERATOR_CRD_RESOURCE_KINDS = (
    ("activechecks.slurm.nebius.ai", "activechecks"),
    ("jailedconfigs.slurm.nebius.ai", "jailedconfigs"),
    ("slurmclusters.slurm.nebius.ai", "slurmclusters"),
    ("nodeconfigurators.slurm.nebius.ai", "nodeconfigurators"),
    ("nodesetpowerstates.slurm.nebius.ai", "nodesetpowerstates"),
    ("nodesets.slurm.nebius.ai", "nodesets"),
)
SOPERATOR_CRD_NAMES = frozenset(name for name, _resource_kind in SOPERATOR_CRD_RESOURCE_KINDS)
SOPERATOR_MIGRATION_PROFILE_DATA_FILE = Path(__file__).with_name(
    "soperator_migration_profiles.yaml"
)
SOPERATOR_HOST_DRIVER_JAIL_CUDA_POLICY_SCHEMA = (
    "nebius-cxcli-soperator-host-driver-jail-cuda-policy/v1"
)
SOPERATOR_COMPATIBLE_RELEASE_NAMES = frozenset({"soperator", "slurm-operator"})
SOPERATOR_COMPATIBLE_CONTROLLER_RELEASE_NAMES = frozenset({"soperator-controller"})
SOPERATOR_COMPATIBLE_CHART_IDENTITIES = frozenset({"soperator", "helm-soperator", "slurm-operator"})
GPU_STACK_HELM_DISCOVERY_NAMESPACES = ("nvidia-gpu-operator", "nvidia-network-operator")
_EPHEMERAL_HELM_RELEASE_KEYS = frozenset(
    {"description", "last_deployed", "revision", "status", "updated"}
)


@dataclass(frozen=True)
class SoperatorOnboardingFinding:
    layer: str
    status: str
    severity: str
    message: str
    action_id: str = ""
    evidence: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class SoperatorOnboardingAction:
    id: str
    title: str
    layer: str
    required: bool = False
    selected: bool = False
    disruptive: bool = False
    reason: str = ""


@dataclass(frozen=True)
class SoperatorRemediationItem:
    id: str
    title: str
    classification: str
    reason: str
    requires_customer_approval: bool = False


@dataclass(frozen=True)
class SoperatorMigrationPhase:
    id: str
    title: str
    status: str
    progress_label: str = ""
    requires_customer_approval: bool = False
    quiet_window: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SoperatorOnboardingReport:
    schema: str
    target_ref: str
    analyzed_at: str
    state: str
    fingerprint: str
    findings: tuple[SoperatorOnboardingFinding, ...]
    actions: tuple[SoperatorOnboardingAction, ...]
    source_version: str = ""
    target_version: str = ""
    migration_profile_id: str = ""
    remediation: tuple[SoperatorRemediationItem, ...] = ()
    migration_plan: tuple[SoperatorMigrationPhase, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "target_ref": self.target_ref,
            "analyzed_at": self.analyzed_at,
            "state": self.state,
            "fingerprint": self.fingerprint,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "migration_profile_id": self.migration_profile_id,
            "findings": [asdict(item) for item in self.findings],
            "actions": [asdict(item) for item in self.actions],
            "remediation": [asdict(item) for item in self.remediation],
            "migration_plan": [asdict(item) for item in self.migration_plan],
        }


def _stable_json(value: Any) -> str:
    return json.dumps(to_plain_data(value), sort_keys=True, separators=(",", ":"), default=str)


def _stable_helm_releases(value: Any) -> list[Any]:
    releases = []
    items = value if isinstance(value, list) else []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        releases.append(
            {
                str(key): to_plain_data(release_value)
                for key, release_value in item.items()
                if str(key).strip().lower() not in _EPHEMERAL_HELM_RELEASE_KEYS
            }
        )
    return releases


def _stable_analysis_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    stable = dict(to_plain_data(snapshot))
    stable["helm_releases"] = _stable_helm_releases(stable.get("helm_releases"))
    return stable


def _analysis_fingerprint(snapshot: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _stable_json(_stable_analysis_snapshot(snapshot)).encode("utf-8")
    ).hexdigest()


def _stable_source_discovery_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    stable = dict(copy.deepcopy(to_plain_data(payload)))
    stable.pop("generated_at", None)
    report = stable.get("report")
    if isinstance(report, dict):
        report.pop("analyzed_at", None)
    snapshot = stable.get("snapshot")
    if isinstance(snapshot, dict):
        snapshot["helm_releases"] = _stable_helm_releases(snapshot.get("helm_releases"))
    return stable


def _preserve_source_discovery_timestamps_if_stable(
    *,
    path: Path,
    payload: dict[str, Any],
) -> None:
    if not path.exists():
        return
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(existing, Mapping):
        return
    if _stable_source_discovery_payload(existing) != _stable_source_discovery_payload(payload):
        return
    generated_at = str(existing.get("generated_at", "") or "").strip()
    if generated_at:
        payload["generated_at"] = generated_at
    existing_report = existing.get("report")
    report = payload.get("report")
    if isinstance(existing_report, Mapping) and isinstance(report, dict):
        analyzed_at = str(existing_report.get("analyzed_at", "") or "").strip()
        if analyzed_at:
            report["analyzed_at"] = analyzed_at


def soperator_onboarding_fingerprint(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> str:
    payload = to_plain_data(payload_or_config)
    if not isinstance(payload, Mapping):
        payload = {}
    target = soperator_onboarding_target(payload, target_ref=target_ref)
    app_row = soperator_onboarding_app_row(payload, target_ref=target_ref)
    onboarding = (target or {}).get("soperator_onboarding", {})
    if not isinstance(onboarding, Mapping):
        onboarding = {}
    material = {
        "target_ref": normalize_component_token(target_ref),
        "target": {
            "cluster_id": str((target or {}).get("cluster_id", "") or "").strip(),
            "inventory": (target or {}).get("inventory", {}),
            "kube_context": str((target or {}).get("kube_context", "") or "").strip(),
            "onboarding": {
                "actions": list(onboarding.get("actions", []) or []),
                "state": str(onboarding.get("state", "") or "").strip(),
                "storage_mode": str(onboarding.get("storage_mode", "") or "").strip(),
                "compute_mode": str(onboarding.get("compute_mode", "") or "").strip(),
                "target_version": str(onboarding.get("target_version", "") or "").strip(),
                "source_version": str(onboarding.get("source_version", "") or "").strip(),
                "migration_profile_id": str(
                    onboarding.get("migration_profile_id", "") or ""
                ).strip(),
                "node_template_upgrade": to_plain_data(onboarding.get("node_template_upgrade", {})),
                "upgrade_path": to_plain_data(onboarding.get("upgrade_path", {})),
                "collection_errors": list(onboarding.get("collection_errors", []) or []),
            },
        },
        "soperator": {
            "install_mode": str((app_row or {}).get("install_mode", "") or "").strip(),
        },
    }
    return hashlib.sha256(_stable_json(material).encode("utf-8")).hexdigest()


def soperator_onboarding_report_path(target_ref: str) -> str:
    identity = soperator_cluster_artifact_identity_from_payload({}, target_ref=target_ref)
    return (
        "generated/"
        f"{ONBOARDING_REPORT_DIR}/soperator-clusters/{identity.cluster_key}/onboarding/report.json"
    )


def source_soperator_discovery_report_path(
    project_dir: Path,
    target_ref: str | None = None,
    payload_or_config: Any = None,
) -> Path:
    if target_ref:
        identity = soperator_cluster_artifact_identity_from_payload(
            payload_or_config,
            target_ref=target_ref,
        )
        return soperator_discovery_manifest_path(
            project_dir,
            target_ref,
            artifact_identity=identity,
        )
    return (
        project_dir / "generated" / ONBOARDING_REPORT_DIR / SOURCE_SOPERATOR_DISCOVERY_REPORT_NAME
    )


def soperator_onboarding_target(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> Mapping[str, Any] | None:
    payload = to_plain_data(payload_or_config)
    deploy = payload.get("deploy") if isinstance(payload, Mapping) else None
    targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
    normalized_target = normalize_component_token(target_ref)
    if not isinstance(targets, list) or not normalized_target:
        return None
    for row in targets:
        if not isinstance(row, Mapping):
            continue
        if normalize_component_token(row.get("instance_id")) == normalized_target:
            return row
    return None


def soperator_onboarding_app_row(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> Mapping[str, Any] | None:
    payload = to_plain_data(payload_or_config)
    apps = payload.get("apps") if isinstance(payload, Mapping) else None
    charts = apps.get("charts") if isinstance(apps, Mapping) else None
    normalized_target = normalize_component_token(target_ref)
    if not isinstance(charts, list) or not normalized_target:
        return None
    for row in charts:
        if not isinstance(row, Mapping) or row.get("id") != "soperator":
            continue
        if normalize_component_token(row.get("instance_id")) == normalized_target:
            return row
    return None


def soperator_onboarding_is_accepted(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> bool:
    target = soperator_onboarding_target(payload_or_config, target_ref=target_ref)
    if not isinstance(target, Mapping):
        return False
    onboarding = target.get("soperator_onboarding")
    if not isinstance(onboarding, Mapping):
        return False
    accepted = onboarding.get("accepted")
    if accepted is not True:
        return False
    if str(onboarding.get("state", "") or "").strip() not in ONBOARDING_ACCEPTABLE_STATES:
        return False
    collection_errors = onboarding.get("collection_errors")
    if isinstance(collection_errors, list) and collection_errors:
        return False
    if not _onboarding_storage_mode_is_valid(onboarding):
        return False
    if not _onboarding_compute_mode_is_valid(onboarding):
        return False
    if _unsupported_onboarding_actions(onboarding):
        return False
    recorded = str(onboarding.get("analysis_fingerprint", "") or "").strip()
    if not recorded:
        return False
    if recorded == soperator_onboarding_fingerprint(payload_or_config, target_ref=target_ref):
        return True
    normalized_payload = to_plain_data(payload_or_config)
    if not isinstance(normalized_payload, dict):
        return False
    with suppress(Exception):
        from .cli import _materialize_soperator_component_defaults

        _materialize_soperator_component_defaults(normalized_payload)
        return recorded == soperator_onboarding_fingerprint(
            normalized_payload,
            target_ref=target_ref,
        )
    return False


def validate_soperator_onboarding_acceptance(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> None:
    if soperator_onboarding_is_accepted(payload_or_config, target_ref=target_ref):
        return
    target = normalize_component_token(target_ref) or target_ref
    onboarding_target = soperator_onboarding_target(payload_or_config, target_ref=target_ref)
    onboarding = (
        onboarding_target.get("soperator_onboarding", {})
        if isinstance(onboarding_target, Mapping)
        else {}
    )
    if isinstance(onboarding, Mapping) and _required_storage_mode_from_onboarding(onboarding):
        required_storage_mode = _required_storage_mode_from_onboarding(onboarding)
        configured_storage_mode = str(onboarding.get("storage_mode", "") or "").strip()
        if configured_storage_mode != required_storage_mode:
            raise ValueError(
                f"apps:soperator target '{target}' requires "
                f"deploy.targets[].soperator_onboarding.storage_mode "
                f"'{required_storage_mode}' because the accepted onboarding actions require "
                "aligned SFS storage migration. Rerun Soperator onboarding or choose "
                "create-aligned-sfs."
            )
    if isinstance(onboarding, Mapping):
        required_compute_mode = _required_compute_mode_from_onboarding(onboarding)
        configured_compute_mode = str(onboarding.get("compute_mode", "") or "").strip()
        if required_compute_mode and configured_compute_mode != required_compute_mode:
            raise ValueError(
                f"apps:soperator target '{target}' requires "
                f"deploy.targets[].soperator_onboarding.compute_mode "
                f"'{required_compute_mode}' because the accepted onboarding actions require "
                "Soperator-aligned compute migration. Rerun Soperator onboarding or choose "
                "create-aligned-node-groups."
            )
        unsupported_actions = _unsupported_onboarding_actions(onboarding)
        if unsupported_actions:
            formatted = ", ".join(unsupported_actions)
            raise ValueError(
                f"apps:soperator target '{target}' has unsupported "
                f"deploy.targets[].soperator_onboarding.actions value(s): {formatted}. "
                "Rerun `nebius-cxcli ext-soperator onboard` so the accepted config "
                "uses the current Soperator onboarding action contract."
            )
    raise ValueError(
        f"recovery-required: apps:soperator target '{target}' uses "
        "onboard-existing-cluster but has no v6 campaign and does not have a current "
        "accepted configuration. External Soperator upgrade requires a locked "
        "v6 campaign in deploy.targets[].soperator_onboarding.upgrade_path. Run "
        "`nebius-cxcli ext-soperator onboard` for this cluster; in-place conversion is "
        "not supported, and a journal is never an upgrade-path authority."
    )


def _required_storage_mode_from_onboarding(onboarding: Mapping[str, Any]) -> str:
    actions = {
        str(action or "").strip()
        for action in onboarding.get("actions", []) or []
        if str(action or "").strip()
    }
    if (
        ONBOARDING_ACTION_CREATE_ALIGNED_SFS in actions
        or ONBOARDING_ACTION_PLAN_DATA_MIGRATION in actions
    ):
        return ONBOARDING_STORAGE_MODE_CREATE_ALIGNED_SFS
    return ""


def _onboarding_storage_mode_is_valid(onboarding: Mapping[str, Any]) -> bool:
    configured_storage_mode = str(onboarding.get("storage_mode", "") or "").strip()
    if configured_storage_mode and configured_storage_mode not in ONBOARDING_STORAGE_MODES:
        return False
    required_storage_mode = _required_storage_mode_from_onboarding(onboarding)
    if not required_storage_mode:
        return True
    return configured_storage_mode == required_storage_mode


def _required_compute_mode_from_onboarding(onboarding: Mapping[str, Any]) -> str:
    actions = {
        str(action or "").strip()
        for action in onboarding.get("actions", []) or []
        if str(action or "").strip()
    }
    if ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION in actions:
        return ONBOARDING_COMPUTE_MODE_CREATE_ALIGNED_NODE_GROUPS
    return ""


def _onboarding_compute_mode_is_valid(onboarding: Mapping[str, Any]) -> bool:
    configured_compute_mode = str(onboarding.get("compute_mode", "") or "").strip()
    if configured_compute_mode and configured_compute_mode not in ONBOARDING_COMPUTE_MODES:
        return False
    required_compute_mode = _required_compute_mode_from_onboarding(onboarding)
    if not required_compute_mode:
        return True
    return configured_compute_mode == required_compute_mode


def _unsupported_onboarding_actions(onboarding: Mapping[str, Any]) -> tuple[str, ...]:
    actions = onboarding.get("actions", [])
    if not isinstance(actions, list):
        return ()
    return tuple(
        sorted(
            {
                action_id
                for action in actions
                if (action_id := str(action or "").strip())
                and action_id not in ONBOARDING_ACTION_IDS
            }
        )
    )


def _report_has_finding(
    report: SoperatorOnboardingReport,
    *,
    layer: str,
    status: str,
) -> bool:
    return any(finding.layer == layer and finding.status == status for finding in report.findings)


def _node_group_inventory_from_target(target: Mapping[str, Any] | None) -> Mapping[str, Any]:
    inventory = target.get("inventory") if isinstance(target, Mapping) else None
    node_groups = inventory.get("node_groups") if isinstance(inventory, Mapping) else None
    return node_groups if isinstance(node_groups, Mapping) else {}


def _node_group_kinds(node_groups: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    cpu: set[str] = set()
    gpu: set[str] = set()
    for raw_key, raw_group in node_groups.items():
        key = normalize_component_token(raw_key)
        if not key or not isinstance(raw_group, Mapping):
            continue
        is_gpu = raw_group.get("gpu") is True or str(raw_group.get("gpu", "")).lower() in {
            "1",
            "true",
            "yes",
        }
        if is_gpu:
            gpu.add(key)
        else:
            cpu.add(key)
    return cpu, gpu


def _node_groups_missing_selector_labels(node_groups: Mapping[str, Any]) -> set[str]:
    missing: set[str] = set()
    for raw_key, raw_group in node_groups.items():
        key = normalize_component_token(raw_key)
        if not key or not isinstance(raw_group, Mapping):
            continue
        selector = raw_group.get("selector")
        if isinstance(selector, Mapping) and str(selector.get("key", "") or "").strip():
            continue
        labels = raw_group.get("labels")
        if isinstance(labels, Mapping) and any(
            str(labels.get(label_key, "") or "").strip()
            for label_key in (
                "nebius.com/node-group",
                "nebius.com/node-group-id",
                "yandex.cloud/node-group-id",
                "node.kubernetes.io/instance-type",
            )
        ):
            continue
        node_labels = raw_group.get("node_labels")
        if (
            isinstance(node_labels, Mapping)
            and str(node_labels.get("nebius.com/node-group", "") or "").strip()
        ):
            continue
        missing.add(key)
    return missing


def _node_group_soperator_role(raw_group: Mapping[str, Any]) -> str:
    labels: dict[str, Any] = {}
    for field in ("labels", "node_labels"):
        value = raw_group.get(field)
        if isinstance(value, Mapping):
            labels.update(value)
    for label_key in ONBOARDING_NODESET_LABEL_KEYS:
        role = normalize_component_token(labels.get(label_key))
        if role:
            return role
    return ""


def _node_group_role_evidence(node_groups: Mapping[str, Any]) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = {role: [] for role in ONBOARDING_SERVICE_ROLES}
    evidence["worker"] = []
    for raw_key, raw_group in node_groups.items():
        group = normalize_component_token(raw_key)
        if not group or not isinstance(raw_group, Mapping):
            continue
        role = _node_group_soperator_role(raw_group)
        if role in ONBOARDING_SERVICE_ROLES:
            evidence[role].append(group)
        elif role.startswith(ONBOARDING_WORKER_ROLE_PREFIX):
            evidence["worker"].append(group)
    return {role: groups for role, groups in evidence.items() if groups}


def _compute_layout_target_compatible(node_groups: Mapping[str, Any]) -> bool:
    evidence = _node_group_role_evidence(node_groups)
    return all(role in evidence for role in ONBOARDING_SERVICE_ROLES) and bool(
        evidence.get("worker")
    )


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence_of_names(value: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return names
    for item in value:
        if isinstance(item, Mapping):
            metadata = item.get("metadata")
            name = metadata.get("name") if isinstance(metadata, Mapping) else item.get("name")
        else:
            name = item
        text = str(name or "").strip()
        if text:
            names.add(text)
    return names


def _storage_layout_keys(
    *,
    storage: Mapping[str, Any],
    pvcs: Sequence[Mapping[str, Any]],
    pvs: Sequence[Mapping[str, Any]],
) -> set[str]:
    keys = {normalize_component_token(key) for key in storage}
    required_keys = set(ONBOARDING_REQUIRED_STORAGE_KEYS)
    for resource in (*pvcs, *pvs):
        candidates = _storage_resource_name_candidates(resource)
        for candidate in candidates:
            normalized = normalize_component_token(candidate)
            if not normalized:
                continue
            for required_key in required_keys:
                if (
                    normalized == required_key
                    or normalized.startswith(f"{required_key}-")
                    or normalized.endswith(f"-{required_key}")
                    or f"-{required_key}-" in normalized
                ):
                    keys.add(required_key)
    return keys


def _storage_resource_name_candidates(resource: Mapping[str, Any]) -> tuple[str, ...]:
    candidates: list[str] = []
    metadata = resource.get("metadata")
    if isinstance(metadata, Mapping):
        candidates.append(str(metadata.get("name", "") or ""))
    spec = resource.get("spec")
    if isinstance(spec, Mapping):
        candidates.append(str(spec.get("volumeName", "") or ""))
        claim_ref = spec.get("claimRef")
        if isinstance(claim_ref, Mapping):
            candidates.append(str(claim_ref.get("name", "") or ""))
    return tuple(candidate for candidate in candidates if candidate.strip())


def _version_tuple(version: str) -> tuple[int, ...] | None:
    text = str(version or "").strip()
    match = re.search(r"([0-9]+(?:\.[0-9]+){0,3})", text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _ps_suffix_number(version: str) -> int | None:
    text = str(version or "").strip().removeprefix("v")
    core = _version_tuple(text)
    if core is None:
        return None
    core_text = ".".join(str(part) for part in core)
    core_index = text.find(core_text)
    if core_index < 0:
        return None
    suffix = text[core_index + len(core_text) :]
    if not suffix:
        return 0
    match = re.fullmatch(r"-ps\.(\d+)", suffix)
    if match is None:
        return None
    return int(match.group(1))


def normalize_soperator_release_version(version: str) -> str:
    text = str(version or "").strip().removeprefix("v")
    match = re.search(r"([0-9]+(?:\.[0-9]+){1,3})", text)
    return match.group(1) if match else ""


def compare_chart_versions(live: str, pinned: str) -> str:
    live_tuple = _version_tuple(live)
    pinned_tuple = _version_tuple(pinned)
    if live_tuple is None or pinned_tuple is None:
        return "unknown"
    max_len = max(len(live_tuple), len(pinned_tuple))
    normalized_live = live_tuple + (0,) * (max_len - len(live_tuple))
    normalized_pinned = pinned_tuple + (0,) * (max_len - len(pinned_tuple))
    if normalized_live < normalized_pinned:
        return "older"
    if normalized_live > normalized_pinned:
        return "newer"
    live_text = str(live or "").strip().removeprefix("v")
    pinned_text = str(pinned or "").strip().removeprefix("v")
    if live_text == pinned_text:
        return "equal"
    live_ps = _ps_suffix_number(live_text)
    pinned_ps = _ps_suffix_number(pinned_text)
    if live_ps is None or pinned_ps is None:
        return "unknown"
    if live_ps < pinned_ps:
        return "older"
    if live_ps > pinned_ps:
        return "newer"
    return "equal"


@lru_cache(maxsize=1)
def _load_soperator_migration_profile_data() -> Mapping[str, Any]:
    with suppress(Exception):
        payload = yaml.safe_load(SOPERATOR_MIGRATION_PROFILE_DATA_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, Mapping):
            return payload
    return {}


def _host_driver_jail_cuda_policy_error(message: str) -> RuntimeError:
    return RuntimeError(
        "Committed Soperator host-driver/Jail-CUDA compatibility policy is invalid: " + message
    )


def _normalized_jail_cuda_version(value: object) -> str:
    text = str(value or "").strip().lower().removeprefix("cuda")
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", text):
        return ""
    parts = text.split(".")
    if len(parts) == 2:
        parts.append("0")
    return ".".join(str(int(part)) for part in parts)


def soperator_host_driver_jail_cuda_policy() -> Mapping[str, Any]:
    payload = _load_soperator_migration_profile_data()
    raw_policy = payload.get("host_driver_jail_cuda_policy")
    if not isinstance(raw_policy, Mapping):
        raise _host_driver_jail_cuda_policy_error("policy mapping is missing.")
    schema = str(raw_policy.get("schema", "") or "").strip()
    if schema != SOPERATOR_HOST_DRIVER_JAIL_CUDA_POLICY_SCHEMA:
        raise _host_driver_jail_cuda_policy_error(
            f"schema must be {SOPERATOR_HOST_DRIVER_JAIL_CUDA_POLICY_SCHEMA}, "
            f"got {schema or 'missing'}."
        )

    raw_driver_presets = raw_policy.get("driver_presets")
    if not isinstance(raw_driver_presets, Mapping) or not raw_driver_presets:
        raise _host_driver_jail_cuda_policy_error("driver_presets must be a non-empty mapping.")
    driver_presets: dict[str, dict[str, int]] = {}
    for raw_preset, raw_record in raw_driver_presets.items():
        preset = str(raw_preset or "").strip().lower()
        if not preset or not isinstance(raw_record, Mapping):
            raise _host_driver_jail_cuda_policy_error(
                "every driver_presets entry must have a name and mapping value."
            )
        branch = raw_record.get("driver_branch")
        if isinstance(branch, bool) or not isinstance(branch, int) or branch <= 0:
            raise _host_driver_jail_cuda_policy_error(
                f"driver_presets.{preset}.driver_branch must be a positive integer."
            )
        if preset in driver_presets:
            raise _host_driver_jail_cuda_policy_error(
                f"driver preset {preset} is declared more than once."
            )
        driver_presets[preset] = {"driver_branch": branch}

    raw_jail_cuda = raw_policy.get("jail_cuda")
    if not isinstance(raw_jail_cuda, Mapping) or not raw_jail_cuda:
        raise _host_driver_jail_cuda_policy_error("jail_cuda must be a non-empty mapping.")
    jail_cuda: dict[str, dict[str, Any]] = {}
    required_operator_fields = (
        "component_id",
        "chart",
        "chart_version",
        "repository",
    )
    for raw_version, raw_rule in raw_jail_cuda.items():
        version = _normalized_jail_cuda_version(raw_version)
        if not version or not isinstance(raw_rule, Mapping):
            raise _host_driver_jail_cuda_policy_error(
                "every jail_cuda entry must have a semantic CUDA version and mapping value."
            )
        minimum_branch = raw_rule.get("minimum_driver_branch")
        if (
            isinstance(minimum_branch, bool)
            or not isinstance(minimum_branch, int)
            or minimum_branch <= 0
        ):
            raise _host_driver_jail_cuda_policy_error(
                f"jail_cuda.{version}.minimum_driver_branch must be a positive integer."
            )
        allow_newer = raw_rule.get("allow_newer_driver_branches")
        if not isinstance(allow_newer, bool):
            raise _host_driver_jail_cuda_policy_error(
                f"jail_cuda.{version}.allow_newer_driver_branches must be boolean."
            )
        operator_managed = raw_rule.get("operator_managed")
        required_target = (
            operator_managed.get("required_managed_gpu_operator")
            if isinstance(operator_managed, Mapping)
            else None
        )
        if not isinstance(required_target, Mapping):
            raise _host_driver_jail_cuda_policy_error(
                f"jail_cuda.{version}.operator_managed.required_managed_gpu_operator "
                "must be a mapping."
            )
        normalized_target = {
            field: str(required_target.get(field, "") or "").strip()
            for field in required_operator_fields
        }
        missing_fields = [field for field, value in normalized_target.items() if not value]
        if missing_fields:
            raise _host_driver_jail_cuda_policy_error(
                f"jail_cuda.{version} managed GPU operator target is missing "
                + ", ".join(missing_fields)
                + "."
            )
        if version in jail_cuda:
            raise _host_driver_jail_cuda_policy_error(
                f"Jail CUDA version {version} is declared more than once."
            )
        jail_cuda[version] = {
            "minimum_driver_branch": minimum_branch,
            "allow_newer_driver_branches": allow_newer,
            "operator_managed": {
                "required_managed_gpu_operator": normalized_target,
            },
        }
    return {
        "schema": schema,
        "driver_presets": driver_presets,
        "jail_cuda": jail_cuda,
    }


def validate_soperator_host_driver_jail_cuda_compatibility(
    *,
    jail_cuda_version: str,
    node_group_targets: Sequence[Mapping[str, Any]],
    managed_gpu_operator_target: Mapping[str, Any],
) -> None:
    policy = soperator_host_driver_jail_cuda_policy()
    normalized_cuda = _normalized_jail_cuda_version(jail_cuda_version)
    jail_rules = policy.get("jail_cuda")
    rule = jail_rules.get(normalized_cuda) if isinstance(jail_rules, Mapping) else None
    if not normalized_cuda or not isinstance(rule, Mapping):
        raise RuntimeError(
            "External Soperator campaign cannot be locked: committed host-driver/"
            f"Jail-CUDA policy has no rule for Jail CUDA {jail_cuda_version or 'missing'}."
        )
    driver_presets = policy.get("driver_presets")
    if not isinstance(driver_presets, Mapping):
        raise _host_driver_jail_cuda_policy_error("normalized driver_presets are missing.")
    minimum_branch = int(rule["minimum_driver_branch"])
    allow_newer = rule.get("allow_newer_driver_branches") is True
    operator_managed = rule.get("operator_managed")
    required_operator = (
        operator_managed.get("required_managed_gpu_operator")
        if isinstance(operator_managed, Mapping)
        else None
    )
    if not isinstance(required_operator, Mapping):
        raise _host_driver_jail_cuda_policy_error(
            f"normalized Jail CUDA {normalized_cuda} operator target is missing."
        )

    conflicts: list[str] = []
    for index, node_group in enumerate(node_group_targets):
        target = node_group.get("target")
        target_mapping = target if isinstance(target, Mapping) else node_group
        mode = str(node_group.get("gpu_software_mode", "") or "").strip()
        if mode == "none":
            continue
        name = str(node_group.get("name", "") or node_group.get("id", "") or index).strip()
        drivers_preset = str(target_mapping.get("drivers_preset", "") or "").strip().lower()
        if mode == "provider-managed":
            preset_rule = driver_presets.get(drivers_preset)
            if not isinstance(preset_rule, Mapping):
                conflicts.append(
                    f"node group {name}: provider-managed drivers_preset "
                    f"{drivers_preset or 'missing'} is not committed"
                )
                continue
            branch = preset_rule.get("driver_branch")
            compatible = isinstance(branch, int) and (
                branch >= minimum_branch if allow_newer else branch == minimum_branch
            )
            if not compatible:
                conflicts.append(
                    f"node group {name}: drivers_preset {drivers_preset} uses driver "
                    f"branch {branch}, below Jail CUDA {normalized_cuda} minimum branch "
                    f"{minimum_branch}"
                )
            continue
        if mode == "operator-managed":
            if drivers_preset:
                conflicts.append(
                    f"node group {name}: operator-managed mode must not set "
                    f"drivers_preset {drivers_preset}"
                )
                continue
            mismatches = [
                field
                for field, expected in required_operator.items()
                if str(managed_gpu_operator_target.get(field, "") or "").strip()
                != str(expected or "").strip()
            ]
            if mismatches:
                conflicts.append(
                    f"node group {name}: operator-managed mode requires the exact "
                    "committed managed GPU operator target; mismatched " + ", ".join(mismatches)
                )
            continue
        conflicts.append(f"node group {name}: unknown GPU software mode {mode or 'missing'}")
    if conflicts:
        raise RuntimeError(
            "External Soperator campaign cannot be locked because host-driver/Jail-CUDA "
            "compatibility validation failed: " + "; ".join(conflicts) + "."
        )


def soperator_provider_driver_presets_for_jail_cuda(
    jail_cuda_version: str,
) -> frozenset[str]:
    """Return committed provider-managed presets compatible with one Jail CUDA pin."""

    policy = soperator_host_driver_jail_cuda_policy()
    normalized_cuda = _normalized_jail_cuda_version(jail_cuda_version)
    jail_rules = policy.get("jail_cuda")
    rule = jail_rules.get(normalized_cuda) if isinstance(jail_rules, Mapping) else None
    if not normalized_cuda or not isinstance(rule, Mapping):
        raise RuntimeError(
            "External Soperator campaign cannot be locked: committed host-driver/"
            f"Jail-CUDA policy has no rule for Jail CUDA {jail_cuda_version or 'missing'}."
        )
    driver_presets = policy.get("driver_presets")
    if not isinstance(driver_presets, Mapping):
        raise _host_driver_jail_cuda_policy_error("normalized driver_presets are missing.")
    minimum_branch = int(rule["minimum_driver_branch"])
    allow_newer = rule.get("allow_newer_driver_branches") is True
    return frozenset(
        str(preset)
        for preset, raw_record in driver_presets.items()
        if isinstance(raw_record, Mapping)
        and isinstance(raw_record.get("driver_branch"), int)
        and (
            int(raw_record["driver_branch"]) >= minimum_branch
            if allow_newer
            else int(raw_record["driver_branch"]) == minimum_branch
        )
    )


def soperator_migration_profile_versions() -> tuple[str, ...]:
    payload = _load_soperator_migration_profile_data()
    releases = payload.get("releases") if isinstance(payload, Mapping) else None
    if not isinstance(releases, Sequence) or isinstance(releases, (str, bytes, bytearray)):
        return ()
    versions: list[str] = []
    for row in releases:
        if not isinstance(row, Mapping):
            continue
        version = normalize_soperator_release_version(str(row.get("version", "") or ""))
        if version:
            versions.append(version)
    return tuple(versions)


def _soperator_migration_profile_version_candidates(row: Mapping[str, Any]) -> set[str]:
    candidates = {
        str(row.get("version", "") or ""),
        str(row.get("upstream_tag", "") or ""),
        str(row.get("chart_version", "") or ""),
        str(row.get("app_version", "") or ""),
    }
    aliases = row.get("version_aliases")
    if isinstance(aliases, Sequence) and not isinstance(aliases, (str, bytes, bytearray)):
        candidates.update(str(alias or "") for alias in aliases)
    main_chart = row.get("main_chart")
    if isinstance(main_chart, Mapping):
        candidates.update(
            {
                str(main_chart.get("chart_version", "") or ""),
                str(main_chart.get("app_version", "") or ""),
            }
        )
    normalized = {
        normalize_soperator_release_version(candidate)
        for candidate in candidates
        if str(candidate or "").strip()
    }
    return {candidate for candidate in normalized if candidate}


def soperator_migration_profile_group(profile_id: str) -> Mapping[str, Any]:
    profile_key = str(profile_id or "").strip()
    if not profile_key:
        return {}
    payload = _load_soperator_migration_profile_data()
    profile_groups = payload.get("profile_groups") if isinstance(payload, Mapping) else None
    if not isinstance(profile_groups, Mapping):
        return {}
    profile_group = profile_groups.get(profile_key)
    if not isinstance(profile_group, Mapping):
        return {}
    return dict(profile_group)


def _soperator_generation_for_version(version: str) -> str:
    normalized = normalize_soperator_release_version(version)
    match = re.match(r"^([0-9]+)(?:\.|$)", normalized)
    if match is None:
        return ""
    major = int(match.group(1))
    if major <= 1:
        return "legacy-v1"
    return f"v{major}"


def _soperator_profile_id_for_generation(generation: str) -> str:
    return f"{generation}-to-target" if generation else ""


def _soperator_profile_with_group(
    row: Mapping[str, Any],
    *,
    profile_groups: Mapping[str, Any],
) -> Mapping[str, Any]:
    result = dict(row)
    profile_id = str(row.get("profile_id", "") or "").strip()
    profile_group = profile_groups.get(profile_id)
    if isinstance(profile_group, Mapping):
        result["profile_group"] = dict(profile_group)
        for key in ("requires_aligned_sfs", "compatibility_axes", "execution_contract"):
            if key in profile_group and key not in result:
                result[key] = to_plain_data(profile_group[key])
    return result


def soperator_migration_profile_for_version(
    version: str,
    *,
    allow_generation_fallback: bool = False,
) -> Mapping[str, Any] | None:
    normalized = normalize_soperator_release_version(version)
    if not normalized:
        return None
    payload = _load_soperator_migration_profile_data()
    releases = payload.get("releases") if isinstance(payload, Mapping) else None
    profile_groups = payload.get("profile_groups") if isinstance(payload, Mapping) else None
    if not isinstance(profile_groups, Mapping):
        profile_groups = {}
    if not isinstance(releases, Sequence) or isinstance(releases, (str, bytes, bytearray)):
        return None
    for row in releases:
        if not isinstance(row, Mapping):
            continue
        if normalized in _soperator_migration_profile_version_candidates(row):
            return _soperator_profile_with_group(row, profile_groups=profile_groups)
    if not allow_generation_fallback:
        return None
    generation = _soperator_generation_for_version(normalized)
    profile_id = _soperator_profile_id_for_generation(generation)
    profile_group = profile_groups.get(profile_id)
    if not isinstance(profile_group, Mapping):
        return None
    row = {
        "version": normalized,
        "upstream_tag": normalized,
        "generation": generation,
        "profile_id": profile_id,
        "migration_class": str(profile_group.get("migration_class", "") or "").strip(),
        "profile_match": "generation-fallback",
    }
    return _soperator_profile_with_group(row, profile_groups=profile_groups)


def _soperator_migration_profile_is_generation_fallback(profile: Mapping[str, Any]) -> bool:
    return str(profile.get("profile_match", "") or "").strip() == "generation-fallback"


def _version_tuple_for_support(version: str) -> tuple[int, int, int] | None:
    normalized = normalize_soperator_release_version(version)
    if not normalized:
        return None
    parts = [int(part) for part in normalized.split(".")[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _k8s_minor_tuple_for_support(version: str) -> tuple[int, int] | None:
    text = str(version or "").strip().lstrip("v")
    match = re.search(r"(?P<major>[0-9]+)\.(?P<minor>[0-9]+)", text)
    if match is None:
        return None
    return int(match.group("major")), int(match.group("minor"))


def normalize_k8s_minor_version(version: str) -> str:
    parsed = _k8s_minor_tuple_for_support(version)
    if parsed is None:
        return str(version or "").strip().lstrip("v")
    return f"{parsed[0]}.{parsed[1]}"


def _support_version_compare(left: str, right: str) -> int | None:
    left_tuple = _version_tuple_for_support(left)
    right_tuple = _version_tuple_for_support(right)
    if left_tuple is None or right_tuple is None:
        return None
    if left_tuple < right_tuple:
        return -1
    if left_tuple > right_tuple:
        return 1
    return 0


def _support_k8s_at_least(version: str, minimum: str) -> bool:
    parsed = _k8s_minor_tuple_for_support(version)
    parsed_minimum = _k8s_minor_tuple_for_support(minimum)
    if parsed is None or parsed_minimum is None:
        return False
    return parsed >= parsed_minimum


def _support_version_range_matches(version: str, range_text: str) -> bool:
    normalized = normalize_soperator_release_version(version)
    text = str(range_text or "").strip()
    if not text:
        return True
    if not normalized:
        return False
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(<=|>=|<|>|==|=)?\s*v?([0-9]+(?:\.[0-9]+){0,3})", part)
        if match is None:
            return False
        operator = match.group(1) or "=="
        comparison = _support_version_compare(normalized, match.group(2))
        if comparison is None:
            return False
        if operator in {"=", "=="} and comparison != 0:
            return False
        if operator == "<" and comparison >= 0:
            return False
        if operator == "<=" and comparison > 0:
            return False
        if operator == ">" and comparison <= 0:
            return False
        if operator == ">=" and comparison < 0:
            return False
    return True


def _soperator_upgrade_support_rules() -> tuple[Mapping[str, Any], ...]:
    payload = _load_soperator_migration_profile_data()
    rules = payload.get("support_rules") if isinstance(payload, Mapping) else None
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes, bytearray)):
        return ()
    return tuple(rule for rule in rules if isinstance(rule, Mapping))


def _soperator_upgrade_support_rule_matches(
    rule: Mapping[str, Any],
    *,
    source_version: str,
    target_version: str,
    target_chart_version: str,
    approved_target_chart_version: str,
    target_k8s_version: str,
) -> bool:
    source_range = str(rule.get("source_version_range", "") or "").strip()
    target_range = str(rule.get("target_version_range", "") or "").strip()
    target_chart_policy = str(rule.get("target_chart_version_policy", "") or "").strip()
    target_k8s_min = str(rule.get("target_k8s_min", "") or "").strip()
    target_k8s_max = str(rule.get("target_k8s_max", "") or "").strip()
    if source_range and not _support_version_range_matches(source_version, source_range):
        return False
    if target_range and not _support_version_range_matches(target_version, target_range):
        return False
    if target_chart_policy:
        chart_comparison = compare_chart_versions(
            target_chart_version,
            approved_target_chart_version,
        )
        if target_chart_policy == "cxcli_pin" and chart_comparison != "equal":
            return False
        if target_chart_policy == "not_cxcli_pin" and chart_comparison == "equal":
            return False
    if target_k8s_min and not _support_k8s_at_least(target_k8s_version, target_k8s_min):
        return False
    return not (target_k8s_max and _support_k8s_at_least(target_k8s_version, target_k8s_max))


def _soperator_upgrade_support_status_finding(
    *,
    source_version: str,
    target_version: str,
    approved_target_chart_version: str = "",
    current_k8s_version: str,
    target_k8s_version: str,
) -> SoperatorOnboardingFinding | None:
    source = normalize_soperator_release_version(source_version)
    target = normalize_soperator_release_version(target_version)
    target_chart = str(target_version or "").strip()
    approved_target_chart = str(approved_target_chart_version or target_chart).strip()
    target_k8s = normalize_k8s_minor_version(target_k8s_version)
    current_k8s = normalize_k8s_minor_version(current_k8s_version)
    if not source or not target:
        return None
    matched_rule: Mapping[str, Any] | None = None
    for rule in _soperator_upgrade_support_rules():
        if _soperator_upgrade_support_rule_matches(
            rule,
            source_version=source,
            target_version=target,
            target_chart_version=target_chart,
            approved_target_chart_version=approved_target_chart,
            target_k8s_version=target_k8s,
        ):
            matched_rule = rule
            break
    status = SOPERATOR_UPGRADE_SUPPORT_STATUS_NOT_VALIDATED
    rule_id = "default-not-validated"
    message = (
        "No committed cxcli Soperator/Kubernetes support rule matched this source, "
        "target, and Kubernetes target version path. The path remains blocked until "
        "cxcli has an explicit committed validation rule."
    )
    references: tuple[str, ...] = ()
    recommended_order: Mapping[str, Any] | None = None
    if matched_rule is not None:
        status = str(matched_rule.get("status", "") or "").strip() or status
        rule_id = str(matched_rule.get("id", "") or "").strip() or rule_id
        message = str(matched_rule.get("message", "") or "").strip() or message
        raw_recommended_order = matched_rule.get("recommended_order")
        if isinstance(raw_recommended_order, Mapping):
            recommended_order = dict(raw_recommended_order)
        raw_references = matched_rule.get("references")
        if isinstance(raw_references, Sequence) and not isinstance(
            raw_references,
            (str, bytes, bytearray),
        ):
            references = tuple(str(item) for item in raw_references if str(item or "").strip())
    severity = "info"
    if status in SOPERATOR_UPGRADE_SUPPORT_REJECT_STATUSES:
        severity = "required"
    elif status == SOPERATOR_UPGRADE_SUPPORT_STATUS_SUPPORTED_WITH_WARNING:
        severity = "recommended"
    evidence: dict[str, Any] = {
        "rule_id": rule_id,
        "source_version": source,
        "target_version": target_chart or target,
        "target_app_version": target,
        "target_chart_version": target_chart,
        "approved_target_chart_version": approved_target_chart,
        "current_k8s_version": current_k8s,
        "target_k8s_version": target_k8s,
        "references": list(references),
    }
    if recommended_order:
        evidence["recommended_order"] = dict(recommended_order)
    return SoperatorOnboardingFinding(
        layer=SOPERATOR_UPGRADE_SUPPORT_LAYER,
        status=status,
        severity=severity,
        message=message,
        evidence=evidence,
    )


def soperator_upgrade_support_finding(
    *,
    source_version: str,
    target_version: str,
    approved_target_chart_version: str = "",
    current_k8s_version: str,
    target_k8s_version: str,
) -> Mapping[str, Any] | None:
    """Return the committed support-policy finding for one exact upgrade segment."""

    finding = _soperator_upgrade_support_status_finding(
        source_version=source_version,
        target_version=target_version,
        approved_target_chart_version=approved_target_chart_version,
        current_k8s_version=current_k8s_version,
        target_k8s_version=target_k8s_version,
    )
    return asdict(finding) if finding is not None else None


def soperator_upgrade_support_findings(
    report: SoperatorOnboardingReport | Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    raw_findings: Any
    if isinstance(report, SoperatorOnboardingReport):
        raw_findings = report.to_dict().get("findings")
    else:
        raw_findings = report.get("findings")
    if not isinstance(raw_findings, Sequence) or isinstance(
        raw_findings,
        (str, bytes, bytearray),
    ):
        return ()
    findings: list[Mapping[str, Any]] = []
    for raw_finding in raw_findings:
        if not isinstance(raw_finding, Mapping):
            continue
        if str(raw_finding.get("layer", "") or "").strip() == SOPERATOR_UPGRADE_SUPPORT_LAYER:
            findings.append(raw_finding)
    return tuple(findings)


def soperator_upgrade_support_rejected(
    report: SoperatorOnboardingReport | Mapping[str, Any],
) -> bool:
    for finding in soperator_upgrade_support_findings(report):
        if str(finding.get("status", "") or "").strip() in (
            SOPERATOR_UPGRADE_SUPPORT_REJECT_STATUSES
        ):
            return True
    return False


def _soperator_migration_profile_match_finding(
    *,
    profile: Mapping[str, Any],
    source_version: str,
    target_version: str,
) -> SoperatorOnboardingFinding:
    profile_id = str(profile.get("profile_id", "") or "").strip()
    if _soperator_migration_profile_is_generation_fallback(profile):
        return SoperatorOnboardingFinding(
            layer="migration-profile",
            status="matched-generation",
            severity="recommended",
            message=(
                "Detected Soperator release does not have an exact committed profile row; "
                f"using major-generation migration profile '{profile_id}'."
            ),
            evidence={
                "source_version": source_version,
                "target_version": target_version,
                "generation": str(profile.get("generation", "") or ""),
            },
        )
    return SoperatorOnboardingFinding(
        layer="migration-profile",
        status="matched",
        severity="info",
        message=f"Detected Soperator release matched migration profile '{profile_id}'.",
        evidence={
            "source_version": source_version,
            "target_version": target_version,
            "generation": str(profile.get("generation", "") or ""),
        },
    )


def _migration_phase(
    phase_id: str,
    title: str,
    *,
    status: str = "planned",
    progress_label: str = "",
    requires_customer_approval: bool = False,
    quiet_window: bool = False,
    notes: Sequence[str] = (),
) -> SoperatorMigrationPhase:
    return SoperatorMigrationPhase(
        id=phase_id,
        title=title,
        status=status,
        progress_label=progress_label,
        requires_customer_approval=requires_customer_approval,
        quiet_window=quiet_window,
        notes=tuple(str(note) for note in notes if str(note or "").strip()),
    )


def _migration_approval_phase_title(
    *,
    include_data_migration: bool,
    include_compute_migration: bool,
    include_soperator_upgrade: bool = False,
) -> str:
    if include_data_migration and include_compute_migration:
        return "Customer approval of role, NodeSet, SlurmCluster, and storage changes"
    if include_data_migration:
        return "Customer approval of SlurmCluster storage changes"
    if include_compute_migration:
        return "Customer approval of role, NodeSet, and SlurmCluster compute changes"
    if include_soperator_upgrade:
        return "Customer approval of Soperator chart upgrade and remediation"
    return "Customer approval of Soperator remediation plan"


def _external_upgrade_approval_action_title(
    *,
    include_data_migration: bool,
    include_compute_migration: bool,
    include_soperator_upgrade: bool = False,
) -> str:
    if include_data_migration and include_compute_migration:
        return "Approve Soperator role, storage, and SlurmCluster remediation"
    if include_data_migration:
        return "Approve Soperator storage and SlurmCluster remediation"
    if include_compute_migration:
        return "Approve Soperator role, NodeSet, and SlurmCluster remediation"
    if include_soperator_upgrade:
        return "Approve Soperator chart upgrade and remediation"
    return "Approve Soperator remediation"


def _default_soperator_migration_plan(
    *,
    include_data_migration: bool,
    include_compute_migration: bool = True,
    include_soperator_upgrade: bool = False,
    include_target_gpu_reconciliation: bool = False,
    include_external_node_template_upgrade: bool = False,
) -> tuple[SoperatorMigrationPhase, ...]:
    replacement_compute_required = bool(
        include_compute_migration
        or include_soperator_upgrade
        or include_external_node_template_upgrade
    )
    phases = [
        _migration_phase(
            "discovery-and-plan",
            "Discovery and migration plan generation",
            status="complete",
            progress_label="Discovering Soperator components...",
        ),
        _migration_phase(
            "customer-approval",
            _migration_approval_phase_title(
                include_data_migration=include_data_migration,
                include_compute_migration=(
                    include_compute_migration or include_external_node_template_upgrade
                ),
                include_soperator_upgrade=include_soperator_upgrade,
            ),
            requires_customer_approval=True,
        ),
    ]
    if include_data_migration:
        phases.append(
            _migration_phase(
                "create-aligned-sfs",
                "Create aligned Nebius SFS filesystems without rolling source nodes",
                progress_label="Creating aligned SFS filesystems...",
                requires_customer_approval=True,
                notes=(
                    "Create or reuse target filesystems; do not attach them by updating a source node group.",
                    "The temporary controller bridge nodes receive controller SFS at creation time.",
                ),
            )
        )
    if (
        include_data_migration
        or include_compute_migration
        or include_soperator_upgrade
        or include_external_node_template_upgrade
    ):
        phases.append(
            _migration_phase(
                "controller-ha-bridge",
                "Establish the temporary two-controller Slurm HA bridge",
                progress_label="Controller Bridge: transferring source authority to HA pair",
                requires_customer_approval=True,
                notes=(
                    "Create two fixed one-node bridge groups in distinct failure domains at the source Kubernetes version.",
                    "Fence the source writer before the final cold spool delta and atomic state promotion.",
                    "After the first bridge state write, recovery is roll-forward only.",
                ),
            )
        )
    if include_external_node_template_upgrade:
        phases.append(
            _migration_phase(
                "external-node-template-upgrade",
                "Upgrade the external MK8s control plane",
                progress_label="External MK8s Upgrade: control plane only",
                requires_customer_approval=True,
                notes=(
                    "Upgrade only the Nebius control plane in this phase; do not call Terraform.",
                    "Advance one Kubernetes minor at a time when needed.",
                    "Leave service and worker node groups untouched until compute migration.",
                    "Apply node-group changes through the onboarding-accepted in-place or "
                    "blue-green compute migration.",
                ),
            )
        )
    if include_target_gpu_reconciliation:
        phases.append(
            _migration_phase(
                "target-gpu-stack-remediation",
                "Reconcile target MK8s GPU operator stack",
                progress_label="Reconciling target GPU operator stack...",
                requires_customer_approval=True,
                notes=(
                    "Apply the target-scoped GPU Operator app row.",
                    "Apply the target-scoped Network Operator app row when the target GPU "
                    "shape requires RDMA.",
                    "Validate scheduler-visible GPU/RDMA and NCCL readiness after the "
                    "target replacement activation.",
                ),
            )
        )
    if include_data_migration:
        phases.append(
            _migration_phase(
                "online-bulk-data-sync",
                "Online bulk data sync from old storage to target SFS",
                progress_label="Data Migration: online bulk sync",
                notes=(
                    "Preserve ownership, ACLs, xattrs, symlinks, hardlinks, and timestamps.",
                    "Run while old storage remains the active writer.",
                ),
            )
        )
    if replacement_compute_required:
        if include_compute_migration:
            rolling_title = "Accepted compute migration with preserved running jobs"
            rolling_progress_label = (
                "Compute Migration: eligible groups advancing, busy workers retained, "
                "<running jobs> jobs remaining"
            )
            rolling_notes = (
                "Execute the onboarding-accepted in-place or blue-green mode.",
                "Validate exact provider, Kubernetes, Slurm, and workload identities.",
                "Keep each busy worker blocked until its allocation and epilog finish.",
            )
        elif include_soperator_upgrade:
            rolling_title = "Soperator chart upgrade on accepted target compute"
            rolling_progress_label = (
                "Soperator Upgrade: replacement compute verified, <running jobs> jobs remaining"
            )
            rolling_notes = (
                "Prepare compute according to the accepted migration mode.",
                "Apply target Soperator values against exact replacement identities.",
                "Verify target worker NodeSets before accepting production jobs.",
            )
        else:
            rolling_title = "Kubernetes target-version compute migration"
            rolling_progress_label = (
                "Kubernetes Upgrade: target-version replacement compute verified, "
                "<running jobs> jobs remaining"
            )
            rolling_notes = (
                "Use the accepted in-place or blue-green target-version mode.",
                "Keep every provider mutation bound to the accepted campaign.",
                "Keep each busy source worker until its allocation and epilog finish.",
            )
        phases.append(
            _migration_phase(
                "rolling-compute-migration",
                rolling_title,
                progress_label=rolling_progress_label,
                notes=rolling_notes,
            )
        )
    if include_data_migration or replacement_compute_required:
        if include_data_migration and replacement_compute_required:
            final_title = "Final Slurm controller, accounting, login, and storage-reference cutover"
            final_progress_label = "Data/Compute Migration: final delta and control-plane cutover"
            final_notes = (
                "Pause new scheduling or drain partitions according to customer policy.",
                "Run a final delta sync before updating Soperator values or CRs.",
                "Validate target NodeSets before accepting production jobs.",
            )
            validation_notes = (
                "Keep old storage and preserved worker node groups available until validation passes.",
            )
            retire_title = "Retire old storage and replaced service-role resources only after explicit approval"
        elif include_data_migration:
            final_title = "Final Slurm storage-reference cutover"
            final_progress_label = "Data Migration: final delta and storage-reference cutover"
            final_notes = (
                "Pause new scheduling or drain partitions according to customer policy.",
                "Run a final delta sync before updating Soperator values or CRs.",
            )
            validation_notes = ("Keep old storage resources available until validation passes.",)
            retire_title = "Retire old storage resources only after explicit approval"
        elif include_compute_migration:
            final_title = "Final Soperator compute and control-plane cutover"
            final_progress_label = "Compute Migration: final control-plane cutover"
            final_notes = (
                "Pause new scheduling or drain partitions according to customer policy.",
                "Validate target NodeSets and SlurmCluster reconciliation before accepting production jobs.",
            )
            validation_notes = (
                "Keep preserved worker node groups available until validation passes.",
            )
            retire_title = "Retire replaced service-role resources only after explicit approval"
        elif include_external_node_template_upgrade and not include_soperator_upgrade:
            final_title = "Final Kubernetes target-version compute and control-plane cutover"
            final_progress_label = "Kubernetes Upgrade: final target-version cutover"
            final_notes = (
                "Pause new scheduling according to customer policy.",
                "Validate target-version NodeSets and SlurmCluster reconciliation before "
                "accepting production jobs.",
            )
            validation_notes = (
                "Keep source node groups available until target-version validation passes.",
            )
            retire_title = "Retire replaced source node groups only after explicit approval"
        else:
            final_title = "Final Soperator chart cutover"
            final_progress_label = "Soperator Upgrade: final control-plane cutover"
            final_notes = (
                "Pause new scheduling according to customer policy.",
                "Validate target NodeSets and SlurmCluster reconciliation before accepting production jobs.",
            )
            validation_notes = (
                "Keep preserved worker node groups available until validation passes.",
            )
            retire_title = "Retire old Soperator resources only after explicit approval"
        phases.extend(
            [
                _migration_phase(
                    "final-control-plane-cutover",
                    final_title,
                    progress_label=final_progress_label,
                    requires_customer_approval=True,
                    quiet_window=True,
                    notes=final_notes,
                ),
                *(
                    (
                        _migration_phase(
                            POPULATE_JAIL_REFRESH_PHASE_ID,
                            "Jail Upgrade: refresh shared Soperator jail rootfs",
                            progress_label=(
                                "Jail Upgrade: populate passive active/passive rootfs slot"
                            ),
                            notes=(
                                "Populate the passive jail rootfs slot with the target image.",
                                "Keep persistent jail mounts outside the rootfs slots before "
                                "switching the canonical jail alias and all enabled consumers.",
                                "Require controller, SConfigController, login, worker, and REST "
                                "consumers to use the active slot and be "
                                "Ready before restoring Slurm partitions; keep the previous "
                                "slot available for rollback.",
                            ),
                        ),
                    )
                    if include_soperator_upgrade
                    else ()
                ),
                _migration_phase(
                    "validation-and-rollback-hold",
                    "Validation and rollback hold",
                    notes=validation_notes,
                ),
                _migration_phase(
                    "retire-old-resources",
                    retire_title,
                    requires_customer_approval=True,
                ),
            ]
        )
    return tuple(phases)


def soperator_onboarding_report_for_modes(
    report: SoperatorOnboardingReport,
    *,
    storage_mode: str,
    compute_mode: str,
) -> SoperatorOnboardingReport:
    """Return a report whose selected actions and phases match operator choices."""

    include_data_migration = storage_mode == ONBOARDING_STORAGE_MODE_CREATE_ALIGNED_SFS
    include_compute_migration = compute_mode == ONBOARDING_COMPUTE_MODE_CREATE_ALIGNED_NODE_GROUPS
    include_soperator_upgrade = any(
        action.id == ONBOARDING_ACTION_UPGRADE_SOPERATOR and action.selected
        for action in report.actions
    )
    filtered_actions: list[SoperatorOnboardingAction] = []
    for action in report.actions:
        if (
            action.id
            in {
                ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
                ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
            }
            and not include_data_migration
        ):
            continue
        if action.id == ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION and not include_compute_migration:
            continue
        filtered_actions.append(action)
    required_mode_actions: list[str] = []
    if include_data_migration:
        required_mode_actions.extend(
            [
                ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
                ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
            ]
        )
    if include_compute_migration:
        required_mode_actions.append(ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION)
    selected_ids = {action.id for action in filtered_actions if action.selected}
    analyzed_actions = {action.id: action for action in report.actions}
    insertion_index = next(
        (
            index
            for index, action in enumerate(filtered_actions)
            if action.id == ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE
        ),
        len(filtered_actions),
    )
    for action_id in required_mode_actions:
        if action_id in selected_ids:
            continue
        action = _configured_soperator_action(
            action_id,
            selected_ids=selected_ids | set(required_mode_actions),
            analyzed_actions=analyzed_actions,
        )
        if action is None:
            raise RuntimeError(f"No Soperator onboarding action template exists for {action_id}.")
        filtered_actions.insert(insertion_index, action)
        insertion_index += 1
        selected_ids.add(action_id)
    filtered_actions = [
        replace(
            action,
            title=_external_upgrade_approval_action_title(
                include_data_migration=include_data_migration,
                include_compute_migration=include_compute_migration,
                include_soperator_upgrade=include_soperator_upgrade,
            ),
        )
        if action.id == ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE
        else action
        for action in filtered_actions
    ]
    migration_plan: tuple[SoperatorMigrationPhase, ...] = ()
    if report.migration_plan:
        selected_ids = {action.id for action in filtered_actions if action.selected}
        migration_plan = _default_soperator_migration_plan(
            include_target_gpu_reconciliation=ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK
            in selected_ids,
            include_external_node_template_upgrade=(
                ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE in selected_ids
            ),
            include_soperator_upgrade=ONBOARDING_ACTION_UPGRADE_SOPERATOR in selected_ids,
            include_data_migration=bool(
                selected_ids
                & {
                    ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
                    ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
                }
            ),
            include_compute_migration=ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION in selected_ids,
        )
    remediation = tuple(
        item
        for item in report.remediation
        if include_data_migration or item.classification != "data-sensitive"
    )
    return replace(
        report,
        actions=_dedupe_soperator_actions(filtered_actions),
        remediation=remediation,
        migration_plan=migration_plan,
    )


def _remediation_items_for_profile(
    *,
    profile: Mapping[str, Any] | None,
    storage_present: bool,
    target_version: str,
) -> tuple[SoperatorRemediationItem, ...]:
    items: list[SoperatorRemediationItem] = [
        SoperatorRemediationItem(
            id="upgrade-to-pinned-target",
            title=f"Upgrade or replace Soperator resources to pinned target {target_version}",
            classification="disruptive",
            reason="Live Soperator version differs from the cxcli catalog target.",
            requires_customer_approval=True,
        ),
        SoperatorRemediationItem(
            id="role-layout-approval",
            title="Approve role layout, NodeSets, and SlurmCluster spec changes",
            classification="customer-approval-required",
            reason=(
                "Role, NodeSet, scheduling, accounting, REST, or controller changes can affect "
                "running Slurm workflows."
            ),
            requires_customer_approval=True,
        ),
    ]
    if not storage_present:
        items.append(
            SoperatorRemediationItem(
                id="aligned-sfs-data-migration",
                title="Create aligned SFS filesystems and migrate data before storage cutover",
                classification="data-sensitive",
                reason=(
                    "Old storage remains active while data is copied to new SFS; final delta "
                    "requires a controlled Slurm quiet window."
                ),
                requires_customer_approval=True,
            )
        )
    return tuple(items)


def _dedupe_soperator_actions(
    actions: Sequence[SoperatorOnboardingAction],
) -> tuple[SoperatorOnboardingAction, ...]:
    deduped: list[SoperatorOnboardingAction] = []
    seen: set[str] = set()
    for action in actions:
        action_id = str(action.id or "").strip()
        if action_id and action_id in seen:
            continue
        if action_id:
            seen.add(action_id)
        deduped.append(action)
    return tuple(deduped)


def _onboarding_action_ids(onboarding: Mapping[str, Any]) -> tuple[str, ...]:
    actions = onboarding.get("actions", [])
    if not isinstance(actions, list):
        return ()
    ids: list[str] = []
    seen: set[str] = set()
    for action in actions:
        action_id = str(action or "").strip()
        if not action_id or action_id in seen:
            continue
        ids.append(action_id)
        seen.add(action_id)
    return tuple(ids)


def _configured_soperator_action(
    action_id: str,
    *,
    selected_ids: set[str],
    analyzed_actions: Mapping[str, SoperatorOnboardingAction],
) -> SoperatorOnboardingAction | None:
    analyzed = analyzed_actions.get(action_id)
    if analyzed is not None:
        return replace(analyzed, selected=True)
    include_data_migration = bool(
        selected_ids
        & {
            ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
            ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
        }
    )
    include_compute_migration = ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION in selected_ids
    include_soperator_upgrade = ONBOARDING_ACTION_UPGRADE_SOPERATOR in selected_ids
    templates = {
        ONBOARDING_ACTION_INSTALL_SOPERATOR: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_INSTALL_SOPERATOR,
            title="Install Soperator and required dependencies",
            layer="soperator",
            required=True,
            selected=True,
            reason="Required because no Soperator was detected on the selected MK8s target.",
        ),
        ONBOARDING_ACTION_ADOPT_SOPERATOR: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_ADOPT_SOPERATOR,
            title="Adopt compatible existing Soperator release",
            layer="soperator",
            selected=True,
            reason="Existing resources must be adopted cautiously before cxcli manages them.",
        ),
        ONBOARDING_ACTION_UPGRADE_SOPERATOR: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_UPGRADE_SOPERATOR,
            title="Upgrade Soperator to the cxcli-pinned version",
            layer="versions",
            selected=True,
            reason="Upgrades are allowed when live version is older and profiled.",
        ),
        ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE,
            title=_external_upgrade_approval_action_title(
                include_data_migration=include_data_migration,
                include_compute_migration=include_compute_migration,
                include_soperator_upgrade=include_soperator_upgrade,
            ),
            layer="external-upgrade",
            selected=True,
            disruptive=True,
            reason="External upgrade changes require customer approval before execution.",
        ),
        ONBOARDING_ACTION_CONFIGURE_STORAGE: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_CONFIGURE_STORAGE,
            title="Configure Soperator storage",
            layer="storage-sfs",
            selected=True,
            disruptive=True,
            reason="Storage must match the target Soperator layout before onboarding.",
        ),
        ONBOARDING_ACTION_CREATE_ALIGNED_SFS: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
            title="Create aligned SFS filesystems before storage cutover",
            layer="storage-sfs",
            selected=True,
            disruptive=True,
            reason=(
                "The selected storage mode requires independently owned aligned SFS "
                "targets before cutover."
            ),
        ),
        ONBOARDING_ACTION_PLAN_DATA_MIGRATION: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
            title="Plan online bulk sync and final delta storage migration",
            layer="storage-sfs",
            selected=True,
            disruptive=True,
            reason="Storage data must be migrated without losing ownership or metadata.",
        ),
        ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
            title="Plan accepted compute migration",
            layer="placements",
            selected=True,
            disruptive=True,
            reason=(
                "Service and worker roles follow the separately accepted in-place or blue-green "
                "compute migration and its workload-specific safety gates."
            ),
        ),
        ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
            title="Reconcile target MK8s GPU stack and deploy-time validations",
            layer="gpu-stack",
            required=True,
            selected=True,
            reason=(
                "GPU workers require cxcli-owned GPU/RDMA operator desired state and "
                "deploy-time validation reports on the target cluster; this can adopt an "
                "already healthy live stack."
            ),
        ),
        ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
            title="Upgrade external MK8s control plane only",
            layer="mk8s-node-template",
            selected=True,
            disruptive=True,
            reason=(
                "External Soperator targets are not Terraform-owned, so cxcli advances "
                "the MK8s control plane directly. Node-group template alignment follows the "
                "separately accepted in-place or blue-green compute migration mode."
            ),
        ),
        ONBOARDING_ACTION_ENABLE_TOPOLOGY: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_ENABLE_TOPOLOGY,
            title="Enable Slurm topology profile after operator confirmation",
            layer="topology",
            selected=True,
            reason="Topology remains opt-in for onboarded clusters.",
        ),
    }
    return templates.get(action_id)


def _configured_soperator_actions(
    action_ids: Sequence[str],
    *,
    analyzed_actions: Sequence[SoperatorOnboardingAction],
) -> tuple[SoperatorOnboardingAction, ...]:
    analyzed_by_id = {action.id: action for action in analyzed_actions}
    selected_ids = set(action_ids)
    return _dedupe_soperator_actions(
        tuple(
            action
            for action_id in action_ids
            if (
                action := _configured_soperator_action(
                    action_id,
                    selected_ids=selected_ids,
                    analyzed_actions=analyzed_by_id,
                )
            )
            is not None
        )
    )


def _migration_plan_for_action_ids(
    action_ids: Sequence[str],
) -> tuple[SoperatorMigrationPhase, ...]:
    selected_ids = set(action_ids)
    if not selected_ids & ONBOARDING_EXTERNAL_UPGRADE_ACTION_IDS:
        return ()
    return _default_soperator_migration_plan(
        include_target_gpu_reconciliation=ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK
        in selected_ids,
        include_external_node_template_upgrade=(
            ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE in selected_ids
        ),
        include_soperator_upgrade=ONBOARDING_ACTION_UPGRADE_SOPERATOR in selected_ids,
        include_data_migration=bool(
            selected_ids
            & {
                ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
                ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
            }
        ),
        include_compute_migration=ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION in selected_ids,
    )


def soperator_migration_plan_for_actions(
    action_ids: Sequence[str],
) -> tuple[SoperatorMigrationPhase, ...]:
    """Return the canonical upgrade plan authorized by accepted action ids."""

    return _migration_plan_for_action_ids(action_ids)


def soperator_report_with_accepted_onboarding_contract(
    report: Mapping[str, Any],
    onboarding: Mapping[str, Any],
) -> dict[str, Any]:
    """Overlay accepted config authority without discarding live discovery evidence."""

    effective = copy.deepcopy(to_plain_data(dict(report)))
    if not isinstance(effective, dict):
        effective = {}
    collection_errors = onboarding.get("collection_errors")
    if isinstance(collection_errors, list) and collection_errors:
        return effective
    action_ids = _onboarding_action_ids(onboarding)
    if not action_ids:
        return effective

    analyzed_actions = {
        str(action.get("id", "") or "").strip(): action
        for action in effective.get("actions", []) or []
        if isinstance(action, Mapping) and str(action.get("id", "") or "").strip()
    }
    selected_ids = set(action_ids)
    selected_actions: list[dict[str, Any]] = []
    for action_id in action_ids:
        analyzed = analyzed_actions.get(action_id)
        if analyzed is not None:
            selected = copy.deepcopy(to_plain_data(dict(analyzed)))
            if not isinstance(selected, dict):
                selected = {}
            selected["id"] = action_id
            selected["selected"] = True
            selected_actions.append(selected)
            continue
        configured = _configured_soperator_action(
            action_id,
            selected_ids=selected_ids,
            analyzed_actions={},
        )
        if configured is None:
            raise ValueError(
                "Accepted Soperator onboarding action has no canonical implementation: "
                f"{action_id}. Rerun ext-soperator onboard with the current cxcli."
            )
        selected_actions.append(asdict(configured))

    findings = effective.get("findings")
    if isinstance(findings, list):
        effective["findings"] = [
            copy.deepcopy(to_plain_data(dict(finding)))
            for finding in findings
            if isinstance(finding, Mapping)
            and (
                not str(finding.get("action_id", "") or "").strip()
                or str(finding.get("action_id", "") or "").strip() in selected_ids
            )
        ]
    effective["actions"] = selected_actions
    effective["state"] = (
        str(onboarding.get("state", "") or "").strip()
        or str(effective.get("state", "") or "").strip()
    )
    for key in ("source_version", "target_version", "migration_profile_id"):
        accepted = str(onboarding.get(key, "") or "").strip()
        if accepted:
            effective[key] = accepted
    effective["migration_plan"] = [
        asdict(phase) for phase in _migration_plan_for_action_ids(action_ids)
    ]
    upgrade_path = onboarding.get("upgrade_path")
    if isinstance(upgrade_path, Mapping):
        effective["upgrade_path"] = copy.deepcopy(to_plain_data(dict(upgrade_path)))
    return effective


def _selected_report_action_ids(report: Mapping[str, Any]) -> set[str]:
    return {
        str(action.get("id", "") or "").strip()
        for action in report.get("actions", []) or []
        if isinstance(action, Mapping)
        and action.get("selected") is True
        and str(action.get("id", "") or "").strip()
    }


def soperator_runtime_report_with_accepted_upgrade_plan(
    report: Mapping[str, Any],
    onboarding: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the accepted plan while retaining and validating fresh live evidence."""

    effective = copy.deepcopy(to_plain_data(dict(report)))
    if not isinstance(effective, dict):
        effective = {}
    live_state = str(effective.get("state", "") or "").strip()
    if live_state not in ONBOARDING_ACCEPTABLE_STATES:
        raise ValueError(
            "Fresh Soperator discovery is not in an upgradeable state: "
            f"{live_state or '<missing>'}. Rerun ext-soperator onboard."
        )
    # Known analyzer actions remain fresh evidence; exact version, identity, mode,
    # and source-contract gates decide whether the locked campaign is still valid.
    # Keep this explicit instead of deriving it from ONBOARDING_ACTION_IDS so a new
    # analyzer action fails closed until its runtime semantics are reviewed here.
    reviewed_live_evidence_actions = {
        ONBOARDING_ACTION_ADOPT_SOPERATOR,
        ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE,
        ONBOARDING_ACTION_CONFIGURE_STORAGE,
        ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
        ONBOARDING_ACTION_ENABLE_TOPOLOGY,
        ONBOARDING_ACTION_INSTALL_SOPERATOR,
        ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
        ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
        ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
        ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
        ONBOARDING_ACTION_UPGRADE_SOPERATOR,
    }
    unexpected_actions = sorted(
        _selected_report_action_ids(effective) - reviewed_live_evidence_actions
    )
    if unexpected_actions:
        raise ValueError(
            "Fresh Soperator discovery selected action(s) not recognized by the accepted "
            "runtime contract: "
            + ", ".join(unexpected_actions)
            + ". Rerun ext-soperator onboard before upgrade."
        )
    action_ids = _onboarding_action_ids(onboarding)
    effective["migration_plan"] = [
        asdict(phase) for phase in _migration_plan_for_action_ids(action_ids)
    ]
    upgrade_path = onboarding.get("upgrade_path")
    if isinstance(upgrade_path, Mapping):
        effective["upgrade_path"] = copy.deepcopy(to_plain_data(dict(upgrade_path)))
    return effective


def _release_chart_identity(release: Mapping[str, Any]) -> str:
    explicit = str(release.get("chart_name", "") or "").strip()
    if explicit:
        return explicit.lower()
    chart = str(release.get("chart", "") or "").strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)-[0-9]+(?:[.+-].*)?$", chart)
    return (match.group(1) if match else chart).lower()


def _release_chart_version(release: Mapping[str, Any]) -> str:
    explicit = str(release.get("chart_version", "") or "").strip()
    if explicit:
        return explicit
    chart = str(release.get("chart", "") or "").strip()
    match = re.match(r"^[A-Za-z0-9_.-]+-([0-9]+(?:\.[0-9]+){0,3}(?:[-+][A-Za-z0-9_.-]+)?)$", chart)
    return match.group(1) if match else ""


def _release_detected_version(release: Mapping[str, Any]) -> str:
    for value in (
        _release_chart_version(release),
        str(release.get("app_version", "") or "").strip(),
        str(release.get("appVersion", "") or "").strip(),
        str(release.get("version", "") or "").strip(),
    ):
        normalized = normalize_soperator_release_version(value)
        if normalized:
            return normalized
    return ""


def _resource_metadata(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = resource.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _resource_labels(resource: Mapping[str, Any]) -> Mapping[str, Any]:
    labels = _resource_metadata(resource).get("labels")
    return labels if isinstance(labels, Mapping) else {}


def _soperator_resource_release_candidate(
    snapshot: Mapping[str, Any],
    *,
    namespace: str = "",
    release_name: str = "",
) -> Mapping[str, Any] | None:
    resources = snapshot.get("soperator_resources")
    if not isinstance(resources, Sequence) or isinstance(resources, (str, bytes, bytearray)):
        return None
    requested_namespace = str(namespace or "").strip().lower()
    requested_release = str(release_name or "").strip().lower()
    candidates: list[Mapping[str, Any]] = []
    for resource in resources:
        if not isinstance(resource, Mapping):
            continue
        labels = _resource_labels(resource)
        chart = str(labels.get("helm.sh/chart", "") or "").strip()
        app_version = str(labels.get("app.kubernetes.io/version", "") or "").strip()
        instance_name = str(labels.get("app.kubernetes.io/instance", "") or "").strip()
        app_name = str(labels.get("app.kubernetes.io/name", "") or "").strip()
        metadata = _resource_metadata(resource)
        resource_namespace = str(metadata.get("namespace", "") or "soperator").strip()
        if not chart and not app_version:
            continue
        identity = " ".join((chart, app_version, instance_name, app_name)).lower()
        if "soperator" not in identity and "slurm-operator" not in identity:
            continue
        if requested_namespace and resource_namespace.lower() != requested_namespace:
            continue
        if requested_release and instance_name.lower() != requested_release:
            continue
        candidates.append(
            {
                "name": instance_name or "soperator",
                "namespace": resource_namespace,
                "chart": chart,
                "chart_version": _release_chart_version({"chart": chart}),
                "app_version": app_version,
                "status": "resource-labels",
                "detected_from": "kubernetes-resource-labels",
            }
        )
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            str(item.get("namespace", "") or "").lower() != "soperator",
            str(item.get("name", "") or "").lower() != "soperator",
            str(item.get("namespace", "") or ""),
            str(item.get("name", "") or ""),
            str(item.get("chart", "") or ""),
        ),
    )[0]


def _release_detected_summary(
    release: Mapping[str, Any],
    *,
    source_version: str,
    migration_profile_id: str = "",
) -> str:
    parts = [
        f"name={_release_name(release) or 'unknown'}",
        f"namespace={_release_namespace(release) or 'unknown'}",
        f"chart={str(release.get('chart', '') or '').strip() or 'unknown'}",
        f"version={source_version or _release_detected_version(release) or 'unknown'}",
    ]
    profile = str(migration_profile_id or "").strip()
    suffix = f"; matched migration profile {profile}" if profile else ""
    return "Detected Soperator Helm release: " + ", ".join(parts) + suffix + "."


def _release_namespace(release: Mapping[str, Any]) -> str:
    return str(release.get("namespace", "") or "").strip()


def _release_name(release: Mapping[str, Any]) -> str:
    return str(release.get("name", "") or "").strip().lower()


def _release_status(release: Mapping[str, Any]) -> str:
    return str(release.get("status", "") or "").strip().lower()


def _is_soperator_release_candidate(release: Mapping[str, Any]) -> bool:
    release_name = _release_name(release)
    return (
        release_name in SOPERATOR_COMPATIBLE_RELEASE_NAMES
        or release_name in SOPERATOR_COMPATIBLE_CONTROLLER_RELEASE_NAMES
        or _release_chart_identity(release) in SOPERATOR_COMPATIBLE_CHART_IDENTITIES
    )


def _has_known_soperator_release_name(release: Mapping[str, Any]) -> bool:
    release_name = _release_name(release)
    return (
        release_name in SOPERATOR_COMPATIBLE_RELEASE_NAMES
        or release_name in SOPERATOR_COMPATIBLE_CONTROLLER_RELEASE_NAMES
    )


def _is_compatible_soperator_release(release: Mapping[str, Any]) -> bool:
    release_name = _release_name(release)
    namespace = _release_namespace(release).lower()
    chart_identity = _release_chart_identity(release)
    if release_name in SOPERATOR_COMPATIBLE_RELEASE_NAMES:
        return namespace in {"", "soperator"} and chart_identity in {
            "",
            *SOPERATOR_COMPATIBLE_CHART_IDENTITIES,
        }
    if release_name in SOPERATOR_COMPATIBLE_CONTROLLER_RELEASE_NAMES:
        return namespace == "soperator-system" and chart_identity == "helm-soperator"
    return False


def _release_identity_key(release: Mapping[str, Any]) -> tuple[str, str]:
    return (_release_namespace(release).lower(), _release_name(release))


def _is_shadowed_stale_source_release(
    release: Mapping[str, Any],
    *,
    compatible_release: Mapping[str, Any] | None,
    target_version: str,
) -> bool:
    if compatible_release is None:
        return False
    if _release_identity_key(release) != _release_identity_key(compatible_release):
        return False
    target = normalize_soperator_release_version(target_version)
    compatible_version = _release_detected_version(compatible_release)
    stale_version = _release_detected_version(release)
    if not target or compatible_version != target or not stale_version:
        return False
    return compare_chart_versions(stale_version, target) == "older"


def _node_groups_with_topology_labels(node_groups: Mapping[str, Any]) -> set[str]:
    groups: set[str] = set()
    for raw_key, raw_group in node_groups.items():
        key = normalize_component_token(raw_key)
        if not key or not isinstance(raw_group, Mapping):
            continue
        labels = raw_group.get("labels") or raw_group.get("node_labels")
        if not isinstance(labels, Mapping):
            continue
        if any(str(label).startswith("topology.nebius.com/tier-") for label in labels):
            groups.add(key)
    return groups


def _node_groups_with_rdma(node_groups: Mapping[str, Any]) -> set[str]:
    groups: set[str] = set()
    for raw_key, raw_group in node_groups.items():
        key = normalize_component_token(raw_key)
        if not key or not isinstance(raw_group, Mapping):
            continue
        resources = raw_group.get("allocatable")
        if not isinstance(resources, Mapping):
            continue
        if any(str(resource).startswith("rdma/") for resource in resources):
            groups.add(key)
    return groups


def _node_groups_with_allocatable_gpu(node_groups: Mapping[str, Any]) -> set[str]:
    groups: set[str] = set()
    for raw_key, raw_group in node_groups.items():
        key = normalize_component_token(raw_key)
        if not key or not isinstance(raw_group, Mapping):
            continue
        resources = raw_group.get("allocatable")
        if not isinstance(resources, Mapping):
            continue
        if any(
            str(resource).startswith("nvidia.com/gpu") and str(value) not in {"", "0"}
            for resource, value in resources.items()
        ):
            groups.add(key)
    return groups


def _node_group_label_values(
    node_groups: Mapping[str, Any],
    groups: set[str],
    label_key: str,
) -> tuple[str, ...]:
    values: set[str] = set()
    for raw_key, raw_group in node_groups.items():
        key = normalize_component_token(raw_key)
        if key not in groups or not isinstance(raw_group, Mapping):
            continue
        labels = raw_group.get("labels")
        if not isinstance(labels, Mapping):
            continue
        value = str(labels.get(label_key, "") or "").strip()
        if value:
            values.add(value)
    return tuple(sorted(values))


def _gpu_stack_snapshot(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    value = snapshot.get("gpu_stack")
    return value if isinstance(value, Mapping) else {}


def _deployed_gpu_stack_release(
    releases: Sequence[Mapping[str, Any]],
    *,
    release_name: str,
) -> Mapping[str, Any] | None:
    normalized = release_name.strip().lower()
    for release in releases:
        if _release_name(release) != normalized:
            continue
        if _release_status(release) != "deployed":
            continue
        return release
    return None


def _helm_release_summary(release: Mapping[str, Any] | None) -> dict[str, str]:
    if release is None:
        return {}
    return {
        "name": str(release.get("name", "") or "").strip(),
        "namespace": str(release.get("namespace", "") or "").strip(),
        "chart": str(release.get("chart", "") or "").strip(),
        "app_version": str(release.get("app_version", "") or "").strip(),
        "status": str(release.get("status", "") or "").strip(),
    }


def _policy_resource_name(resource: Mapping[str, Any]) -> str:
    metadata = resource.get("metadata")
    if isinstance(metadata, Mapping):
        name = str(metadata.get("name", "") or "").strip()
        if name:
            return name
    return str(resource.get("name", "") or "").strip()


def _policy_resource_kind(resource: Mapping[str, Any]) -> str:
    return str(resource.get("kind", "") or "").strip().lower()


def _policy_resource_ready(resource: Mapping[str, Any]) -> bool:
    status = resource.get("status")
    if not isinstance(status, Mapping):
        return False
    state = str(status.get("state", "") or "").strip().lower()
    if state in {"ready", "reconciled"}:
        return True
    conditions = status.get("conditions")
    if not isinstance(conditions, Sequence) or isinstance(conditions, (str, bytes, bytearray)):
        return False
    for condition in conditions:
        if not isinstance(condition, Mapping):
            continue
        condition_type = str(condition.get("type", "") or "").strip().lower()
        condition_status = str(condition.get("status", "") or "").strip().lower()
        if condition_type == "ready" and condition_status == "true":
            return True
    return False


def _gpu_stack_policy_ready(
    policies: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    name: str,
) -> bool:
    normalized_kind = kind.strip().lower()
    normalized_name = name.strip().lower()
    for policy in policies:
        policy_kind = _policy_resource_kind(policy)
        policy_name = _policy_resource_name(policy).lower()
        if normalized_kind not in policy_kind or policy_name != normalized_name:
            continue
        if _policy_resource_ready(policy):
            return True
    return False


def _gpu_stack_discovery_evidence(
    *,
    snapshot: Mapping[str, Any],
    node_groups: Mapping[str, Any],
    gpu_groups: set[str],
    rdma_groups: set[str],
) -> dict[str, Any]:
    gpu_stack = _gpu_stack_snapshot(snapshot)
    releases = _sequence_of_mappings(gpu_stack.get("helm_releases"))
    policies = _sequence_of_mappings(gpu_stack.get("policies"))
    gpu_operator = _deployed_gpu_stack_release(releases, release_name="gpu-operator")
    network_operator = _deployed_gpu_stack_release(releases, release_name="network-operator")
    allocatable_gpu_groups = _node_groups_with_allocatable_gpu(node_groups)
    evidence: dict[str, Any] = {
        "gpu_node_groups": sorted(gpu_groups),
        "gpu_allocatable_node_groups": sorted(allocatable_gpu_groups),
        "rdma_allocatable_node_groups": sorted(rdma_groups),
        "driver_presets": list(
            _node_group_label_values(node_groups, gpu_groups, "nebius.com/drivers-preset")
        ),
        "nvidia_driver_versions": list(
            _node_group_label_values(node_groups, gpu_groups, "nebius.com/nvidia_driver_version")
        ),
        "cuda_versions": list(
            _node_group_label_values(node_groups, gpu_groups, "nebius.com/cuda_version")
        ),
    }
    gpu_operator_summary = _helm_release_summary(gpu_operator)
    if gpu_operator_summary:
        evidence["gpu_operator_release"] = gpu_operator_summary
    network_operator_summary = _helm_release_summary(network_operator)
    if network_operator_summary:
        evidence["network_operator_release"] = network_operator_summary
    cluster_policy_ready = _gpu_stack_policy_ready(
        policies,
        kind="clusterpolicy",
        name="cluster-policy",
    )
    nic_cluster_policy_ready = _gpu_stack_policy_ready(
        policies,
        kind="nicclusterpolicy",
        name="nic-cluster-policy",
    )
    if cluster_policy_ready:
        evidence["cluster_policy_ready"] = True
    if nic_cluster_policy_ready:
        evidence["nic_cluster_policy_ready"] = True
    evidence["live_evidence_available"] = bool(releases or policies)
    evidence["gpu_stack_verified"] = bool(
        gpu_operator is not None
        and cluster_policy_ready
        and gpu_groups
        and gpu_groups.issubset(allocatable_gpu_groups)
        and (not rdma_groups or (network_operator is not None and nic_cluster_policy_ready))
    )
    return evidence


def _k8s_minor_text(value: Any) -> str:
    raw = str(value or "").strip().lstrip("v")
    match = re.match(r"^(?P<major>[0-9]+)\.(?P<minor>[0-9]+)", raw)
    if match is None:
        return raw
    return f"{match.group('major')}.{match.group('minor')}"


def _k8s_minor_matches(value: Any, target: str) -> bool:
    current = _k8s_minor_text(value)
    target_minor = _k8s_minor_text(target)
    return bool(current and target_minor and current == target_minor)


def _mapping_text(value: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        text = str(value.get(key, "") or "").strip()
        if text:
            return text
    return ""


def _provider_mk8s_cluster(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    provider = snapshot.get("provider")
    if not isinstance(provider, Mapping):
        return {}
    cluster = provider.get("mk8s_cluster")
    return cluster if isinstance(cluster, Mapping) else {}


def _provider_control_plane_version(snapshot: Mapping[str, Any]) -> str:
    cluster = _provider_mk8s_cluster(snapshot)
    return _mapping_text(
        cluster,
        "control_plane_version",
        "controlPlaneVersion",
        "k8s_version",
        "kubernetes_version",
        "version",
    )


def _provider_node_template(raw_group: Mapping[str, Any]) -> Mapping[str, Any]:
    template = raw_group.get("node_template")
    if isinstance(template, Mapping):
        return template
    provider = raw_group.get("provider")
    if isinstance(provider, Mapping):
        template = provider.get("node_template")
        if isinstance(template, Mapping):
            return template
    return {}


def _provider_node_group_id(raw_group: Mapping[str, Any]) -> str:
    for key in ("node_group_id", "id"):
        text = str(raw_group.get(key, "") or "").strip()
        if text:
            return text
    provider = raw_group.get("provider")
    if isinstance(provider, Mapping):
        for key in ("node_group_id", "id"):
            text = str(provider.get(key, "") or "").strip()
            if text:
                return text
    labels: dict[str, Any] = {}
    for field in ("labels", "node_labels"):
        raw_labels = raw_group.get(field)
        if isinstance(raw_labels, Mapping):
            labels.update(raw_labels)
    for label_key in ("nebius.com/node-group-id", "yandex.cloud/node-group-id"):
        text = str(labels.get(label_key, "") or "").strip()
        if text:
            return text
    return ""


def _provider_node_group_name(raw_group: Mapping[str, Any]) -> str:
    for key in ("node_group_name", "name"):
        text = str(raw_group.get(key, "") or "").strip()
        if text:
            return text
    provider = raw_group.get("provider")
    if isinstance(provider, Mapping):
        for key in ("node_group_name", "name"):
            text = str(provider.get(key, "") or "").strip()
            if text:
                return text
    return ""


def _provider_template_k8s_version(template: Mapping[str, Any]) -> str:
    return _mapping_text(
        template,
        "k8s_version",
        "kubernetes_version",
        "version",
        "target_k8s_version",
    )


def _provider_template_os(template: Mapping[str, Any]) -> str:
    raw_os = template.get("os")
    if isinstance(raw_os, Mapping):
        return _mapping_text(raw_os, "name", "id", "value")
    return str(raw_os or template.get("target_os", "") or "").strip()


def _provider_template_gpu_stack_preset(template: Mapping[str, Any]) -> str:
    gpu_settings = template.get("gpu_settings")
    if not isinstance(gpu_settings, Mapping):
        gpu_settings = template.get("gpuSettings")
    if isinstance(gpu_settings, Mapping):
        text = _mapping_text(gpu_settings, "drivers_preset", "driversPreset")
        if text:
            return text
    return _mapping_text(
        template,
        "gpu_stack_preset",
        "drivers_preset",
        "driversPreset",
        "target_gpu_stack_preset",
    )


def _node_template_inventory_analysis(
    *,
    snapshot: Mapping[str, Any],
    node_groups: Mapping[str, Any],
    gpu_groups: set[str],
    target_k8s_version: str | None = None,
) -> dict[str, Any]:
    target_k8s = (
        normalize_k8s_minor_version(target_k8s_version or "")
        or ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION
    )
    target_os = ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_OS
    target_gpu = ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_GPU_STACK_PRESET
    control_plane_version = _provider_control_plane_version(snapshot)
    provider_collection_errors = _sequence_of_mappings(snapshot.get("provider_collection_errors"))
    control_plane_matches = (
        _k8s_minor_matches(control_plane_version, target_k8s) if control_plane_version else False
    )
    matched: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    provider_templates = 0
    for raw_group_name, raw_group in sorted(node_groups.items()):
        group_name = normalize_component_token(raw_group_name)
        if not group_name or not isinstance(raw_group, Mapping):
            continue
        node_group_id = _provider_node_group_id(raw_group)
        node_group_name = _provider_node_group_name(raw_group)
        template = _provider_node_template(raw_group)
        if not template:
            unknown.append(
                {
                    "node_group": group_name,
                    "node_group_id": node_group_id,
                    "node_group_name": node_group_name,
                    "reason": "provider node-template inventory missing",
                }
            )
            continue
        provider_templates += 1
        current_k8s = _provider_template_k8s_version(template)
        current_os = _provider_template_os(template)
        current_gpu = _provider_template_gpu_stack_preset(template)
        expected_gpu = target_gpu if group_name in gpu_groups else ""
        reasons: list[str] = []
        if not _k8s_minor_matches(current_k8s, target_k8s):
            reasons.append(f"Kubernetes {current_k8s or 'unknown'} != target {target_k8s}")
        if current_os != target_os:
            reasons.append(f"OS {current_os or 'unknown'} != target {target_os}")
        if group_name in gpu_groups:
            if current_gpu != target_gpu:
                reasons.append(
                    f"GPU driver preset {current_gpu or 'driverless'} != target {target_gpu}"
                )
        elif current_gpu:
            reasons.append(f"CPU node group still has GPU driver preset {current_gpu}")
        row = {
            "node_group": group_name,
            "node_group_id": node_group_id,
            "node_group_name": node_group_name,
            "current_k8s_version": current_k8s,
            "current_os": current_os,
            "current_gpu_stack_preset": current_gpu,
            "target_k8s_version": target_k8s,
            "target_os": target_os,
            "target_gpu_stack_preset": expected_gpu,
        }
        if reasons:
            row["reasons"] = reasons
            remaining.append(row)
        else:
            matched.append(row)
    provider_available = bool(control_plane_version or provider_templates)
    control_plane = {
        "current_k8s_version": control_plane_version,
        "target_k8s_version": target_k8s,
        "matches": control_plane_matches,
    }
    return {
        "provider_inventory_available": provider_available,
        "complete": bool(
            provider_available
            and control_plane_matches
            and not provider_collection_errors
            and not remaining
            and not unknown
            and provider_templates
        ),
        "control_plane": control_plane,
        "matched_node_groups": matched,
        "remaining_node_groups": remaining,
        "unknown_node_groups": unknown,
        "target_k8s_version": target_k8s,
        "target_os": target_os,
        "target_gpu_stack_preset": target_gpu,
        "provider_collection_errors": [dict(item) for item in provider_collection_errors],
    }


def _node_template_inventory_finding_evidence(analysis: Mapping[str, Any]) -> dict[str, Any]:
    matched = _sequence_of_mappings(analysis.get("matched_node_groups"))
    remaining = _sequence_of_mappings(analysis.get("remaining_node_groups"))
    unknown = _sequence_of_mappings(analysis.get("unknown_node_groups"))
    return {
        "control_plane": analysis.get("control_plane", {}),
        "target_k8s_version": analysis.get("target_k8s_version", ""),
        "target_os": analysis.get("target_os", ""),
        "target_gpu_stack_preset": analysis.get("target_gpu_stack_preset", ""),
        "matched_node_group_count": len(matched),
        "matched_node_groups": [
            str(item.get("node_group", "") or "").strip()
            for item in matched[:20]
            if str(item.get("node_group", "") or "").strip()
        ],
        "remaining_node_groups": [dict(item) for item in remaining[:50]],
        "unknown_node_groups": [dict(item) for item in unknown[:50]],
        "provider_collection_errors": [
            dict(item) for item in _sequence_of_mappings(analysis.get("provider_collection_errors"))
        ],
    }


def analyze_soperator_onboarding_snapshot(
    snapshot: Mapping[str, Any],
    *,
    target_ref: str,
    pinned_chart_version: str = "",
    pinned_app_version: str = "",
    approved_target_chart_version: str = "",
    source_version_override: str = "",
    target_k8s_version: str | None = None,
) -> SoperatorOnboardingReport:
    node_groups = snapshot.get("node_groups")
    if not isinstance(node_groups, Mapping):
        node_groups = {}
    cpu_groups, gpu_groups = _node_group_kinds(node_groups)
    releases = _sequence_of_mappings(snapshot.get("helm_releases"))
    soperator_candidates = tuple(
        release for release in releases if _is_soperator_release_candidate(release)
    )
    soperator_release = next(
        (release for release in soperator_candidates if _is_compatible_soperator_release(release)),
        None,
    )
    resource_release = (
        _soperator_resource_release_candidate(snapshot) if soperator_release is None else None
    )
    if soperator_release is None and resource_release is not None:
        soperator_release = resource_release
    crd_names = _sequence_of_names(snapshot.get("crds"))
    has_soperator_crds = any(name in SOPERATOR_CRD_NAMES for name in crd_names)
    namespace_names = _sequence_of_names(snapshot.get("namespaces"))
    pvcs = _sequence_of_mappings(snapshot.get("pvcs"))
    pvs = _sequence_of_mappings(snapshot.get("pvs"))
    storage = snapshot.get("storage") if isinstance(snapshot.get("storage"), Mapping) else {}
    storage_keys = _storage_layout_keys(storage=storage, pvcs=pvcs, pvs=pvs)
    rdma_groups = _node_groups_with_rdma(node_groups)
    topology_groups = _node_groups_with_topology_labels(node_groups)
    role_evidence = _node_group_role_evidence(node_groups)
    compute_layout_compatible = _compute_layout_target_compatible(node_groups)
    node_template_inventory = _node_template_inventory_analysis(
        snapshot=snapshot,
        node_groups=node_groups,
        gpu_groups=gpu_groups,
        target_k8s_version=target_k8s_version,
    )
    manual_source_version = normalize_soperator_release_version(source_version_override)
    target_version = normalize_soperator_release_version(pinned_chart_version or pinned_app_version)
    target_chart_version = str(pinned_chart_version or pinned_app_version or "").strip()
    approved_target_chart = str(approved_target_chart_version or target_chart_version).strip()
    target_k8s = str(node_template_inventory.get("target_k8s_version", "") or "").strip()
    external_node_template_required = not bool(node_template_inventory.get("complete"))

    findings: list[SoperatorOnboardingFinding] = []
    actions: list[SoperatorOnboardingAction] = []
    collection_errors = _sequence_of_mappings(snapshot.get("collection_errors"))
    if collection_errors:
        findings.append(
            SoperatorOnboardingFinding(
                layer="kubernetes",
                status="analysis-failed",
                severity="required",
                message=(
                    "Soperator onboarding analysis could not read the target cluster. "
                    "Fix kube context, auth, or network access and rerun the analysis."
                ),
                evidence={"errors": [dict(error) for error in collection_errors]},
            )
        )
        return SoperatorOnboardingReport(
            schema=ONBOARDING_SCHEMA,
            target_ref=normalize_component_token(target_ref),
            analyzed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            state=ONBOARDING_STATE_ANALYSIS_INCOMPLETE,
            fingerprint=_analysis_fingerprint(snapshot),
            findings=tuple(findings),
            actions=(),
        )

    if not node_groups:
        findings.append(
            SoperatorOnboardingFinding(
                layer="kubernetes",
                status="inventory-missing",
                severity="required",
                message="No node-group inventory was discovered for this target.",
                action_id="refresh-inventory",
            )
        )
    else:
        findings.append(
            SoperatorOnboardingFinding(
                layer="kubernetes",
                status="ok",
                severity="info",
                message=(
                    f"Discovered {len(cpu_groups)} CPU node group(s) and "
                    f"{len(gpu_groups)} GPU node group(s)."
                ),
            )
        )
        missing_selector_labels = _node_groups_missing_selector_labels(node_groups)
        if missing_selector_labels:
            findings.append(
                SoperatorOnboardingFinding(
                    layer="placements",
                    status="selector-required",
                    severity="required",
                    message=(
                        "Discovered node groups are missing a usable selector label. "
                        "Label nodes with nebius.com/node-group, yandex.cloud/node-group-id, "
                        "or node.kubernetes.io/instance-type, or provide explicit "
                        "inventory.node_groups.<group>.selector before onboarding."
                    ),
                    evidence={"node_groups": sorted(missing_selector_labels)},
                )
            )
        if "soperator" not in namespace_names:
            findings.append(
                SoperatorOnboardingFinding(
                    layer="soperator-base",
                    status="missing",
                    severity="required",
                    message="Namespace 'soperator' was not detected; cxcli will render it for install.",
                )
            )
        if compute_layout_compatible:
            findings.append(
                SoperatorOnboardingFinding(
                    layer="placements",
                    status="target-compatible",
                    severity="info",
                    message=(
                        "Existing Soperator service-role and worker node-group labels were "
                        "detected. cxcli can preserve these role and placement mappings on "
                        "the accepted in-place or blue-green compute migration whenever "
                        "upgrade work is required; compatibility alone never authorizes a "
                        "source node-group mutation."
                    ),
                    evidence={"roles": role_evidence},
                )
            )
        elif role_evidence:
            missing_roles = [
                role for role in (*ONBOARDING_SERVICE_ROLES, "worker") if role not in role_evidence
            ]
            findings.append(
                SoperatorOnboardingFinding(
                    layer="placements",
                    status="incomplete",
                    severity="recommended",
                    message=(
                        "Some Soperator node-group role labels were detected, but the "
                        "standard service-role and worker layout is incomplete. Missing "
                        "roles: " + ", ".join(missing_roles) + "."
                    ),
                    action_id=ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
                    evidence={"roles": role_evidence, "missing_roles": missing_roles},
                )
            )

    if not gpu_groups:
        findings.append(
            SoperatorOnboardingFinding(
                layer="gpu-rdma",
                status="warning",
                severity="recommended",
                message="No GPU node group was discovered; worker placement will be CPU-only.",
            )
        )
    else:
        gpu_stack_evidence = _gpu_stack_discovery_evidence(
            snapshot=snapshot,
            node_groups=node_groups,
            gpu_groups=gpu_groups,
            rdma_groups=rdma_groups,
        )
        if gpu_stack_evidence.get("gpu_stack_verified"):
            gpu_stack_status = "verified"
            gpu_stack_severity = "info"
            gpu_stack_message = (
                "Live GPU stack evidence is healthy for the discovered GPU node groups; "
                "cxcli will keep the target GPU stack selected for adoption/reconciliation "
                "with deploy-time GPU readiness/GPU visibility and explicit NCCL benchmark commands. "
                "This is not a failure signal."
            )
        elif gpu_stack_evidence.get("live_evidence_available"):
            gpu_stack_status = "reconcile-planned"
            gpu_stack_severity = "required"
            gpu_stack_message = (
                "GPU node groups were discovered, but live GPU-stack evidence is incomplete "
                "or not fully healthy in the onboarding snapshot; cxcli will reconcile the "
                "target GPU Operator, Network Operator when GPU-cluster/RDMA-capable, and "
                "deploy-time GPU readiness/GPU visibility plus explicit NCCL benchmark commands."
            )
        else:
            gpu_stack_status = "reconcile-planned"
            gpu_stack_severity = "required"
            gpu_stack_message = (
                "GPU node groups were discovered; cxcli will manage the target GPU stack as "
                "desired state, including GPU Operator, Network Operator when the target is "
                "GPU-cluster/RDMA-capable, deploy-time GPU readiness/GPU visibility, and "
                "explicit NCCL benchmark commands. This does not mean the current stack is broken."
            )
        findings.append(
            SoperatorOnboardingFinding(
                layer="gpu-stack",
                status=gpu_stack_status,
                severity=gpu_stack_severity,
                message=gpu_stack_message,
                action_id=ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
                evidence=gpu_stack_evidence,
            )
        )
        actions.append(
            SoperatorOnboardingAction(
                id=ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
                title="Reconcile target MK8s GPU stack and deploy-time validations",
                layer="gpu-stack",
                required=True,
                selected=True,
                reason=(
                    "GPU workers require cxcli-owned GPU/RDMA operator desired state and "
                    "deploy-time validation reports on the target cluster; this can adopt an "
                    "already healthy live stack."
                ),
            )
        )
    if gpu_groups and not rdma_groups:
        findings.append(
            SoperatorOnboardingFinding(
                layer="gpu-rdma",
                status="validation-planned",
                severity="required",
                message=(
                    "GPU node groups were discovered, but no scheduler-visible RDMA resources "
                    "were found in node allocatable data. If the target is "
                    "GPU-cluster/RDMA-capable, cxcli will reconcile Network Operator/RDMA "
                    "policy and verify scheduler-visible GPU/RDMA readiness and NCCL at "
                    "deploy time; for Ethernet-only GPU shapes this is not a failure by itself."
                ),
                action_id=ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
            )
        )

    if gpu_groups and topology_groups >= gpu_groups:
        findings.append(
            SoperatorOnboardingFinding(
                layer="topology",
                status="available",
                severity="optional",
                message="All GPU node groups expose topology.nebius.com/tier-* labels.",
                action_id=ONBOARDING_ACTION_ENABLE_TOPOLOGY,
            )
        )
        actions.append(
            SoperatorOnboardingAction(
                id=ONBOARDING_ACTION_ENABLE_TOPOLOGY,
                title="Enable Slurm topology profile after operator confirmation",
                layer="topology",
                selected=False,
                reason="Topology remains opt-in for onboarded clusters.",
            )
        )
    else:
        findings.append(
            SoperatorOnboardingFinding(
                layer="topology",
                status="disabled",
                severity="info",
                message=(
                    "Slurm topology should stay disabled until topology labels are complete "
                    "and verified for all worker groups."
                ),
            )
        )

    required_storage_keys = set(ONBOARDING_REQUIRED_STORAGE_KEYS)
    detected_required_storage_keys = storage_keys & required_storage_keys
    storage_present = detected_required_storage_keys == required_storage_keys
    storage_primitives_present = bool(storage_keys or pvcs or pvs)
    if storage_present:
        findings.append(
            SoperatorOnboardingFinding(
                layer="storage-sfs",
                status="target-compatible",
                severity="info",
                message=(
                    "Existing jail, controller-spool, and accounting storage layout was "
                    "detected and can be kept unchanged after customer approval."
                ),
                evidence={"storage_keys": sorted(storage_keys), "pvcs": len(pvcs), "pvs": len(pvs)},
            )
        )
    elif storage_primitives_present:
        missing_storage_keys = sorted(required_storage_keys - detected_required_storage_keys)
        findings.append(
            SoperatorOnboardingFinding(
                layer="storage-sfs",
                status="incompatible",
                severity="recommended",
                message=(
                    "Existing storage primitives were detected, but the target Soperator "
                    "storage layout is incomplete. Missing required storage keys: "
                    f"{', '.join(missing_storage_keys)}. Select create-aligned-sfs to "
                    "plan aligned Nebius SFS creation, or keep-existing-storage to "
                    "preserve the discovered storage contract without storage realignment."
                ),
                action_id=ONBOARDING_ACTION_CONFIGURE_STORAGE,
                evidence={
                    "storage_keys": sorted(storage_keys),
                    "missing_storage_keys": missing_storage_keys,
                    "pvcs": len(pvcs),
                    "pvs": len(pvs),
                },
            )
        )
        actions.extend(
            [
                SoperatorOnboardingAction(
                    id=ONBOARDING_ACTION_CONFIGURE_STORAGE,
                    title="Configure Soperator storage",
                    layer="storage-sfs",
                    selected=True,
                    disruptive=True,
                    reason="Storage must match the target Soperator layout before onboarding.",
                ),
                SoperatorOnboardingAction(
                    id=ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
                    title="Create aligned SFS filesystems before storage cutover",
                    layer="storage-sfs",
                    selected=True,
                    disruptive=True,
                    reason="Detected storage is not compatible with the target Soperator layout.",
                ),
            ]
        )
    else:
        findings.append(
            SoperatorOnboardingFinding(
                layer="storage-sfs",
                status="missing",
                severity="recommended",
                message=(
                    "Jail, controller-spool, and accounting storage were not detected. "
                    "Select create-aligned-sfs to create production-aligned Nebius SFS "
                    "filesystems before storage cutover."
                ),
                action_id=ONBOARDING_ACTION_CONFIGURE_STORAGE,
            )
        )
        actions.append(
            SoperatorOnboardingAction(
                id=ONBOARDING_ACTION_CONFIGURE_STORAGE,
                title="Configure Soperator storage",
                layer="storage-sfs",
                selected=True,
                disruptive=True,
                reason="Storage is required for production-grade Soperator operation.",
            )
        )
        actions.append(
            SoperatorOnboardingAction(
                id=ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
                title="Create aligned SFS filesystems before storage cutover",
                layer="storage-sfs",
                selected=True,
                disruptive=True,
                reason="No compatible existing storage layout was discovered.",
            )
        )

    incompatible_releases = tuple(
        release for release in soperator_candidates if not _is_compatible_soperator_release(release)
    )
    shadowed_stale_releases = tuple(
        release
        for release in incompatible_releases
        if _is_shadowed_stale_source_release(
            release,
            compatible_release=soperator_release,
            target_version=target_version,
        )
    )
    reviewable_incompatible_releases = tuple(
        release for release in incompatible_releases if release not in shadowed_stale_releases
    )
    incompatible_release = next(
        (
            release
            for release in reviewable_incompatible_releases
            if _has_known_soperator_release_name(release)
        ),
        None,
    ) or next(iter(reviewable_incompatible_releases), None)
    if shadowed_stale_releases:
        findings.append(
            SoperatorOnboardingFinding(
                layer="soperator",
                status="stale-source-release",
                severity="info",
                message=(
                    "Canonical target Soperator Helm release is already deployed; older "
                    "same-release source-family Helm record(s) are recorded as stale cleanup "
                    "evidence and are not selected onboarding work."
                ),
                evidence={"releases": [dict(release) for release in shadowed_stale_releases]},
            )
        )

    if resource_release is not None:
        findings.append(
            SoperatorOnboardingFinding(
                layer="soperator",
                status="resource-label-version-detected",
                severity="info",
                message=(
                    "Detected Soperator version from Kubernetes resource labels because "
                    "Helm release metadata was not available."
                ),
                evidence={"release": dict(resource_release)},
            )
        )

    detected_source_release = (
        soperator_release
        if isinstance(soperator_release, Mapping)
        else incompatible_release
        if isinstance(incompatible_release, Mapping)
        and _has_known_soperator_release_name(incompatible_release)
        else None
    )
    detected_source_version = (
        _release_detected_version(detected_source_release)
        if isinstance(detected_source_release, Mapping)
        else ""
    )
    detected_source_profile = (
        soperator_migration_profile_for_version(
            detected_source_version,
            allow_generation_fallback=True,
        )
        if detected_source_version
        else None
    )
    manual_source_version_applies = bool(
        manual_source_version and (has_soperator_crds or incompatible_release is not None)
    )
    release_identity_needs_source_version = (
        incompatible_release is not None
        and not manual_source_version_applies
        and detected_source_profile is None
    )

    if incompatible_release is not None and not manual_source_version_applies:
        if release_identity_needs_source_version:
            state = ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN
            findings.append(
                SoperatorOnboardingFinding(
                    layer="soperator",
                    status="source-version-required",
                    severity="required",
                    message=(
                        "Existing Soperator-like Helm release has incompatible release name, "
                        "namespace, or chart identity. Select the source Soperator version so "
                        "cxcli can match an exact committed migration-profile row or known "
                        "major-generation profile before it manages the release."
                    ),
                    evidence={"release": dict(incompatible_release)},
                )
            )
        else:
            findings.append(
                SoperatorOnboardingFinding(
                    layer="soperator",
                    status="helm-release-detected",
                    severity="recommended",
                    message=(
                        _release_detected_summary(
                            incompatible_release,
                            source_version=detected_source_version,
                            migration_profile_id=str(
                                detected_source_profile.get("profile_id", "")
                                if isinstance(detected_source_profile, Mapping)
                                else ""
                            ),
                        )
                        + " No manual source-version input is required."
                    ),
                    evidence={
                        "release": dict(incompatible_release),
                        "source_version": detected_source_version,
                        "migration_profile_id": str(
                            detected_source_profile.get("profile_id", "")
                            if isinstance(detected_source_profile, Mapping)
                            else ""
                        ),
                    },
                )
            )
    elif incompatible_release is not None:
        state = ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN
        findings.append(
            SoperatorOnboardingFinding(
                layer="soperator",
                status="source-version-selected",
                severity="recommended",
                message=(
                    "Existing Soperator Helm release has non-standard identity; "
                    "operator-selected source version will be used for migration profile matching."
                ),
                evidence={
                    "release": dict(incompatible_release),
                    "source_version": manual_source_version,
                },
            )
        )
    elif soperator_release is None and not has_soperator_crds:
        state = ONBOARDING_STATE_NO_SOPERATOR_DETECTED
        findings.append(
            SoperatorOnboardingFinding(
                layer="soperator",
                status="missing",
                severity="required",
                message="Soperator Helm release and CRDs were not detected.",
                action_id=ONBOARDING_ACTION_INSTALL_SOPERATOR,
            )
        )
        actions.append(
            SoperatorOnboardingAction(
                id=ONBOARDING_ACTION_INSTALL_SOPERATOR,
                title="Install Soperator and required dependencies",
                layer="soperator",
                required=True,
                selected=True,
                reason="Required because no Soperator was detected on the selected MK8s target.",
            )
        )
    else:
        state = ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN
        findings.append(
            SoperatorOnboardingFinding(
                layer="soperator",
                status="detected",
                severity="info",
                message="Existing Soperator resources were detected.",
                action_id=ONBOARDING_ACTION_ADOPT_SOPERATOR,
                evidence={"release": dict(soperator_release or {})},
            )
        )
        if soperator_release is not None:
            actions.append(
                SoperatorOnboardingAction(
                    id=ONBOARDING_ACTION_ADOPT_SOPERATOR,
                    title="Adopt compatible existing Soperator release",
                    layer="soperator",
                    selected=True,
                    reason="Existing resources must be adopted cautiously before cxcli manages them.",
                )
            )

    source_version = detected_source_version or (
        manual_source_version if manual_source_version_applies else ""
    )
    migration_profile: Mapping[str, Any] | None = None
    migration_profile_id = ""
    remediation: tuple[SoperatorRemediationItem, ...] = ()
    migration_plan: tuple[SoperatorMigrationPhase, ...] = ()

    if source_version and (
        isinstance(detected_source_release, Mapping) or manual_source_version_applies
    ):
        if isinstance(detected_source_release, Mapping):
            live_chart = _release_chart_version(detected_source_release)
            live_app = str(detected_source_release.get("app_version", "") or "").strip()
        else:
            live_chart = source_version
            live_app = source_version
        if manual_source_version_applies and not detected_source_version:
            findings.append(
                SoperatorOnboardingFinding(
                    layer="versions",
                    status="source-version-selected",
                    severity="info",
                    message=(
                        "Operator selected a Soperator source version because discovery did not "
                        "find a compatible Helm release version."
                    ),
                    evidence={"source_version": source_version},
                )
            )
        comparison = compare_chart_versions(live_chart, pinned_chart_version)
        app_comparison = compare_chart_versions(live_app, pinned_app_version)
        release_matches_target = (
            isinstance(soperator_release, Mapping)
            and not release_identity_needs_source_version
            and comparison == "equal"
            and (app_comparison in {"equal", "unknown"} or not live_app or not pinned_app_version)
        )
        if release_matches_target:
            migration_profile = soperator_migration_profile_for_version(
                source_version,
                allow_generation_fallback=True,
            )
            if migration_profile is not None:
                migration_profile_id = str(migration_profile.get("profile_id", "") or "").strip()
                findings.append(
                    _soperator_migration_profile_match_finding(
                        profile=migration_profile,
                        source_version=source_version,
                        target_version=target_version,
                    )
                )
            state = ONBOARDING_STATE_EXISTING_SOPERATOR_TARGET
            findings.append(
                SoperatorOnboardingFinding(
                    layer="versions",
                    status="target-version",
                    severity="info",
                    message="Existing Soperator version matches the cxcli-pinned target.",
                    evidence={"live_chart": live_chart, "live_app": live_app},
                )
            )
            if external_node_template_required:
                provider_inventory_available = bool(
                    node_template_inventory.get("provider_inventory_available")
                )
                migration_plan = _default_soperator_migration_plan(
                    include_target_gpu_reconciliation=any(
                        action.id == ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK
                        and action.selected
                        for action in actions
                    ),
                    include_external_node_template_upgrade=True,
                    include_soperator_upgrade=False,
                    include_data_migration=False,
                    include_compute_migration=False,
                )
                findings.append(
                    SoperatorOnboardingFinding(
                        layer="mk8s-node-template",
                        status="remediation-planned",
                        severity="required",
                        message=(
                            "The external MK8s control plane will be upgraded directly; "
                            "node-group template alignment follows the separately accepted "
                            "in-place or blue-green compute migration mode."
                            if provider_inventory_available
                            else (
                                "External MK8s control-plane and node-group provider "
                                "inventory was not available during onboarding; cxcli will "
                                "verify the control-plane hop and accepted target compute "
                                "plan during the external upgrade."
                            )
                        ),
                        action_id=ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
                        evidence=_node_template_inventory_finding_evidence(node_template_inventory),
                    )
                )
                actions.append(
                    SoperatorOnboardingAction(
                        id=ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
                        title="Upgrade external MK8s control plane only",
                        layer="mk8s-node-template",
                        selected=True,
                        disruptive=True,
                        reason=(
                            "The Soperator chart is already current, but the external MK8s "
                            "control plane has not reached the requested Kubernetes target; "
                            "compute alignment follows the separately accepted migration mode."
                        ),
                    )
                )
                actions.append(
                    SoperatorOnboardingAction(
                        id=ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE,
                        title="Approve external MK8s control-plane remediation",
                        layer="external-upgrade",
                        selected=True,
                        disruptive=True,
                        reason="External control-plane changes require customer approval before execution.",
                    )
                )
        else:
            migration_profile = soperator_migration_profile_for_version(
                source_version,
                allow_generation_fallback=True,
            )
            if migration_profile is None:
                state = ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN
                findings.append(
                    SoperatorOnboardingFinding(
                        layer="migration-profile",
                        status="profile-missing",
                        severity="required",
                        message=(
                            "Detected Soperator release does not match an exact committed "
                            "cxcli migration profile or known generation profile. Refresh "
                            "the profile history before onboarding."
                        ),
                        evidence={"source_version": source_version or live_chart or live_app},
                    )
                )
            else:
                migration_profile_id = str(migration_profile.get("profile_id", "") or "").strip()
                findings.append(
                    _soperator_migration_profile_match_finding(
                        profile=migration_profile,
                        source_version=source_version,
                        target_version=target_version,
                    )
                )
        if comparison == "older" or app_comparison == "older":
            control_plane_inventory = node_template_inventory.get("control_plane")
            current_k8s = (
                str(control_plane_inventory.get("current_k8s_version", "") or "").strip()
                if isinstance(control_plane_inventory, Mapping)
                else ""
            )
            support_finding = _soperator_upgrade_support_status_finding(
                source_version=source_version,
                target_version=target_chart_version or target_version,
                approved_target_chart_version=approved_target_chart,
                current_k8s_version=current_k8s,
                target_k8s_version=target_k8s,
            )
            if support_finding is not None:
                findings.append(support_finding)
            if migration_profile is not None and not release_identity_needs_source_version:
                state = ONBOARDING_STATE_EXISTING_SOPERATOR_SUPPORTED
                remediation = _remediation_items_for_profile(
                    profile=migration_profile,
                    storage_present=storage_present,
                    target_version=pinned_chart_version or target_version,
                )
                migration_plan = _default_soperator_migration_plan(
                    include_target_gpu_reconciliation=any(
                        action.id == ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK
                        and action.selected
                        for action in actions
                    ),
                    include_external_node_template_upgrade=external_node_template_required,
                    include_soperator_upgrade=True,
                    include_data_migration=any(
                        item.classification == "data-sensitive" for item in remediation
                    ),
                    include_compute_migration=not compute_layout_compatible,
                )
            findings.append(
                SoperatorOnboardingFinding(
                    layer="versions",
                    status="upgrade-available",
                    severity="recommended",
                    message="Existing Soperator version is older than the cxcli-pinned version.",
                    action_id=(
                        ""
                        if release_identity_needs_source_version
                        else ONBOARDING_ACTION_UPGRADE_SOPERATOR
                    ),
                    evidence={"live_chart": live_chart, "live_app": live_app},
                )
            )
            if migration_profile is not None and not release_identity_needs_source_version:
                actions.append(
                    SoperatorOnboardingAction(
                        id=ONBOARDING_ACTION_UPGRADE_SOPERATOR,
                        title="Upgrade Soperator to the cxcli-pinned version",
                        layer="versions",
                        selected=True,
                        reason="Upgrades are allowed when live version is older and profiled.",
                    )
                )
                actions.append(
                    SoperatorOnboardingAction(
                        id=ONBOARDING_ACTION_APPROVE_EXTERNAL_UPGRADE,
                        title="Approve Soperator role, storage, and SlurmCluster remediation",
                        layer="external-upgrade",
                        selected=True,
                        disruptive=True,
                        reason="External upgrade changes require customer approval before execution.",
                    )
                )
                if external_node_template_required:
                    provider_inventory_available = bool(
                        node_template_inventory.get("provider_inventory_available")
                    )
                    findings.append(
                        SoperatorOnboardingFinding(
                            layer="mk8s-node-template",
                            status="remediation-planned",
                            severity="required",
                            message=(
                                "The external MK8s control plane will be upgraded directly; "
                                "node-group template alignment follows the separately accepted "
                                "in-place or blue-green compute migration mode."
                                if provider_inventory_available
                                else (
                                    "External MK8s control-plane and node-group provider "
                                    "inventory was not available during onboarding; cxcli will "
                                    "verify the control-plane hop and accepted target compute "
                                    "plan before it manages Soperator."
                                )
                            ),
                            action_id=ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
                            evidence=_node_template_inventory_finding_evidence(
                                node_template_inventory
                            ),
                        )
                    )
                    actions.append(
                        SoperatorOnboardingAction(
                            id=ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
                            title="Upgrade external MK8s control plane only",
                            layer="mk8s-node-template",
                            selected=True,
                            disruptive=True,
                            reason=(
                                "External Soperator targets are not Terraform-owned, so cxcli "
                                "advances the MK8s control plane directly while target OS/GPU "
                                "alignment follows the accepted compute migration mode."
                            ),
                        )
                    )
                else:
                    findings.append(
                        SoperatorOnboardingFinding(
                            layer="mk8s-node-template",
                            status="target-compatible",
                            severity="info",
                            message=(
                                "External MK8s control plane and discovered node-group "
                                "templates already match the target Kubernetes version, node "
                                "OS image, and GPU driver preset requirements. cxcli will "
                                "skip external control-plane and compute remediation unless live "
                                "state drifts."
                            ),
                            evidence=_node_template_inventory_finding_evidence(
                                node_template_inventory
                            ),
                        )
                    )
                if any(item.classification == "data-sensitive" for item in remediation):
                    actions.extend(
                        [
                            SoperatorOnboardingAction(
                                id=ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
                                title="Create aligned SFS filesystems before storage cutover",
                                layer="storage-sfs",
                                selected=True,
                                disruptive=True,
                                reason="Data-sensitive storage migrations need dual-attached SFS.",
                            ),
                            SoperatorOnboardingAction(
                                id=ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
                                title="Plan online bulk sync and final delta storage migration",
                                layer="storage-sfs",
                                selected=True,
                                disruptive=True,
                                reason=(
                                    "Storage data must be migrated without losing ownership or "
                                    "metadata."
                                ),
                            ),
                        ]
                    )
                if not compute_layout_compatible:
                    actions.append(
                        SoperatorOnboardingAction(
                            id=ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
                            title="Plan accepted compute migration",
                            layer="placements",
                            selected=True,
                            disruptive=True,
                            reason=(
                                "Service and worker roles follow the separately accepted in-place "
                                "or blue-green migration and its workload-specific safety gates."
                            ),
                        )
                    )
        if comparison == "newer" or app_comparison == "newer":
            if not release_identity_needs_source_version:
                state = ONBOARDING_STATE_EXISTING_SOPERATOR_NEWER
            findings.append(
                SoperatorOnboardingFinding(
                    layer="versions",
                    status="newer-than-cxcli",
                    severity="required",
                    message=(
                        "Existing Soperator version is newer than the cxcli-pinned version; "
                        "cxcli will not downgrade it without an explicit replacement plan."
                    ),
                    evidence={"live_chart": live_chart, "live_app": live_app},
                )
            )
        if comparison == "unknown" and (live_chart or pinned_chart_version):
            state = ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN
            findings.append(
                SoperatorOnboardingFinding(
                    layer="versions",
                    status="version-compare-required",
                    severity="required",
                    message="Soperator chart version could not be compared safely.",
                    evidence={"live_chart": live_chart, "pinned_chart": pinned_chart_version},
                )
            )
        if (
            not release_identity_needs_source_version
            and state
            not in {
                ONBOARDING_STATE_EXISTING_SOPERATOR_SUPPORTED,
                ONBOARDING_STATE_EXISTING_SOPERATOR_NEWER,
                ONBOARDING_STATE_EXISTING_SOPERATOR_TARGET,
            }
            and migration_profile is not None
            and comparison == "equal"
            and (app_comparison in {"equal", "unknown"} or not live_app or not pinned_app_version)
        ):
            state = ONBOARDING_STATE_EXISTING_SOPERATOR_TARGET
            findings.append(
                SoperatorOnboardingFinding(
                    layer="versions",
                    status="target-version",
                    severity="info",
                    message="Existing Soperator version matches the cxcli-pinned target.",
                    evidence={"live_chart": live_chart, "live_app": live_app},
                )
            )

    if soperator_release is None and has_soperator_crds and not source_version:
        state = ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN
        findings.append(
            SoperatorOnboardingFinding(
                layer="versions",
                status="source-version-required",
                severity="required",
                message=(
                    "Soperator CRDs were detected but no compatible Helm release version was found. "
                    "Select the source Soperator version so cxcli can match an exact committed "
                    "migration-profile row or known major-generation profile."
                ),
            )
        )

    if (
        source_version
        and target_version
        and not any(finding.layer == SOPERATOR_UPGRADE_SUPPORT_LAYER for finding in findings)
    ):
        control_plane_inventory = node_template_inventory.get("control_plane")
        current_k8s = (
            str(control_plane_inventory.get("current_k8s_version", "") or "").strip()
            if isinstance(control_plane_inventory, Mapping)
            else ""
        )
        support_finding = _soperator_upgrade_support_status_finding(
            source_version=source_version,
            target_version=target_chart_version or target_version,
            approved_target_chart_version=approved_target_chart,
            current_k8s_version=current_k8s,
            target_k8s_version=target_k8s,
        )
        if support_finding is not None:
            findings.append(support_finding)

    return SoperatorOnboardingReport(
        schema=ONBOARDING_SCHEMA,
        target_ref=normalize_component_token(target_ref),
        analyzed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        state=state,
        fingerprint=_analysis_fingerprint(snapshot),
        findings=tuple(findings),
        actions=_dedupe_soperator_actions(actions),
        source_version=source_version,
        target_version=pinned_chart_version or target_version,
        migration_profile_id=migration_profile_id,
        remediation=remediation,
        migration_plan=migration_plan,
    )


def target_snapshot_from_config(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> dict[str, Any]:
    target = soperator_onboarding_target(payload_or_config, target_ref=target_ref)
    node_groups = _node_group_inventory_from_target(target)
    onboarding = target.get("soperator_onboarding") if isinstance(target, Mapping) else {}
    collection_errors = (
        onboarding.get("collection_errors") if isinstance(onboarding, Mapping) else ()
    )
    source_version = (
        str(onboarding.get("source_version", "") or "").strip()
        if isinstance(onboarding, Mapping)
        else ""
    )
    helm_releases: list[dict[str, Any]] = []
    if source_version:
        helm_releases.append(
            {
                "name": "soperator",
                "namespace": "soperator",
                "chart": f"soperator-{source_version}",
                "app_version": source_version,
            }
        )
    return {
        "node_groups": dict(node_groups),
        "helm_releases": helm_releases,
        "crds": [],
        "namespaces": ["soperator"] if source_version else [],
        "collection_errors": list(collection_errors) if isinstance(collection_errors, list) else [],
        "storage": {},
    }


def _matching_source_discovery_report(
    *,
    source_report_path: Path | None,
    target_ref: str,
    onboarding: Mapping[str, Any],
) -> dict[str, Any] | None:
    if source_report_path is None or not source_report_path.exists():
        return None
    try:
        payload = load_soperator_discovery_bundle(source_report_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    report = payload.get("report")
    if not isinstance(report, Mapping):
        return None
    if normalize_component_token(report.get("target_ref")) != normalize_component_token(target_ref):
        return None
    for key in ("state", "source_version", "target_version", "migration_profile_id"):
        expected = str(onboarding.get(key, "") or "").strip()
        actual = str(report.get(key, "") or "").strip()
        if expected and actual != expected:
            return None
    return soperator_report_with_accepted_onboarding_contract(report, onboarding)


def _report_with_accepted_onboarding_contract(
    report: SoperatorOnboardingReport,
    onboarding: Mapping[str, Any],
) -> SoperatorOnboardingReport:
    collection_errors = onboarding.get("collection_errors")
    if isinstance(collection_errors, list) and collection_errors:
        return report
    action_ids = _onboarding_action_ids(onboarding)
    if not action_ids:
        return report
    selected_action_ids = set(action_ids)
    selected_actions = _configured_soperator_actions(action_ids, analyzed_actions=report.actions)
    filtered_findings = tuple(
        finding
        for finding in report.findings
        if not finding.action_id or finding.action_id in selected_action_ids
    )
    return replace(
        report,
        state=str(onboarding.get("state", "") or "").strip() or report.state,
        actions=selected_actions,
        findings=filtered_findings,
        source_version=str(onboarding.get("source_version", "") or "").strip()
        or report.source_version,
        target_version=str(onboarding.get("target_version", "") or "").strip()
        or report.target_version,
        migration_profile_id=str(onboarding.get("migration_profile_id", "") or "").strip()
        or report.migration_profile_id,
        migration_plan=_migration_plan_for_action_ids(action_ids),
    )


def soperator_onboarding_target_refs(payload_or_config: Any) -> tuple[str, ...]:
    payload = to_plain_data(payload_or_config)
    apps = payload.get("apps") if isinstance(payload, Mapping) else None
    charts = apps.get("charts") if isinstance(apps, Mapping) else None
    if not isinstance(charts, list):
        return ()
    refs: list[str] = []
    seen: set[str] = set()
    for row in charts:
        if not isinstance(row, Mapping):
            continue
        if row.get("id") != "soperator" or row.get("install_mode") != "onboard-existing-cluster":
            continue
        target_ref = normalize_component_token(row.get("instance_id"))
        if target_ref and target_ref not in seen:
            refs.append(target_ref)
            seen.add(target_ref)
    return tuple(refs)


def build_soperator_onboarding_report_from_config(
    payload_or_config: Any,
    *,
    target_ref: str,
    pinned_chart_version: str = "",
    pinned_app_version: str = "",
    approved_target_chart_version: str = "",
    source_report_path: Path | None = None,
) -> dict[str, Any]:
    target = soperator_onboarding_target(payload_or_config, target_ref=target_ref)
    onboarding = target.get("soperator_onboarding") if isinstance(target, Mapping) else {}
    if isinstance(onboarding, Mapping):
        source_report = _matching_source_discovery_report(
            source_report_path=source_report_path,
            target_ref=target_ref,
            onboarding=onboarding,
        )
        if source_report is not None:
            source_report["onboard_description"] = EXT_SOPERATOR_ONBOARD_DESCRIPTION
            source_report["accepted_fingerprint"] = soperator_onboarding_fingerprint(
                payload_or_config,
                target_ref=target_ref,
            )
            return source_report
    snapshot = target_snapshot_from_config(payload_or_config, target_ref=target_ref)
    app_row = soperator_onboarding_app_row(payload_or_config, target_ref=target_ref)
    if not pinned_chart_version and isinstance(app_row, Mapping):
        pinned_chart_version = str(app_row.get("version", "") or "").strip()
    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref=target_ref,
        pinned_chart_version=pinned_chart_version,
        pinned_app_version=pinned_app_version,
        approved_target_chart_version=approved_target_chart_version,
    )
    if isinstance(onboarding, Mapping):
        storage_mode = str(onboarding.get("storage_mode", "") or "").strip()
        compute_mode = str(onboarding.get("compute_mode", "") or "").strip()
        if storage_mode or compute_mode:
            report = soperator_onboarding_report_for_modes(
                report,
                storage_mode=storage_mode or ONBOARDING_STORAGE_MODE_KEEP_EXISTING,
                compute_mode=compute_mode or ONBOARDING_COMPUTE_MODE_KEEP_EXISTING,
            )
        if onboarding.get("accepted") is True:
            report = _report_with_accepted_onboarding_contract(report, onboarding)
    report_payload = report.to_dict()
    report_payload["onboard_description"] = EXT_SOPERATOR_ONBOARD_DESCRIPTION
    if isinstance(onboarding, Mapping):
        upgrade_path = onboarding.get("upgrade_path")
        if isinstance(upgrade_path, Mapping):
            report_payload["upgrade_path"] = copy.deepcopy(to_plain_data(upgrade_path))
    report_payload["accepted_fingerprint"] = soperator_onboarding_fingerprint(
        payload_or_config,
        target_ref=target_ref,
    )
    return report_payload


def write_soperator_onboarding_reports(
    payload_or_config: Any,
    generated_dir: Path,
    *,
    pinned_chart_version: str = "",
    pinned_app_version: str = "",
    approved_target_chart_version: str = "",
) -> list[Path]:
    written: list[Path] = []
    for target_ref in soperator_onboarding_target_refs(payload_or_config):
        identity = soperator_cluster_artifact_identity_from_payload(
            payload_or_config,
            target_ref=target_ref,
        )
        report = build_soperator_onboarding_report_from_config(
            payload_or_config,
            target_ref=target_ref,
            pinned_chart_version=pinned_chart_version,
            pinned_app_version=pinned_app_version,
            approved_target_chart_version=approved_target_chart_version,
            source_report_path=source_soperator_discovery_report_path(
                generated_dir.parent,
                target_ref,
                payload_or_config=payload_or_config,
            ),
        )
        report.update(identity.as_metadata())
        path = (
            soperator_cluster_report_dir(
                generated_dir.parent,
                identity,
                "onboarding",
            )
            / "report.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp_path = Path(handle.name)
                handle.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
            tmp_path.replace(path)
        finally:
            if tmp_path is not None and tmp_path.exists():
                with suppress(OSError):
                    tmp_path.unlink()
        written.append(path)
    return written


def write_source_soperator_discovery_report(
    target_dir: Path,
    *,
    target_ref: str,
    snapshot: Mapping[str, Any],
    report: SoperatorOnboardingReport | Mapping[str, Any],
    artifact_identity: SoperatorClusterArtifactIdentity | None = None,
    cluster_id: str = "",
    cluster_name: str = "",
    source_kind: str = "external",
    command: Sequence[str] | None = None,
    namespace: str = "",
    release_name: str = "",
    kube_context: str = "",
    chart_values: Mapping[str, Any] | None = None,
    slurm_snapshot: Mapping[str, Any] | None = None,
    accounting_snapshot: Mapping[str, Any] | None = None,
    target_versions: Mapping[str, Any] | None = None,
    guidance_lines: Sequence[str] | None = None,
    output_dir: Path | None = None,
    redaction: str = "support",
) -> Path:
    return write_soperator_discovery_bundle(
        target_dir,
        target_ref=target_ref,
        snapshot=snapshot,
        report=report,
        source_kind=source_kind,
        command=command,
        artifact_identity=artifact_identity,
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        namespace=namespace,
        release_name=release_name,
        kube_context=kube_context,
        chart_values=chart_values,
        slurm_snapshot=slurm_snapshot,
        accounting_snapshot=accounting_snapshot,
        target_versions=target_versions,
        guidance_lines=guidance_lines,
        output_dir=output_dir,
        redaction=redaction,
    )


def _soperator_jail_pvc_binding(
    soperator_resources: Sequence[Mapping[str, Any]],
) -> tuple[str, str] | None:
    slurmclusters = tuple(
        item
        for item in soperator_resources
        if str(item.get("kind", "") or "").strip() == "SlurmCluster"
    )
    if not slurmclusters:
        return None

    bindings: set[tuple[str, str]] = set()
    unresolved: list[str] = []
    for slurmcluster in slurmclusters:
        metadata = (
            slurmcluster.get("metadata")
            if isinstance(slurmcluster.get("metadata"), Mapping)
            else {}
        )
        namespace = str(metadata.get("namespace", "") or "soperator").strip()
        cluster_name = str(metadata.get("name", "") or "<unnamed>").strip()
        spec = slurmcluster.get("spec") if isinstance(slurmcluster.get("spec"), Mapping) else {}
        volume_sources: dict[str, list[Mapping[str, Any]]] = {}
        for source in _sequence_of_mappings(spec.get("volumeSources")):
            source_name = str(source.get("name", "") or "").strip()
            if source_name:
                volume_sources.setdefault(source_name, []).append(source)

        referenced_source_names: set[str] = set()
        direct_claim_names: set[str] = set()
        slurm_nodes = spec.get("slurmNodes")
        if isinstance(slurm_nodes, Mapping):
            for role in slurm_nodes.values():
                if not isinstance(role, Mapping):
                    continue
                volumes = role.get("volumes")
                jail = volumes.get("jail") if isinstance(volumes, Mapping) else None
                if not isinstance(jail, Mapping):
                    continue
                persistent_volume_claim = jail.get("persistentVolumeClaim")
                claim_name = str(
                    persistent_volume_claim.get("claimName", "")
                    if isinstance(persistent_volume_claim, Mapping)
                    else ""
                ).strip()
                if claim_name:
                    direct_claim_names.add(claim_name)
                source_name = str(jail.get("volumeSourceName", "") or "").strip()
                if source_name:
                    referenced_source_names.add(source_name)

        if not referenced_source_names and not direct_claim_names:
            referenced_source_names.update(
                source_name
                for source_name in volume_sources
                if normalize_component_token(source_name) in {"jail", "jail-rootfs"}
            )

        bindings.update((namespace, claim_name) for claim_name in direct_claim_names)
        for source_name in sorted(referenced_source_names):
            source_matches = volume_sources.get(source_name, [])
            if len(source_matches) != 1:
                unresolved.append(
                    f"{namespace}/{cluster_name} volumeSourceName={source_name} "
                    f"resolved to {len(source_matches)} declarations"
                )
                continue
            persistent_volume_claim = source_matches[0].get("persistentVolumeClaim")
            claim_name = str(
                persistent_volume_claim.get("claimName", "")
                if isinstance(persistent_volume_claim, Mapping)
                else ""
            ).strip()
            if not claim_name:
                unresolved.append(
                    f"{namespace}/{cluster_name} volumeSourceName={source_name} has no PVC"
                )
                continue
            bindings.add((namespace, claim_name))

    if unresolved:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: unresolved SlurmCluster Jail "
            "volume reference(s): " + "; ".join(unresolved) + "."
        )
    if len(bindings) != 1:
        details = ", ".join(f"{namespace}/{name}" for namespace, name in sorted(bindings))
        if not details:
            details = "none"
        raise RuntimeError(
            "Soperator Jail identity resolution failed: expected exactly one discovered "
            f"Jail PVC binding, found {len(bindings)} ({details})."
        )
    return next(iter(bindings))


def _soperator_jail_filesystem_identity(
    *,
    soperator_resources: Sequence[Mapping[str, Any]],
    pvcs: Sequence[Mapping[str, Any]],
    pvs: Sequence[Mapping[str, Any]],
) -> str:
    binding = _soperator_jail_pvc_binding(soperator_resources)
    if binding is None:
        return ""
    namespace, claim_name = binding
    pvc_matches = [
        item
        for item in pvcs
        if str(
            item.get("metadata", {}).get("namespace", "")
            if isinstance(item.get("metadata"), Mapping)
            else ""
        ).strip()
        == namespace
        and str(
            item.get("metadata", {}).get("name", "")
            if isinstance(item.get("metadata"), Mapping)
            else ""
        ).strip()
        == claim_name
    ]
    if len(pvc_matches) != 1:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: discovered Jail PVC "
            f"{namespace}/{claim_name} resolved to {len(pvc_matches)} live PVC objects."
        )
    pvc = pvc_matches[0]
    pvc_status = pvc.get("status") if isinstance(pvc.get("status"), Mapping) else {}
    if str(pvc_status.get("phase", "") or "").strip() != "Bound":
        raise RuntimeError(
            "Soperator Jail identity resolution failed: discovered Jail PVC "
            f"{namespace}/{claim_name} is not Bound."
        )
    pvc_spec = pvc.get("spec") if isinstance(pvc.get("spec"), Mapping) else {}
    pv_name = str(pvc_spec.get("volumeName", "") or "").strip()
    if not pv_name:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: discovered Jail PVC "
            f"{namespace}/{claim_name} has no bound PV name."
        )
    pv_matches = [
        item
        for item in pvs
        if str(
            item.get("metadata", {}).get("name", "")
            if isinstance(item.get("metadata"), Mapping)
            else ""
        ).strip()
        == pv_name
    ]
    if len(pv_matches) != 1:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: bound PV "
            f"{pv_name} resolved to {len(pv_matches)} live PV objects."
        )
    pv = pv_matches[0]
    pv_status = pv.get("status") if isinstance(pv.get("status"), Mapping) else {}
    if str(pv_status.get("phase", "") or "").strip() != "Bound":
        raise RuntimeError(f"Soperator Jail identity resolution failed: PV {pv_name} is not Bound.")
    pv_spec = pv.get("spec") if isinstance(pv.get("spec"), Mapping) else {}
    claim_ref = pv_spec.get("claimRef") if isinstance(pv_spec.get("claimRef"), Mapping) else {}
    if (
        str(claim_ref.get("namespace", "") or "").strip() != namespace
        or str(claim_ref.get("name", "") or "").strip() != claim_name
    ):
        raise RuntimeError(
            "Soperator Jail identity resolution failed: PV "
            f"{pv_name} claimRef does not match {namespace}/{claim_name}."
        )
    pvc_metadata = pvc.get("metadata") if isinstance(pvc.get("metadata"), Mapping) else {}
    pvc_uid = str(pvc_metadata.get("uid", "") or "").strip()
    claim_uid = str(claim_ref.get("uid", "") or "").strip()
    if pvc_uid and claim_uid and pvc_uid != claim_uid:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: PV "
            f"{pv_name} claimRef UID does not match the discovered Jail PVC."
        )
    csi = pv_spec.get("csi") if isinstance(pv_spec.get("csi"), Mapping) else {}
    volume_handle = str(csi.get("volumeHandle", "") or "").strip()
    if not volume_handle:
        local = pv_spec.get("local") if isinstance(pv_spec.get("local"), Mapping) else {}
        local_path = str(local.get("path", "") or "").strip()
        if local_path:
            # Active/passive Jail slots are local-path PVs backed by a Nebius SFS
            # mounted on the node. The PV path identifies only the logical slot;
            # the immutable backing filesystem ID is resolved from the fresh
            # Nebius SDK node-group attachment inventory after snapshots merge.
            return ""
        raise RuntimeError(
            "Soperator Jail identity resolution failed: bound PV "
            f"{pv_name} has no CSI volumeHandle."
        )
    return volume_handle


def soperator_jail_filesystem_identity_from_provider_node_groups(
    node_groups: Mapping[str, Any],
    *,
    kubernetes_identity: str = "",
) -> str:
    """Resolve one immutable Jail SFS ID from Nebius node-group attachments."""

    existing_identity = str(kubernetes_identity or "").strip()
    candidates: dict[str, list[str]] = {}
    incomplete: list[str] = []
    for group_key, raw_group in sorted(node_groups.items(), key=lambda item: str(item[0])):
        if not isinstance(raw_group, Mapping):
            continue
        provider = raw_group.get("provider")
        provider = provider if isinstance(provider, Mapping) else {}
        template = provider.get("node_template")
        template = template if isinstance(template, Mapping) else {}
        filesystems = template.get("filesystems")
        if not isinstance(filesystems, Sequence) or isinstance(
            filesystems,
            (str, bytes, bytearray),
        ):
            continue
        group_name = str(
            provider.get("node_group_name") or raw_group.get("node_group_name") or group_key
        ).strip()
        for attachment in filesystems:
            if not isinstance(attachment, Mapping):
                continue
            mount_tag = str(attachment.get("mount_tag") or attachment.get("mountTag") or "").strip()
            if normalize_component_token(mount_tag) != "jail":
                continue
            filesystem_id = str(
                attachment.get("filesystem_id") or attachment.get("filesystemId") or ""
            ).strip()
            if not filesystem_id:
                for key in ("existing_filesystem", "existingFilesystem", "filesystem"):
                    nested = attachment.get(key)
                    if isinstance(nested, Mapping):
                        filesystem_id = str(nested.get("id", "") or "").strip()
                    if filesystem_id:
                        break
            if not filesystem_id:
                incomplete.append(group_name or str(group_key))
                continue
            candidates.setdefault(filesystem_id, []).append(group_name or str(group_key))

    if incomplete:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: Nebius node-group Jail "
            "attachment(s) have no immutable filesystem ID: "
            + ", ".join(sorted(set(incomplete)))
            + "."
        )
    if len(candidates) > 1:
        details = "; ".join(
            f"{filesystem_id} on {', '.join(sorted(set(groups)))}"
            for filesystem_id, groups in sorted(candidates.items())
        )
        raise RuntimeError(
            "Soperator Jail identity resolution failed: Nebius node-group Jail "
            f"attachments reference multiple backing filesystems ({details})."
        )
    provider_identity = next(iter(candidates), "")
    if existing_identity and provider_identity and existing_identity != provider_identity:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: Kubernetes CSI identity "
            f"{existing_identity} conflicts with Nebius node-group attachment identity "
            f"{provider_identity}."
        )
    resolved = provider_identity or existing_identity
    if not resolved:
        raise RuntimeError(
            "Soperator Jail identity resolution failed: neither the bound Jail PV nor "
            "fresh Nebius SDK node-group Jail attachments expose an immutable backing "
            "filesystem ID."
        )
    return resolved


def _soperator_slurmcluster_uid(
    soperator_resources: Sequence[Mapping[str, Any]],
) -> str:
    candidates: list[tuple[str, str, str]] = []
    for resource in soperator_resources:
        if str(resource.get("kind", "") or "").strip().lower() != "slurmcluster":
            continue
        metadata = resource.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        candidates.append(
            (
                str(metadata.get("namespace", "") or "soperator").strip(),
                str(metadata.get("name", "") or "<unnamed>").strip(),
                str(metadata.get("uid", "") or "").strip(),
            )
        )
    if not candidates:
        return ""
    if len(candidates) != 1:
        identities = ", ".join(
            f"{namespace}/{name} uid={uid or 'missing'}"
            for namespace, name, uid in sorted(candidates)
        )
        raise RuntimeError(
            "Soperator identity resolution failed: expected exactly one discovered "
            f"SlurmCluster identity, found {len(candidates)} ({identities})."
        )
    namespace, name, uid = candidates[0]
    if not uid:
        raise RuntimeError(
            "Soperator identity resolution failed: discovered SlurmCluster "
            f"{namespace}/{name} has no immutable UID."
        )
    return uid


_RESUME_SLURMCLUSTER_IDENTITY_SCOPE_SCHEMA = (
    "nebius-cxcli-ext-soperator-resume-slurmcluster-identity/v1"
)


def _resume_slurmcluster_identity_resources(
    soperator_resources: Sequence[Mapping[str, Any]],
    *,
    identity_scope: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], dict[str, Any]]:
    """Validate one checkpoint-scoped handoff inventory and return its identity view."""

    if identity_scope.get("schema") != _RESUME_SLURMCLUSTER_IDENTITY_SCOPE_SCHEMA:
        raise RuntimeError(
            "External Soperator resume SlurmCluster identity scope has an unsupported schema."
        )
    mode = str(identity_scope.get("mode", "") or "").strip()
    if mode not in {"target-handoff", "source-cleanup", "target-only"}:
        raise RuntimeError(
            "External Soperator resume SlurmCluster identity scope has an unsupported mode."
        )
    if str(identity_scope.get("phase_id", "") or "").strip() not in {
        "populate-jail-refresh",
        "rolling-compute-migration",
    }:
        raise RuntimeError(
            "External Soperator resume SlurmCluster identity scope is not bound to a "
            "supported target-creation phase."
        )
    source_ref = _mapping_value(identity_scope.get("source"))
    target_ref = _mapping_value(identity_scope.get("target"))

    def _required_ref(ref: Mapping[str, Any], *, label: str) -> dict[str, str]:
        normalized = {
            "namespace": str(ref.get("namespace", "") or "").strip(),
            "name": str(ref.get("name", "") or "").strip(),
            "uid": str(ref.get("uid", "") or "").strip(),
        }
        missing = [key for key, value in normalized.items() if not value]
        if missing:
            raise RuntimeError(
                "External Soperator resume SlurmCluster identity scope has an incomplete "
                f"{label} binding: missing {', '.join(missing)}."
            )
        return normalized

    source = _required_ref(source_ref, label="source")
    target = {
        "namespace": str(target_ref.get("namespace", "") or "").strip(),
        "name": str(target_ref.get("name", "") or "").strip(),
        "uid": str(target_ref.get("uid", "") or "").strip(),
    }
    target_uid_bootstrap = not target["uid"]
    if not target["namespace"] or not target["name"]:
        raise RuntimeError(
            "External Soperator resume SlurmCluster identity scope has an incomplete target "
            "binding."
        )
    if mode in {"source-cleanup", "target-only"} and target_uid_bootstrap:
        raise RuntimeError(
            "External Soperator source-cleanup/target-only resume requires a checkpointed target "
            "SlurmCluster UID."
        )
    if source["namespace"] != "soperator" or target["namespace"] != "soperator":
        raise RuntimeError(
            "External Soperator resume SlurmCluster identity scope is namespace-bound to soperator."
        )
    if source["name"] == target["name"]:
        raise RuntimeError(
            "External Soperator dual-CR resume requires distinct source and target "
            "SlurmCluster names."
        )

    slurmclusters = tuple(
        item
        for item in soperator_resources
        if str(item.get("kind", "") or "").strip().lower() == "slurmcluster"
    )
    expected_counts = (
        {1, 2}
        if mode == "source-cleanup" or (mode == "target-handoff" and target_uid_bootstrap)
        else ({2} if mode == "target-handoff" else {1})
    )
    if len(slurmclusters) not in expected_counts:
        expected_text = "1 or 2" if len(expected_counts) > 1 else str(next(iter(expected_counts)))
        raise RuntimeError(
            "External Soperator checkpoint-scoped SlurmCluster inventory expected exactly "
            f"{expected_text} object(s) for {mode}, found {len(slurmclusters)}."
        )

    def _matches(resource: Mapping[str, Any], ref: Mapping[str, str]) -> bool:
        metadata = _mapping_value(resource.get("metadata"))
        return (
            str(metadata.get("namespace", "") or "soperator").strip() == ref["namespace"]
            and str(metadata.get("name", "") or "").strip() == ref["name"]
        )

    source_matches = tuple(item for item in slurmclusters if _matches(item, source))
    target_matches = tuple(item for item in slurmclusters if _matches(item, target))
    if mode == "target-handoff" and len(source_matches) != 1:
        raise RuntimeError(
            "External Soperator checkpoint-scoped SlurmCluster inventory does not contain "
            "exactly one immutable source binding."
        )
    source_resource = source_matches[0] if source_matches else None
    if source_resource is not None:
        live_source_uid = str(
            _mapping_value(source_resource.get("metadata")).get("uid", "") or ""
        ).strip()
        if not live_source_uid or live_source_uid != source["uid"]:
            raise RuntimeError(
                "External Soperator live source SlurmCluster UID differs from the immutable "
                "checkpoint binding."
            )
    if mode == "target-handoff" and target_uid_bootstrap and len(slurmclusters) == 1:
        return (source_resource,), {}
    if len(target_matches) != 1:
        raise RuntimeError(
            "External Soperator checkpoint-scoped SlurmCluster inventory does not contain "
            "exactly one target binding."
        )
    target_resource = target_matches[0]
    if mode == "source-cleanup" and len(slurmclusters) == 2 and len(source_matches) != 1:
        raise RuntimeError(
            "External Soperator source-cleanup resume found an unbound second SlurmCluster."
        )
    if mode == "target-only" and source_matches:
        raise RuntimeError(
            "External Soperator source SlurmCluster reappeared after checkpointed cleanup."
        )
    live_target_uid = str(
        _mapping_value(target_resource.get("metadata")).get("uid", "") or ""
    ).strip()
    if not live_target_uid or live_target_uid == source["uid"]:
        raise RuntimeError(
            "External Soperator live target SlurmCluster UID is missing or aliases the source UID."
        )
    if target["uid"] and live_target_uid != target["uid"]:
        raise RuntimeError(
            "External Soperator live target SlurmCluster UID differs from the immutable "
            "checkpoint binding."
        )
    if target_uid_bootstrap:
        if identity_scope.get("allow_target_uid_bootstrap") is not True:
            raise RuntimeError(
                "External Soperator target SlurmCluster UID is not checkpointed and bootstrap "
                "was not authorized by the target-handoff phase."
            )
        metadata = _mapping_value(target_resource.get("metadata"))
        annotations = _mapping_value(metadata.get("annotations"))
        labels = _mapping_value(metadata.get("labels"))
        expected_version = str(identity_scope.get("target_version", "") or "").strip()
        chart_label = str(labels.get("helm.sh/chart", "") or "").strip()
        expected_chart_label = f"soperator-{expected_version.replace('+', '_')}"
        if (
            not expected_version
            or str(annotations.get("meta.helm.sh/release-name", "") or "").strip() != "soperator"
            or str(annotations.get("meta.helm.sh/release-namespace", "") or "").strip()
            != target["namespace"]
            or str(labels.get("app.kubernetes.io/managed-by", "") or "").strip().lower() != "helm"
            or chart_label != expected_chart_label
        ):
            raise RuntimeError(
                "External Soperator target SlurmCluster UID bootstrap requires exact Helm "
                "ownership and the checkpointed target chart version."
            )
        target["uid"] = live_target_uid

    normalized_scope = {
        "schema": _RESUME_SLURMCLUSTER_IDENTITY_SCOPE_SCHEMA,
        "mode": mode,
        "phase_id": str(identity_scope.get("phase_id", "") or "").strip(),
        "source": source,
        "target": target,
        "target_uid_bootstrapped": target_uid_bootstrap,
        "identity_role": "source" if source_resource is not None else "target",
    }
    identity_resources = (source_resource,) if source_resource is not None else (target_resource,)
    return identity_resources, normalized_scope


def collect_kubectl_soperator_snapshot(
    *,
    kube_context: str,
    timeout: int = 30,
    extra_env: Mapping[str, str] | None = None,
    slurmcluster_identity_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context = str(kube_context or "").strip()
    if not context:
        return {"node_groups": {}, "helm_releases": [], "crds": []}
    collection_errors: list[dict[str, Any]] = []
    nodes = _kubectl_json(
        ["kubectl", "--context", context, "get", "nodes", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    crds = _kubectl_json(
        ["kubectl", "--context", context, "get", "crd", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    namespaces = _kubectl_json(
        ["kubectl", "--context", context, "get", "namespace", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    namespace_names = (
        [
            str(item.get("metadata", {}).get("name", "")).strip()
            for item in namespaces.get("items", [])
            if isinstance(item, Mapping)
        ]
        if isinstance(namespaces, Mapping)
        else []
    )
    pvs = _kubectl_list_json_with_bounded_retry(
        ["kubectl", "--context", context, "get", "pv", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    pvcs = _kubectl_list_json_with_bounded_retry(
        ["kubectl", "--context", context, "get", "pvc", "-A", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    workloads: Mapping[str, Any] = {}
    if "soperator" in namespace_names:
        workloads = _kubectl_list_json_with_bounded_retry(
            [
                "kubectl",
                "--context",
                context,
                "get",
                "deployments,statefulsets,statefulsets.apps.kruise.io,"
                "daemonsets,pods,jobs,services,configmaps,secrets",
                "-n",
                "soperator",
                "-o",
                "json",
            ],
            timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
    crd_names = (
        [
            str(item.get("metadata", {}).get("name", "")).strip()
            for item in crds.get("items", [])
            if isinstance(item, Mapping)
        ]
        if isinstance(crds, Mapping)
        else []
    )
    soperator_resource_kinds = [
        resource_kind
        for crd_name, resource_kind in SOPERATOR_CRD_RESOURCE_KINDS
        if crd_name in set(crd_names)
    ]
    soperator_resources: Mapping[str, Any] = {}
    if soperator_resource_kinds:
        soperator_resources = _kubectl_list_json_with_bounded_retry(
            [
                "kubectl",
                "--context",
                context,
                "get",
                ",".join(soperator_resource_kinds),
                "-A",
                "-o",
                "json",
            ],
            timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
    if slurmcluster_identity_scope is not None:
        scoped_slurmclusters = _kubectl_list_json_with_bounded_retry(
            [
                "kubectl",
                "--context",
                context,
                "get",
                "slurmclusters",
                "-n",
                "soperator",
                "-o",
                "json",
            ],
            timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
        scoped_items = scoped_slurmclusters.get("items")
        if not isinstance(scoped_items, list):
            detail = _last_kubectl_inventory_error(
                collection_errors,
                resource="slurmclusters",
            )
            raise RuntimeError(
                "External Soperator checkpoint resume could not collect the exact "
                "namespace-scoped SlurmCluster inventory after 3 attempts; refusing to "
                f"infer identity from broad CRD discovery.{detail}"
            )
        broad_items = (
            soperator_resources.get("items", []) if isinstance(soperator_resources, Mapping) else []
        )
        soperator_resources = {
            "apiVersion": "v1",
            "kind": "List",
            "items": [
                *(
                    item
                    for item in _sequence_of_mappings(broad_items)
                    if str(item.get("kind", "") or "").strip().lower() != "slurmcluster"
                ),
                *_sequence_of_mappings(scoped_items),
            ],
        }
    all_helm_releases = _helm_json(
        ["helm", "--kube-context", context, "list", "-A", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    helm_releases = (
        [
            release
            for release in all_helm_releases
            if isinstance(release, Mapping) and _is_soperator_release_candidate(release)
        ]
        if isinstance(all_helm_releases, list)
        else []
    )
    gpu_stack_helm_releases = []
    for namespace in GPU_STACK_HELM_DISCOVERY_NAMESPACES:
        namespace_releases = _helm_json(
            ["helm", "--kube-context", context, "list", "-n", namespace, "-o", "json"],
            timeout,
            errors=None,
            extra_env=extra_env,
        )
        if isinstance(namespace_releases, list):
            gpu_stack_helm_releases.extend(namespace_releases)
    gpu_stack_policies = _kubectl_json(
        [
            "kubectl",
            "--context",
            context,
            "get",
            "clusterpolicy,nicclusterpolicy",
            "-A",
            "-o",
            "json",
        ],
        timeout,
        errors=None,
        extra_env=extra_env,
    )
    worker_topology_by_nodeset = _collect_worker_topology_by_nodeset(
        kube_context=context,
        workloads=workloads,
        timeout=timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    slurm_health = _collect_slurm_health_from_login(
        kube_context=context,
        workloads=workloads,
        timeout=timeout,
        extra_env=extra_env,
    )
    node_groups: dict[str, dict[str, Any]] = {}
    for item in nodes.get("items", []) if isinstance(nodes, Mapping) else []:
        if not isinstance(item, Mapping):
            continue
        metadata = item.get("metadata")
        status = item.get("status")
        labels = metadata.get("labels") if isinstance(metadata, Mapping) else {}
        allocatable = status.get("allocatable") if isinstance(status, Mapping) else {}
        if not isinstance(labels, Mapping):
            labels = {}
        if not isinstance(allocatable, Mapping):
            allocatable = {}
        selector_key = ""
        selector_value = ""
        for candidate_key in (
            "nebius.com/node-group-id",
            "yandex.cloud/node-group-id",
            "nebius.com/node-group",
            "node.kubernetes.io/instance-type",
        ):
            candidate_value = str(labels.get(candidate_key) or "").strip()
            if candidate_value:
                selector_key = candidate_key
                selector_value = candidate_value
                break
        group_key = selector_value or "default"
        normalized = normalize_component_token(group_key) or "default"
        group = node_groups.setdefault(
            normalized,
            {
                "allocatable": {},
                "gpu": False,
                "node_count": 0,
                "labels": {},
                "nodes": [],
                "selector": {
                    "key": selector_key,
                    "operator": "In",
                    "values": [selector_value],
                }
                if selector_key and selector_value
                else {},
                "taints": [],
            },
        )
        group["node_count"] = int(group.get("node_count", 0)) + 1
        node_names = group.setdefault("nodes", [])
        node_name = str(metadata.get("name", "") if isinstance(metadata, Mapping) else "").strip()
        if isinstance(node_names, list) and node_name and node_name not in node_names:
            node_names.append(node_name)
        resources = group.setdefault("allocatable", {})
        if isinstance(resources, dict):
            for key, value in allocatable.items():
                resources[str(key)] = str(value)
        group["gpu"] = bool(group.get("gpu")) or any(
            str(key).startswith("nvidia.com/gpu") and str(value) not in {"0", ""}
            for key, value in allocatable.items()
        )
        taints = (
            item.get("spec", {}).get("taints", []) if isinstance(item.get("spec"), Mapping) else []
        )
        if isinstance(taints, list):
            existing_taints = group.setdefault("taints", [])
            if isinstance(existing_taints, list):
                for taint in taints:
                    if taint not in existing_taints:
                        existing_taints.append(taint)
        label_map = group.setdefault("labels", {})
        if isinstance(label_map, dict):
            for key, value in labels.items():
                text_key = str(key)
                if text_key.startswith(("nebius.com/", "slurm.nebius.ai/", "topology.nebius.com/")):
                    label_map.setdefault(text_key, str(value))
    kubernetes_uid = ""
    soperator_uid = ""
    for item in namespaces.get("items", []) if isinstance(namespaces, Mapping) else []:
        if not isinstance(item, Mapping):
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        namespace_name = str(metadata.get("name", "") or "").strip()
        namespace_uid = str(metadata.get("uid", "") or "").strip()
        if namespace_name == "kube-system":
            kubernetes_uid = namespace_uid
        elif namespace_name == "soperator":
            soperator_uid = namespace_uid
    collected_soperator_resources = (
        soperator_resources.get("items", []) if isinstance(soperator_resources, Mapping) else []
    )
    collected_pvcs = pvcs.get("items", []) if isinstance(pvcs, Mapping) else []
    collected_pvs = pvs.get("items", []) if isinstance(pvs, Mapping) else []
    identity_resources = _sequence_of_mappings(collected_soperator_resources)
    normalized_identity_scope: dict[str, Any] = {}
    if slurmcluster_identity_scope is not None:
        identity_resources, normalized_identity_scope = _resume_slurmcluster_identity_resources(
            identity_resources,
            identity_scope=slurmcluster_identity_scope,
        )
    slurmcluster_uid = (
        str(_mapping_value(normalized_identity_scope.get("source")).get("uid", "") or "").strip()
        if normalized_identity_scope.get("identity_role") == "target"
        else _soperator_slurmcluster_uid(identity_resources)
    )
    jail_binding = _soperator_jail_pvc_binding(identity_resources)
    if jail_binding is not None and not isinstance(pvcs.get("items"), list):
        detail = _last_kubectl_inventory_error(collection_errors, resource="pvc")
        raise RuntimeError(
            "Soperator Jail identity resolution failed: live PVC inventory collection "
            "failed after 3 attempts; refusing to treat a failed read as an empty inventory."
            f"{detail}"
        )
    if jail_binding is not None and not isinstance(pvs.get("items"), list):
        detail = _last_kubectl_inventory_error(collection_errors, resource="pv")
        raise RuntimeError(
            "Soperator Jail identity resolution failed: live PV inventory collection "
            "failed after 3 attempts; refusing to treat a failed read as an empty inventory."
            f"{detail}"
        )
    jail_filesystem_id = _soperator_jail_filesystem_identity(
        soperator_resources=identity_resources,
        pvcs=_sequence_of_mappings(collected_pvcs),
        pvs=_sequence_of_mappings(collected_pvs),
    )
    controller_bridge_source = _controller_bridge_source_record(
        workloads=_sequence_of_mappings(
            workloads.get("items", []) if isinstance(workloads, Mapping) else []
        ),
        soperator_resources=identity_resources,
        pvcs=_sequence_of_mappings(collected_pvcs),
        pvs=_sequence_of_mappings(collected_pvs),
        slurm_health=slurm_health,
    )
    result = {
        "node_groups": node_groups,
        "helm_releases": helm_releases if isinstance(helm_releases, list) else [],
        "crds": crd_names,
        "namespaces": namespace_names,
        "pvs": collected_pvs,
        "pvcs": collected_pvcs,
        "soperator_resources": collected_soperator_resources,
        "soperator_namespace_resources": _sanitize_namespace_resource_items(
            workloads.get("items", []) if isinstance(workloads, Mapping) else []
        ),
        "kubernetes_nodes": _sanitize_kubernetes_node_items(
            nodes.get("items", []) if isinstance(nodes, Mapping) else []
        ),
        "slurm_health": slurm_health,
        "controller_bridge_source": controller_bridge_source,
        "worker_topology_by_nodeset": worker_topology_by_nodeset,
        "gpu_stack": {
            "helm_releases": gpu_stack_helm_releases,
            "policies": (
                gpu_stack_policies.get("items", [])
                if isinstance(gpu_stack_policies, Mapping)
                else []
            ),
        },
        "cluster_identity": {
            "kubernetes_uid": kubernetes_uid,
            "soperator_uid": soperator_uid,
            "slurmcluster_uid": slurmcluster_uid,
            "jail_filesystem_id": jail_filesystem_id,
        },
        "collection_errors": collection_errors,
    }
    if normalized_identity_scope:
        result["resume_slurmcluster_identity"] = normalized_identity_scope
        selected_slurmcluster_ids = {id(item) for item in identity_resources}
        result["identity_soperator_resources"] = [
            item
            for item in _sequence_of_mappings(collected_soperator_resources)
            if str(item.get("kind", "") or "").strip().lower() != "slurmcluster"
            or id(item) in selected_slurmcluster_ids
        ]
    return result


def _sanitize_kubernetes_node_items(items: Any) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in _sequence_of_mappings(items):
        metadata = _mapping_value(item.get("metadata"))
        spec = _mapping_value(item.get("spec"))
        status = _mapping_value(item.get("status"))
        sanitized.append(
            {
                "metadata": {
                    "name": metadata.get("name"),
                    "uid": metadata.get("uid"),
                    "labels": copy.deepcopy(to_plain_data(_mapping_value(metadata.get("labels")))),
                },
                "spec": {"unschedulable": spec.get("unschedulable") is True},
                "status": {
                    "conditions": [
                        dict(copy.deepcopy(to_plain_data(dict(condition))))
                        for condition in _sequence_of_mappings(status.get("conditions"))
                    ]
                },
            }
        )
    return sanitized


def _collect_slurm_health_from_login(
    *,
    kube_context: str,
    workloads: Mapping[str, Any],
    timeout: int,
    extra_env: Mapping[str, str] | None,
) -> dict[str, Any]:
    login_pods: list[str] = []
    for item in _sequence_of_mappings(workloads.get("items")):
        if str(item.get("kind", "") or "").strip() != "Pod":
            continue
        metadata = _mapping_value(item.get("metadata"))
        status = _mapping_value(item.get("status"))
        name = str(metadata.get("name", "") or "").strip()
        labels = _mapping_value(metadata.get("labels"))
        component = normalize_component_token(
            labels.get("app.kubernetes.io/component")
            or labels.get("slurm.nebius.ai/nodeset")
            or labels.get("slurm.nebius.ai/nodeset-name")
        )
        if (
            name
            and str(status.get("phase", "") or "").strip() == "Running"
            and (component == "login" or name.startswith("login-"))
        ):
            login_pods.append(name)
    if not login_pods:
        return {
            "checked": False,
            "healthy": False,
            "reason": "running login pod not found",
        }
    pod = sorted(login_pods)[0]
    command = [
        "kubectl",
        "--context",
        kube_context,
        "-n",
        "soperator",
        "exec",
        pod,
        "--",
        "scontrol",
        "ping",
    ]
    run_env = None if extra_env is None else {**os.environ, **dict(extra_env)}
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "checked": True,
            "healthy": False,
            "pod": pod,
            "reason": str(exc),
        }
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip()
    )
    healthy = completed.returncode == 0 and bool(
        re.search(r"\bSlurmctld(?:\([^)]*\))?.*\bis\s+UP\b", output, re.IGNORECASE)
    )
    return {
        "checked": True,
        "healthy": healthy,
        "pod": pod,
        "output": output[:1000],
        "reason": "" if healthy else "scontrol ping did not report Slurmctld UP",
    }


def _bridge_resource_identity(resource: Mapping[str, Any]) -> dict[str, str]:
    metadata = _mapping_value(resource.get("metadata"))
    return {
        "api_version": str(resource.get("apiVersion", "") or "").strip(),
        "kind": str(resource.get("kind", "") or "").strip(),
        "namespace": str(metadata.get("namespace", "") or "").strip(),
        "name": str(metadata.get("name", "") or "").strip(),
        "uid": str(metadata.get("uid", "") or "").strip(),
        "resource_version": str(metadata.get("resourceVersion", "") or "").strip(),
    }


def _bridge_referenced_object_names(
    pod_spec: Mapping[str, Any],
) -> tuple[set[str], set[str]]:
    config_maps: set[str] = set()
    credentials: set[str] = set()
    for volume in _sequence_of_mappings(pod_spec.get("volumes")):
        config_map = _mapping_value(volume.get("configMap"))
        credential = _mapping_value(volume.get("secret"))
        config_name = str(config_map.get("name", "") or "").strip()
        credential_name = str(credential.get("secretName", "") or "").strip()
        if config_name:
            config_maps.add(config_name)
        if credential_name:
            credentials.add(credential_name)
        projected = _mapping_value(volume.get("projected"))
        for source in _sequence_of_mappings(projected.get("sources")):
            config_name = str(_mapping_value(source.get("configMap")).get("name", "") or "").strip()
            credential_name = str(
                _mapping_value(source.get("secret")).get("name", "") or ""
            ).strip()
            if config_name:
                config_maps.add(config_name)
            if credential_name:
                credentials.add(credential_name)
    for container_kind in ("initContainers", "containers"):
        for container in _sequence_of_mappings(pod_spec.get(container_kind)):
            for source in _sequence_of_mappings(container.get("envFrom")):
                config_name = str(
                    _mapping_value(source.get("configMapRef")).get("name", "") or ""
                ).strip()
                credential_name = str(
                    _mapping_value(source.get("secretRef")).get("name", "") or ""
                ).strip()
                if config_name:
                    config_maps.add(config_name)
                if credential_name:
                    credentials.add(credential_name)
            for item in _sequence_of_mappings(container.get("env")):
                value_from = _mapping_value(item.get("valueFrom"))
                config_name = str(
                    _mapping_value(value_from.get("configMapKeyRef")).get("name", "") or ""
                ).strip()
                credential_name = str(
                    _mapping_value(value_from.get("secretKeyRef")).get("name", "") or ""
                ).strip()
                if config_name:
                    config_maps.add(config_name)
                if credential_name:
                    credentials.add(credential_name)
    return config_maps, credentials


def _bridge_pullable_image_digest(declared_image: str, image_id: str) -> str:
    digest_match = re.search(r"sha256:[0-9a-f]{64}$", image_id)
    if not digest_match:
        return ""
    digest = digest_match.group(0)
    candidate = re.sub(r"^[a-z][a-z0-9+.-]*://", "", image_id.strip())
    if "@sha256:" in candidate and "/" in candidate.rsplit("@", 1)[0]:
        return candidate
    repository = declared_image.split("@", 1)[0]
    final_slash = repository.rfind("/")
    final_colon = repository.rfind(":")
    if final_colon > final_slash:
        repository = repository[:final_colon]
    if not repository:
        return ""
    return f"{repository}@{digest}"


def _bridge_resource_fingerprint(
    resources: Sequence[Mapping[str, Any]],
    *,
    names: set[str],
    key_markers: tuple[str, ...] = (),
) -> tuple[str, tuple[str, ...]]:
    material: list[dict[str, Any]] = []
    matched_keys: set[str] = set()
    for resource in resources:
        identity = _bridge_resource_identity(resource)
        if identity["name"] not in names:
            continue
        hashed_fields: dict[str, str] = {}
        for key, value in sorted(
            _mapping_value(resource.get("data")).items(),
            key=lambda item: str(item[0]),
        ):
            normalized_key = str(key)
            if key_markers and not any(marker in normalized_key.lower() for marker in key_markers):
                continue
            matched_keys.add(normalized_key)
            hashed_fields[normalized_key] = hashlib.sha256(
                str(value or "").encode("utf-8")
            ).hexdigest()
        if hashed_fields:
            material.append({"identity": identity, "field_hashes": hashed_fields})
    if not material:
        return "", ()
    return (
        hashlib.sha256(_stable_json(material).encode("utf-8")).hexdigest(),
        tuple(sorted(matched_keys)),
    )


def _controller_bridge_source_record(
    *,
    workloads: Sequence[Mapping[str, Any]],
    soperator_resources: Sequence[Mapping[str, Any]],
    pvcs: Sequence[Mapping[str, Any]],
    pvs: Sequence[Mapping[str, Any]],
    slurm_health: Mapping[str, Any],
) -> dict[str, Any]:
    """Capture immutable bridge inputs and one-way hashes, never credential values."""

    blockers: list[str] = []
    controller_pods = [
        item
        for item in workloads
        if str(item.get("kind", "") or "") == "Pod"
        and str(_mapping_value(item.get("metadata")).get("namespace", "") or "") == "soperator"
        and (
            str(_mapping_value(item.get("metadata")).get("name", "") or "") == "controller-0"
            or normalize_component_token(
                _mapping_value(_mapping_value(item.get("metadata")).get("labels")).get(
                    "slurm.nebius.ai/nodeset-name"
                )
            )
            == "controller"
        )
    ]
    controller_workloads = [
        item
        for item in workloads
        if str(item.get("kind", "") or "") == "StatefulSet"
        and normalize_component_token(_mapping_value(item.get("metadata")).get("name"))
        == "controller"
    ]
    slurmclusters = [
        item
        for item in soperator_resources
        if str(item.get("kind", "") or "").lower() == "slurmcluster"
        and str(_mapping_value(item.get("metadata")).get("namespace", "") or "") == "soperator"
    ]
    if len(controller_pods) != 1:
        blockers.append("expected exactly one live source controller Pod")
    if len(controller_workloads) != 1:
        blockers.append("expected exactly one live source controller StatefulSet")
    if len(slurmclusters) != 1:
        blockers.append("expected exactly one source SlurmCluster")

    pod = controller_pods[0] if len(controller_pods) == 1 else {}
    workload = controller_workloads[0] if len(controller_workloads) == 1 else {}
    slurmcluster = slurmclusters[0] if len(slurmclusters) == 1 else {}
    pod_spec = _mapping_value(pod.get("spec"))
    pod_status = _mapping_value(pod.get("status"))

    containers = _sequence_of_mappings(pod_spec.get("containers"))
    slurmctld_container = next(
        (
            item
            for item in containers
            if "slurmctld" in str(item.get("name", "") or "").lower()
            or str(item.get("name", "") or "").lower() == "controller"
        ),
        containers[0] if containers else {},
    )
    container_name = str(slurmctld_container.get("name", "") or "").strip()
    declared_image = str(slurmctld_container.get("image", "") or "").strip()
    container_status = next(
        (
            item
            for item in _sequence_of_mappings(pod_status.get("containerStatuses"))
            if str(item.get("name", "") or "") == container_name
        ),
        {},
    )
    image_id = str(container_status.get("imageID", "") or "").strip()
    resolved_image = _bridge_pullable_image_digest(declared_image, image_id)
    if not declared_image:
        blockers.append("source controller declared image is missing")
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", resolved_image):
        blockers.append("source controller resolved image digest is missing")

    config_map_names, credential_names = _bridge_referenced_object_names(pod_spec)
    config_maps = [item for item in workloads if str(item.get("kind", "") or "") == "ConfigMap"]
    credentials = [item for item in workloads if str(item.get("kind", "") or "") == "Secret"]
    configuration_fingerprint, configuration_keys = _bridge_resource_fingerprint(
        config_maps,
        names=config_map_names,
    )
    munge_fingerprint, munge_keys = _bridge_resource_fingerprint(
        credentials,
        names=credential_names,
        key_markers=("munge",),
    )
    jwt_fingerprint, jwt_keys = _bridge_resource_fingerprint(
        credentials,
        names=credential_names,
        key_markers=("jwt", "jwks"),
    )
    if not configuration_fingerprint:
        blockers.append("referenced source controller configuration could not be fingerprinted")
    if not munge_fingerprint:
        blockers.append("referenced source MUNGE material could not be fingerprinted")
    if not jwt_fingerprint:
        blockers.append("referenced source JWT material could not be fingerprinted")

    return _complete_controller_bridge_source_record(
        blockers=blockers,
        pod=pod,
        workload=workload,
        slurmcluster=slurmcluster,
        pvcs=pvcs,
        pvs=pvs,
        slurm_health=slurm_health,
        container_name=container_name,
        declared_image=declared_image,
        resolved_image=resolved_image,
        config_map_names=config_map_names,
        configuration_keys=configuration_keys,
        configuration_fingerprint=configuration_fingerprint,
        credential_names=credential_names,
        munge_keys=munge_keys,
        munge_fingerprint=munge_fingerprint,
        jwt_keys=jwt_keys,
        jwt_fingerprint=jwt_fingerprint,
    )


def _complete_controller_bridge_source_record(
    *,
    blockers: list[str],
    pod: Mapping[str, Any],
    workload: Mapping[str, Any],
    slurmcluster: Mapping[str, Any],
    pvcs: Sequence[Mapping[str, Any]],
    pvs: Sequence[Mapping[str, Any]],
    slurm_health: Mapping[str, Any],
    container_name: str,
    declared_image: str,
    resolved_image: str,
    config_map_names: set[str],
    configuration_keys: Sequence[str],
    configuration_fingerprint: str,
    credential_names: set[str],
    munge_keys: Sequence[str],
    munge_fingerprint: str,
    jwt_keys: Sequence[str],
    jwt_fingerprint: str,
) -> dict[str, Any]:
    pod_spec = _mapping_value(pod.get("spec"))
    claim_names = {
        str(_mapping_value(volume.get("persistentVolumeClaim")).get("claimName", "") or "").strip()
        for volume in _sequence_of_mappings(pod_spec.get("volumes"))
        if str(
            _mapping_value(volume.get("persistentVolumeClaim")).get("claimName", "") or ""
        ).strip()
    }
    controller_pvcs = [
        item
        for item in pvcs
        if str(_mapping_value(item.get("metadata")).get("namespace", "") or "") == "soperator"
        and str(_mapping_value(item.get("metadata")).get("name", "") or "") in claim_names
        and any(
            marker in str(_mapping_value(item.get("metadata")).get("name", "") or "").lower()
            for marker in ("controller", "spool", "slurm-state")
        )
    ]
    if len(controller_pvcs) != 1:
        blockers.append("expected exactly one source controller state PVC")
    controller_pvc = controller_pvcs[0] if len(controller_pvcs) == 1 else {}
    controller_pvc_identity = _bridge_resource_identity(controller_pvc)
    volume_name = str(_mapping_value(controller_pvc.get("spec")).get("volumeName", "") or "")
    controller_pvs = [
        item
        for item in pvs
        if str(_mapping_value(item.get("metadata")).get("name", "") or "") == volume_name
    ]
    if len(controller_pvs) != 1:
        blockers.append("source controller state PV binding is missing or ambiguous")
    controller_pv_identity = _bridge_resource_identity(
        controller_pvs[0] if len(controller_pvs) == 1 else {}
    )

    jail_binding = _soperator_jail_pvc_binding((slurmcluster,)) if slurmcluster else None
    jail_pvcs = [
        item
        for item in pvcs
        if jail_binding is not None
        and str(_mapping_value(item.get("metadata")).get("namespace", "") or "") == jail_binding[0]
        and str(_mapping_value(item.get("metadata")).get("name", "") or "") == jail_binding[1]
    ]
    if jail_binding is None or len(jail_pvcs) != 1:
        blockers.append("expected exactly one source controller Jail PVC")
    jail_pvc = jail_pvcs[0] if len(jail_pvcs) == 1 else {}
    jail_pvc_identity = _bridge_resource_identity(jail_pvc)
    jail_pv_name = str(_mapping_value(jail_pvc.get("spec")).get("volumeName", "") or "")
    jail_pvs = [
        item
        for item in pvs
        if str(_mapping_value(item.get("metadata")).get("name", "") or "") == jail_pv_name
    ]
    if len(jail_pvs) != 1:
        blockers.append("source controller Jail PV binding is missing or ambiguous")
    jail_pv = jail_pvs[0] if len(jail_pvs) == 1 else {}
    jail_pv_identity = _bridge_resource_identity(jail_pv)
    jail_pv_spec = _mapping_value(jail_pv.get("spec"))
    jail_local_path = str(_mapping_value(jail_pv_spec.get("local")).get("path", "") or "")
    jail_capacity = str(_mapping_value(jail_pv_spec.get("capacity")).get("storage", "") or "")
    jail_storage_class = str(
        _mapping_value(jail_pvc.get("spec")).get("storageClassName", "")
        or jail_pv_spec.get("storageClassName", "")
        or ""
    )
    jail_access_modes = [
        str(item) for item in jail_pv_spec.get("accessModes", []) or [] if str(item).strip()
    ]
    jail_volume_mode = str(jail_pv_spec.get("volumeMode", "") or "Filesystem")
    if jail_pvc_identity.get("name") not in claim_names:
        blockers.append("source controller Pod does not mount the discovered Jail PVC")
    if not jail_local_path.startswith("/"):
        blockers.append("source controller Jail PV must expose an absolute local SFS path")
    if not jail_capacity or not jail_storage_class or not jail_access_modes:
        blockers.append("source controller Jail PV/PVC storage contract is incomplete")

    identities = {
        "SlurmCluster": _bridge_resource_identity(slurmcluster),
        "controller StatefulSet": _bridge_resource_identity(workload),
        "controller Pod": _bridge_resource_identity(pod),
        "controller PVC": controller_pvc_identity,
        "controller PV": controller_pv_identity,
        "controller Jail PVC": jail_pvc_identity,
        "controller Jail PV": jail_pv_identity,
    }
    for label, identity in identities.items():
        if not identity.get("name") or not identity.get("uid"):
            blockers.append(f"{label} immutable identity is incomplete")

    version_material = " ".join(
        [declared_image, resolved_image, str(slurm_health.get("output", "") or "")]
    )
    version_match = re.search(r"(?<!\d)(\d{2}\.\d{2}(?:\.\d+)?)(?!\d)", version_material)
    slurm_version = version_match.group(1) if version_match else ""
    if not slurm_version:
        blockers.append("source Slurm version could not be resolved")

    return {
        "status": "ready" if not blockers else "blocked",
        "blockers": blockers,
        "slurmcluster": identities["SlurmCluster"],
        "controller_workload": identities["controller StatefulSet"],
        "controller_pod": {
            **identities["controller Pod"],
            "node_name": str(pod_spec.get("nodeName", "") or "").strip(),
            "container_name": container_name,
            "declared_image": declared_image,
            "resolved_image_digest": resolved_image,
            "slurm_version": slurm_version,
        },
        "controller_pvc": controller_pvc_identity,
        "controller_pv": controller_pv_identity,
        "jail_pvc": jail_pvc_identity,
        "jail_pv": jail_pv_identity,
        "jail_storage": {
            "filesystem_id": "",
            "local_path": jail_local_path,
            "storage_class_name": jail_storage_class,
            "storage_size": jail_capacity,
            "access_modes": jail_access_modes,
            "volume_mode": jail_volume_mode,
        },
        "configuration": {
            "config_map_names": sorted(config_map_names),
            "data_keys": list(configuration_keys),
            "fingerprint": configuration_fingerprint,
        },
        "munge": {
            "object_names": sorted(credential_names),
            "data_keys": list(munge_keys),
            "fingerprint": munge_fingerprint,
        },
        "jwt": {
            "object_names": sorted(credential_names),
            "data_keys": list(jwt_keys),
            "fingerprint": jwt_fingerprint,
        },
    }


def _sanitize_namespace_resource_items(items: Any) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return sanitized
    for item in items:
        if not isinstance(item, Mapping):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        row: dict[str, Any] = {
            "apiVersion": item.get("apiVersion"),
            "kind": item.get("kind"),
            "metadata": {
                "name": metadata.get("name"),
                "namespace": metadata.get("namespace"),
                "labels": metadata.get("labels")
                if isinstance(metadata.get("labels"), Mapping)
                else {},
            },
        }
        kind = str(item.get("kind", "") or "")
        if kind == "Secret":
            row["type"] = item.get("type")
            data = item.get("data")
            row["data_keys"] = sorted(str(key) for key in data) if isinstance(data, Mapping) else []
        elif kind in {"Deployment", "StatefulSet", "DaemonSet", "Pod"}:
            spec = item.get("spec") if isinstance(item.get("spec"), Mapping) else {}
            if kind in {"Deployment", "StatefulSet"}:
                row["spec"] = {"replicas": spec.get("replicas")}
            row["status"] = item.get("status") if isinstance(item.get("status"), Mapping) else {}
        elif kind == "Job":
            row["metadata"]["uid"] = metadata.get("uid")
            row["metadata"]["creationTimestamp"] = metadata.get("creationTimestamp")
            row["status"] = item.get("status") if isinstance(item.get("status"), Mapping) else {}
            spec = item.get("spec") if isinstance(item.get("spec"), Mapping) else {}
            template = spec.get("template") if isinstance(spec.get("template"), Mapping) else {}
            pod_spec = template.get("spec") if isinstance(template.get("spec"), Mapping) else {}
            containers = pod_spec.get("containers")
            if isinstance(containers, Sequence) and not isinstance(
                containers, (str, bytes, bytearray)
            ):
                row["containers"] = [
                    {
                        "name": container.get("name"),
                        "image": container.get("image"),
                    }
                    for container in containers
                    if isinstance(container, Mapping)
                ]
            else:
                row["containers"] = []
            volumes = pod_spec.get("volumes")
            row["pvc_claim_names"] = (
                sorted(
                    {
                        str(pvc.get("claimName") or "").strip()
                        for volume in volumes
                        if isinstance(volume, Mapping)
                        and isinstance(
                            pvc := volume.get("persistentVolumeClaim"),
                            Mapping,
                        )
                        and str(pvc.get("claimName") or "").strip()
                    }
                )
                if isinstance(volumes, Sequence)
                and not isinstance(volumes, (str, bytes, bytearray))
                else []
            )
        elif kind == "Service":
            spec = item.get("spec") if isinstance(item.get("spec"), Mapping) else {}
            row["spec"] = {
                "type": spec.get("type"),
                "ports": spec.get("ports") if isinstance(spec.get("ports"), list) else [],
                "selector": spec.get("selector")
                if isinstance(spec.get("selector"), Mapping)
                else {},
            }
        elif kind == "ConfigMap":
            data = item.get("data")
            row["data_keys"] = sorted(str(key) for key in data) if isinstance(data, Mapping) else []
        sanitized.append(row)
    return sanitized


def _subprocess_error_payload(command: Sequence[str], exc: BaseException) -> dict[str, Any]:
    stderr = str(getattr(exc, "stderr", "") or "").strip()
    stdout = str(getattr(exc, "stdout", "") or "").strip()
    message = stderr or stdout or str(exc)
    return {
        "command": " ".join(str(part) for part in command),
        "message": message,
    }


def _kubectl_json(
    command: Sequence[str],
    timeout: int,
    *,
    errors: list[dict[str, Any]] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    run_env = None if extra_env is None else {**os.environ, **dict(extra_env)}
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        if errors is not None:
            errors.append(_subprocess_error_payload(command, exc))
        return {}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        if errors is not None:
            errors.append(_subprocess_error_payload(command, exc))
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _kubectl_list_json_with_bounded_retry(
    command: Sequence[str],
    timeout: int,
    *,
    errors: list[dict[str, Any]] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> Mapping[str, Any]:
    """Retry a failed inventory read without treating an authoritative empty list as failure."""
    attempt_errors: list[dict[str, Any]] = []
    payload: Mapping[str, Any] = {}
    for _attempt in range(3):
        current_errors: list[dict[str, Any]] = []
        payload = _kubectl_json(
            command,
            timeout,
            errors=current_errors,
            extra_env=extra_env,
        )
        if isinstance(payload.get("items"), list):
            return payload
        attempt_errors.extend(current_errors)
    if errors is not None:
        errors.extend(attempt_errors)
    return payload


def _last_kubectl_inventory_error(errors: Sequence[Mapping[str, Any]], *, resource: str) -> str:
    marker = f" get {resource} "
    matching = [
        error for error in errors if marker in f" {str(error.get('command', '') or '').strip()} "
    ]
    if not matching:
        return ""
    message = " ".join(str(matching[-1].get("message", "") or "").split())
    return f" Last error: {message[:500]}" if message else ""


def _helm_json(
    command: Sequence[str],
    timeout: int,
    *,
    errors: list[dict[str, Any]] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> Any:
    run_env = None if extra_env is None else {**os.environ, **dict(extra_env)}
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        if errors is not None:
            errors.append(_subprocess_error_payload(command, exc))
        return []
    try:
        return json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        if errors is not None:
            errors.append(_subprocess_error_payload(command, exc))
        return []


def _kubectl_text(
    command: Sequence[str],
    timeout: int,
    *,
    errors: list[dict[str, Any]] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> str:
    run_env = None if extra_env is None else {**os.environ, **dict(extra_env)}
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        if errors is not None:
            errors.append(_subprocess_error_payload(command, exc))
        return ""
    return completed.stdout or ""


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


def _positive_int(value: Any, *, fallback: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def parse_worker_lscpu_topology(stdout: str) -> dict[str, Any]:
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


def _worker_pods_by_nodeset(workloads: Mapping[str, Any]) -> dict[str, str]:
    pods: dict[str, str] = {}
    for item in workloads.get("items", []) if isinstance(workloads, Mapping) else []:
        if not isinstance(item, Mapping) or str(item.get("kind", "") or "") != "Pod":
            continue
        status = item.get("status") if isinstance(item.get("status"), Mapping) else {}
        if str(status.get("phase", "") or "") != "Running":
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        labels = metadata.get("labels") if isinstance(metadata.get("labels"), Mapping) else {}
        pod_name = str(metadata.get("name", "") or "").strip()
        nodeset_name = ""
        for key in ONBOARDING_NODESET_LABEL_KEYS:
            candidate = normalize_component_token(labels.get(key))
            if candidate.startswith(ONBOARDING_WORKER_ROLE_PREFIX):
                nodeset_name = candidate
                break
        if pod_name and nodeset_name:
            pods.setdefault(nodeset_name, pod_name)
    return pods


def _collect_worker_topology_by_nodeset(
    *,
    kube_context: str,
    workloads: Mapping[str, Any],
    timeout: int,
    errors: list[dict[str, Any]],
    extra_env: Mapping[str, str] | None,
) -> dict[str, Any]:
    topology_by_nodeset: dict[str, Any] = {}
    for nodeset_name, pod_name in sorted(_worker_pods_by_nodeset(workloads).items()):
        stdout = _kubectl_text(
            [
                "kubectl",
                "--context",
                kube_context,
                "-n",
                "soperator",
                "exec",
                pod_name,
                "-c",
                "slurmd",
                "--",
                "lscpu",
                "-J",
            ],
            timeout,
            errors=errors,
            extra_env=extra_env,
        )
        topology = parse_worker_lscpu_topology(stdout)
        if topology:
            topology["source_pod"] = pod_name
            topology_by_nodeset[nodeset_name] = topology
    return topology_by_nodeset
