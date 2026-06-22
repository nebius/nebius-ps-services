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

ONBOARDING_SCHEMA = "nebius-cxcli-soperator-onboarding/v2"
ONBOARDING_REPORT_DIR = "reports"
SOURCE_SOPERATOR_DISCOVERY_REPORT_NAME = "ext-soperator-onboard-source-discovery-report.json"
ONBOARDING_STATE_NO_SOPERATOR_DETECTED = "no-soperator-detected"
ONBOARDING_STATE_EXISTING_SOPERATOR_SUPPORTED = "existing-soperator-supported"
ONBOARDING_STATE_EXISTING_SOPERATOR_TARGET = "existing-soperator-target"
ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN = "existing-soperator-unknown"
ONBOARDING_STATE_EXISTING_SOPERATOR_NEWER = "existing-soperator-newer"
ONBOARDING_STATE_ANALYSIS_INCOMPLETE = "analysis-incomplete"
ONBOARDING_ACTION_INSTALL_SOPERATOR = "install-soperator"
ONBOARDING_ACTION_ADOPT_SOPERATOR = "adopt-soperator"
ONBOARDING_ACTION_UPGRADE_SOPERATOR = "upgrade-soperator"
ONBOARDING_ACTION_APPROVE_MIGRATION = "approve-soperator-migration"
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
        ONBOARDING_ACTION_APPROVE_MIGRATION,
        ONBOARDING_ACTION_CONFIGURE_STORAGE,
        ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
        ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
        ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
        ONBOARDING_ACTION_RECONCILE_TARGET_GPU_STACK,
        ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
        ONBOARDING_ACTION_ENABLE_TOPOLOGY,
    }
)
ONBOARDING_MIGRATION_ACTION_IDS = frozenset(
    {
        ONBOARDING_ACTION_UPGRADE_SOPERATOR,
        ONBOARDING_ACTION_APPROVE_MIGRATION,
        ONBOARDING_ACTION_CREATE_ALIGNED_SFS,
        ONBOARDING_ACTION_PLAN_DATA_MIGRATION,
        ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
        ONBOARDING_ACTION_UPGRADE_EXTERNAL_NODE_TEMPLATE,
    }
)
ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION = "1.33"
ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_OS = "ubuntu24.04"
ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_GPU_STACK_PRESET = "cuda13.0"
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
SOPERATOR_COMPATIBLE_RELEASE_NAMES = frozenset({"soperator", "slurm-operator"})
SOPERATOR_COMPATIBLE_CONTROLLER_RELEASE_NAMES = frozenset({"soperator-controller"})
SOPERATOR_COMPATIBLE_CHART_IDENTITIES = frozenset({"soperator", "helm-soperator", "slurm-operator"})
SOPERATOR_HELM_DISCOVERY_NAMESPACES = ("soperator", "soperator-system", "flux-system")
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
                "collection_errors": list(onboarding.get("collection_errors", []) or []),
            },
        },
        "soperator": {
            "install_mode": str((app_row or {}).get("install_mode", "") or "").strip(),
        },
    }
    return hashlib.sha256(_stable_json(material).encode("utf-8")).hexdigest()


def soperator_onboarding_report_path(target_ref: str) -> str:
    normalized = normalize_component_token(target_ref) or "mk8s"
    return f"generated/{ONBOARDING_REPORT_DIR}/soperator-onboarding-{normalized}.json"


def source_soperator_discovery_report_path(project_dir: Path) -> Path:
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
        f"apps:soperator target '{target}' uses onboard-existing-cluster but does not have "
        "a current accepted deploy.targets[].soperator_onboarding analysis. Rerun the "
        "Soperator onboarding wizard or refresh the analysis fingerprint before render/deploy."
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


def soperator_onboarding_effective_storage_mode(
    report: SoperatorOnboardingReport,
    storage_mode: str,
) -> str:
    """Return the storage mode after analyzer compatibility evidence is applied."""

    if storage_mode == ONBOARDING_STORAGE_MODE_CREATE_ALIGNED_SFS and _report_has_finding(
        report, layer="storage-sfs", status="target-compatible"
    ):
        return ONBOARDING_STORAGE_MODE_KEEP_EXISTING
    return storage_mode


def soperator_onboarding_effective_compute_mode(
    report: SoperatorOnboardingReport,
    compute_mode: str,
) -> str:
    """Return the compute mode after analyzer compatibility evidence is applied."""

    if compute_mode == ONBOARDING_COMPUTE_MODE_CREATE_ALIGNED_NODE_GROUPS and _report_has_finding(
        report, layer="placements", status="target-compatible"
    ):
        return ONBOARDING_COMPUTE_MODE_KEEP_EXISTING
    return compute_mode


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


def _migration_approval_action_title(
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
                include_compute_migration=include_compute_migration,
                include_soperator_upgrade=include_soperator_upgrade,
            ),
            requires_customer_approval=True,
        ),
    ]
    if include_external_node_template_upgrade:
        phases.append(
            _migration_phase(
                "external-node-template-upgrade",
                "Upgrade external MK8s control plane and node templates",
                progress_label=(
                    "External MK8s Upgrade: control plane first, worker groups zero-surge by default"
                ),
                requires_customer_approval=True,
                notes=(
                    "Run direct Nebius cluster and node-group updates; do not call Terraform.",
                    "Upgrade the control plane first, one Kubernetes minor at a time when needed.",
                    "Upgrade service-role source node groups with a temporary zero-surge strategy.",
                    "Upgrade worker source node groups with zero-surge by default, or safe-surge "
                    "waves when selected, and restore each group's original strategy.",
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
                    "target rollout.",
                ),
            )
        )
    if include_data_migration:
        phases.extend(
            [
                _migration_phase(
                    "create-aligned-sfs",
                    "Create aligned Nebius SFS filesystems and attach old and new storage",
                    progress_label="Creating aligned SFS filesystems...",
                    requires_customer_approval=True,
                    notes=(
                        "Old storage remains mounted and active while target SFS is attached.",
                        "No storage references are cut over in this phase.",
                    ),
                ),
                _migration_phase(
                    "online-bulk-data-sync",
                    "Online bulk data sync from old storage to target SFS",
                    progress_label="Data Migration: online bulk sync",
                    notes=(
                        "Preserve ownership, ACLs, xattrs, symlinks, hardlinks, and timestamps.",
                        "Run while old storage remains the active writer.",
                    ),
                ),
            ]
        )
    if include_compute_migration or include_soperator_upgrade:
        if include_compute_migration:
            rolling_title = "In-place compute remediation with preserved worker node groups"
            rolling_progress_label = (
                "Compute Remediation: service roles aligned, preserved worker groups verified, "
                "<running jobs> jobs remaining"
            )
            rolling_notes = (
                "Create or reuse service-role node groups without duplicating worker capacity.",
                "Map worker NodeSets to detected existing worker node groups.",
                "Apply migration-owned template changes with serial zero-surge service-role "
                "updates and zero-surge worker updates by default.",
            )
        else:
            rolling_title = "Soperator chart upgrade with existing compute layout"
            rolling_progress_label = (
                "Soperator Upgrade: existing compute layout verified, <running jobs> jobs remaining"
            )
            rolling_notes = (
                "Reuse detected service-role and worker node groups.",
                "Apply target Soperator values without creating parallel worker capacity.",
                "Verify the preserved worker NodeSets before accepting production jobs.",
            )
        phases.append(
            _migration_phase(
                "rolling-compute-migration",
                rolling_title,
                progress_label=rolling_progress_label,
                notes=rolling_notes,
            )
        )
    if include_data_migration or include_compute_migration or include_soperator_upgrade:
        if include_data_migration and include_compute_migration:
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

    effective_storage_mode = soperator_onboarding_effective_storage_mode(report, storage_mode)
    effective_compute_mode = soperator_onboarding_effective_compute_mode(report, compute_mode)
    include_data_migration = effective_storage_mode == ONBOARDING_STORAGE_MODE_CREATE_ALIGNED_SFS
    include_compute_migration = (
        effective_compute_mode == ONBOARDING_COMPUTE_MODE_CREATE_ALIGNED_NODE_GROUPS
    )
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
        if action.id == ONBOARDING_ACTION_APPROVE_MIGRATION:
            action = replace(
                action,
                title=_migration_approval_action_title(
                    include_data_migration=include_data_migration,
                    include_compute_migration=include_compute_migration,
                    include_soperator_upgrade=include_soperator_upgrade,
                ),
            )
        filtered_actions.append(action)
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
        ONBOARDING_ACTION_APPROVE_MIGRATION: SoperatorOnboardingAction(
            id=ONBOARDING_ACTION_APPROVE_MIGRATION,
            title=_migration_approval_action_title(
                include_data_migration=include_data_migration,
                include_compute_migration=include_compute_migration,
                include_soperator_upgrade=include_soperator_upgrade,
            ),
            layer="migration",
            selected=True,
            disruptive=True,
            reason="Migration changes require customer approval before execution.",
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
            reason="Detected storage is not compatible with the target Soperator layout.",
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
            title="Plan in-place compute remediation without duplicating workers",
            layer="placements",
            selected=True,
            disruptive=True,
            reason=(
                "Service-role layout changes need guarded remediation; worker node groups "
                "stay in place while external node-template upgrades run through the "
                "migration executor."
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
            title="Upgrade external MK8s control plane and node templates",
            layer="mk8s-node-template",
            selected=True,
            disruptive=True,
            reason=(
                "External Soperator targets are not Terraform-owned, so cxcli must align "
                "Kubernetes version, node OS image, and Nebius GPU driver preset through "
                "direct Nebius updates during migration."
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
    if not selected_ids & ONBOARDING_MIGRATION_ACTION_IDS:
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
) -> dict[str, Any]:
    target_k8s = ONBOARDING_EXTERNAL_NODE_TEMPLATE_TARGET_K8S_VERSION
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
    source_version_override: str = "",
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
    )
    manual_source_version = normalize_soperator_release_version(source_version_override)
    target_version = normalize_soperator_release_version(pinned_chart_version or pinned_app_version)

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
                        "detected and can be reused without creating a replacement compute layout."
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
                "and deploy-time GPU readiness, CUDA smoke, and NCCL validations. "
                "This is not a failure signal."
            )
        elif gpu_stack_evidence.get("live_evidence_available"):
            gpu_stack_status = "reconcile-planned"
            gpu_stack_severity = "required"
            gpu_stack_message = (
                "GPU node groups were discovered, but live GPU-stack evidence is incomplete "
                "or not fully healthy in the onboarding snapshot; cxcli will reconcile the "
                "target GPU Operator, Network Operator when GPU-cluster/RDMA-capable, and "
                "deploy-time GPU readiness, CUDA smoke, and NCCL validations."
            )
        else:
            gpu_stack_status = "reconcile-planned"
            gpu_stack_severity = "required"
            gpu_stack_message = (
                "GPU node groups were discovered; cxcli will manage the target GPU stack as "
                "desired state, including GPU Operator, Network Operator when the target is "
                "GPU-cluster/RDMA-capable, and deploy-time GPU readiness, CUDA smoke, "
                "and NCCL validations. This does not mean the current stack is broken."
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
                    "preserve the discovered storage contract without an SFS migration plan."
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
    incompatible_release = next(
        (release for release in incompatible_releases if release not in shadowed_stale_releases),
        None,
    )
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

    detected_source_version = (
        _release_detected_version(soperator_release)
        if isinstance(soperator_release, Mapping)
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
                    status="noncanonical-release-detected",
                    severity="recommended",
                    message=(
                        "Compatible Soperator release version was detected and matched to a "
                        "migration profile; the noncanonical Soperator-like Helm release is "
                        "kept as review evidence instead of requiring source-version input."
                    ),
                    evidence={
                        "release": dict(incompatible_release),
                        "source_version": detected_source_version,
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
                    "Existing Soperator-like Helm release has noncanonical identity; "
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

    if source_version and (isinstance(soperator_release, Mapping) or manual_source_version_applies):
        if isinstance(soperator_release, Mapping):
            live_chart = _release_chart_version(soperator_release)
            live_app = str(soperator_release.get("app_version", "") or "").strip()
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
            if migration_profile is not None and not release_identity_needs_source_version:
                state = ONBOARDING_STATE_EXISTING_SOPERATOR_SUPPORTED
                remediation = _remediation_items_for_profile(
                    profile=migration_profile,
                    storage_present=storage_present,
                    target_version=pinned_chart_version or target_version,
                )
                external_node_template_required = not bool(node_template_inventory.get("complete"))
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
                        id=ONBOARDING_ACTION_APPROVE_MIGRATION,
                        title="Approve Soperator role, storage, and SlurmCluster remediation",
                        layer="migration",
                        selected=True,
                        disruptive=True,
                        reason="Migration changes require customer approval before execution.",
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
                                "External MK8s control plane and node templates will be "
                                "upgraded during migration through direct Nebius updates "
                                "because the target is not Terraform-owned by cxcli."
                                if provider_inventory_available
                                else (
                                    "External MK8s control plane and node-template provider "
                                    "inventory was not available during onboarding; cxcli will "
                                    "verify and align the external node templates during "
                                    "migration before it manages Soperator."
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
                            title="Upgrade external MK8s control plane and node templates",
                            layer="mk8s-node-template",
                            selected=True,
                            disruptive=True,
                            reason=(
                                "External Soperator targets are not Terraform-owned, so cxcli "
                                "must align Kubernetes version, node OS image, and Nebius GPU "
                                "driver preset through direct Nebius updates during migration."
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
                                "skip external node-template remediation unless live state drifts."
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
                            title="Plan in-place compute remediation without duplicating workers",
                            layer="placements",
                            selected=True,
                            disruptive=True,
                            reason=(
                                "Service-role layout changes need guarded remediation; worker node "
                                "groups stay in place while external node-template upgrades run "
                                "through the migration executor."
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


def _source_report_selected_action_ids(report: Mapping[str, Any]) -> tuple[str, ...]:
    actions = report.get("actions", [])
    if not isinstance(actions, list):
        return ()
    ids: list[str] = []
    for action in actions:
        if not isinstance(action, Mapping) or action.get("selected") is not True:
            continue
        action_id = str(action.get("id", "") or "").strip()
        if action_id:
            ids.append(action_id)
    return tuple(ids)


def _matching_source_discovery_report(
    *,
    source_report_path: Path | None,
    target_ref: str,
    onboarding: Mapping[str, Any],
) -> dict[str, Any] | None:
    if source_report_path is None or not source_report_path.exists():
        return None
    try:
        payload = json.loads(source_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    report = payload.get("report")
    if not isinstance(report, Mapping):
        return None
    if normalize_component_token(report.get("target_ref")) != normalize_component_token(target_ref):
        return None
    action_ids = _onboarding_action_ids(onboarding)
    if _source_report_selected_action_ids(report) != action_ids:
        return None
    for key in ("state", "source_version", "target_version", "migration_profile_id"):
        expected = str(onboarding.get(key, "") or "").strip()
        actual = str(report.get(key, "") or "").strip()
        if expected and actual != expected:
            return None
    return copy.deepcopy(dict(report))


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
) -> list[Path]:
    written: list[Path] = []
    reports_dir = generated_dir / ONBOARDING_REPORT_DIR
    for target_ref in soperator_onboarding_target_refs(payload_or_config):
        report = build_soperator_onboarding_report_from_config(
            payload_or_config,
            target_ref=target_ref,
            pinned_chart_version=pinned_chart_version,
            pinned_app_version=pinned_app_version,
            source_report_path=source_soperator_discovery_report_path(generated_dir.parent),
        )
        path = reports_dir / f"soperator-onboarding-{target_ref}.json"
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
    cluster_id: str = "",
    cluster_name: str = "",
) -> Path:
    path = source_soperator_discovery_report_path(target_dir)
    report_payload = (
        report.to_dict() if isinstance(report, SoperatorOnboardingReport) else dict(report)
    )
    payload = {
        "schema": "nebius-cxcli-source-soperator-discovery/v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "target_ref": normalize_component_token(target_ref),
        "cluster_id": str(cluster_id or "").strip(),
        "cluster_name": str(cluster_name or "").strip(),
        "report": report_payload,
        "snapshot": to_plain_data(snapshot),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _preserve_source_discovery_timestamps_if_stable(path=path, payload=payload)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists():
        with suppress(OSError):
            if path.read_text(encoding="utf-8") == rendered:
                return path
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
            handle.write(rendered)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            with suppress(OSError):
                tmp_path.unlink()
    return path


def collect_kubectl_soperator_snapshot(
    *,
    kube_context: str,
    timeout: int = 30,
    extra_env: Mapping[str, str] | None = None,
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
    pvs = _kubectl_json(
        ["kubectl", "--context", context, "get", "pv", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    pvcs = _kubectl_json(
        ["kubectl", "--context", context, "get", "pvc", "-A", "-o", "json"],
        timeout,
        errors=collection_errors,
        extra_env=extra_env,
    )
    workloads: Mapping[str, Any] = {}
    if "soperator" in namespace_names:
        workloads = _kubectl_json(
            [
                "kubectl",
                "--context",
                context,
                "get",
                "deployments,statefulsets,daemonsets,pods,services,configmaps,secrets",
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
        soperator_resources = _kubectl_json(
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
    helm_releases = []
    for namespace in SOPERATOR_HELM_DISCOVERY_NAMESPACES:
        namespace_releases = _helm_json(
            ["helm", "--kube-context", context, "list", "-n", namespace, "-o", "json"],
            timeout,
            errors=collection_errors,
            extra_env=extra_env,
        )
        if isinstance(namespace_releases, list):
            helm_releases.extend(namespace_releases)
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
    return {
        "node_groups": node_groups,
        "helm_releases": helm_releases if isinstance(helm_releases, list) else [],
        "crds": crd_names,
        "namespaces": namespace_names,
        "pvs": pvs.get("items", []) if isinstance(pvs, Mapping) else [],
        "pvcs": pvcs.get("items", []) if isinstance(pvcs, Mapping) else [],
        "soperator_resources": (
            soperator_resources.get("items", []) if isinstance(soperator_resources, Mapping) else []
        ),
        "soperator_namespace_resources": _sanitize_namespace_resource_items(
            workloads.get("items", []) if isinstance(workloads, Mapping) else []
        ),
        "worker_topology_by_nodeset": worker_topology_by_nodeset,
        "gpu_stack": {
            "helm_releases": gpu_stack_helm_releases,
            "policies": (
                gpu_stack_policies.get("items", [])
                if isinstance(gpu_stack_policies, Mapping)
                else []
            ),
        },
        "collection_errors": collection_errors,
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
            row["status"] = item.get("status") if isinstance(item.get("status"), Mapping) else {}
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
