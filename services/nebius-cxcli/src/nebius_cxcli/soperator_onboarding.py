"""Read-only Soperator onboarding analysis helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .component_instances import normalize_component_token
from .runtime_config import to_plain_data

ONBOARDING_SCHEMA = "nebius-cxcli-soperator-onboarding/v2"
ONBOARDING_REPORT_DIR = "reports"
SOURCE_SOPERATOR_DISCOVERY_REPORT_NAME = "source-soperator-cluster-discovery-report.json"
ONBOARDING_STATE_NO_SOPERATOR_DETECTED = "no-soperator-detected"
ONBOARDING_STATE_EXISTING_SOPERATOR_SUPPORTED = "existing-soperator-supported"
ONBOARDING_STATE_EXISTING_SOPERATOR_TARGET = "existing-soperator-target"
ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN = "existing-soperator-unknown"
ONBOARDING_STATE_EXISTING_SOPERATOR_NEWER = "existing-soperator-newer"
ONBOARDING_STATE_ANALYSIS_BLOCKED = "analysis-blocked"
ONBOARDING_ACTION_INSTALL_SOPERATOR = "install-soperator"
ONBOARDING_ACTION_ADOPT_SOPERATOR = "adopt-soperator"
ONBOARDING_ACTION_UPGRADE_SOPERATOR = "upgrade-soperator"
ONBOARDING_ACTION_APPROVE_MIGRATION = "approve-soperator-migration"
ONBOARDING_ACTION_CONFIGURE_STORAGE = "configure-soperator-storage"
ONBOARDING_ACTION_CREATE_ALIGNED_SFS = "create-aligned-sfs"
ONBOARDING_ACTION_PLAN_DATA_MIGRATION = "plan-soperator-data-migration"
ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION = "plan-soperator-compute-migration"
ONBOARDING_ACTION_ENABLE_TOPOLOGY = "enable-slurm-topology"
ONBOARDING_ACTION_REVIEW_GPU_RDMA = "review-gpu-rdma"
ONBOARDING_REQUIRED_STORAGE_KEYS = ("jail", "controller-spool", "accounting")
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
    blocked: bool = False
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
    return hashlib.sha256(_stable_json(_stable_analysis_snapshot(snapshot)).encode("utf-8")).hexdigest()


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
                "target_version": str(onboarding.get("target_version", "") or "").strip(),
                "source_version": str(onboarding.get("source_version", "") or "").strip(),
                "migration_profile_id": str(
                    onboarding.get("migration_profile_id", "") or ""
                ).strip(),
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
    raise ValueError(
        f"apps:soperator target '{target}' uses onboard-existing-cluster but does not have "
        "a current accepted deploy.targets[].soperator_onboarding analysis. Rerun the "
        "Soperator onboarding wizard or refresh the analysis fingerprint before render/deploy."
    )


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
    suffix = text[core_index + len(core_text):]
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


def soperator_migration_profile_for_version(version: str) -> Mapping[str, Any] | None:
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
        if normalize_soperator_release_version(str(row.get("version", "") or "")) == normalized:
            result = dict(row)
            profile_id = str(row.get("profile_id", "") or "").strip()
            profile_group = profile_groups.get(profile_id)
            if isinstance(profile_group, Mapping):
                result["profile_group"] = dict(profile_group)
                for key in ("requires_aligned_sfs", "compatibility_axes"):
                    if key in profile_group and key not in result:
                        result[key] = to_plain_data(profile_group[key])
            return result
    return None


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


def _default_soperator_migration_plan(
    *,
    include_data_migration: bool,
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
            "Customer approval of role, NodeSet, SlurmCluster, and storage changes",
            requires_customer_approval=True,
        ),
    ]
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
    phases.extend(
        [
            _migration_phase(
                "rolling-compute-migration",
                "Rolling compute migration by draining and validating nodes in batches",
                progress_label=(
                    "Compute Migration: <drained>/<total> nodes, <ready> target nodes, "
                    "<running jobs> jobs remaining"
                ),
                notes=(
                    "Do not terminate running jobs; drain old nodes and admit target nodes in batches.",
                    "Validate target NodeSets before accepting production jobs.",
                ),
            ),
            _migration_phase(
                "final-control-plane-cutover",
                "Final Slurm controller, accounting, login, and storage-reference cutover",
                progress_label="Data Migration: final delta and control-plane cutover",
                requires_customer_approval=True,
                quiet_window=True,
                notes=(
                    "Pause new scheduling or drain partitions according to customer policy.",
                    "Run a final delta sync before updating Soperator values or CRs.",
                ),
            ),
            _migration_phase(
                "validation-and-rollback-hold",
                "Validation and rollback hold",
                notes=("Keep old storage and old compute resources available until validation passes.",),
            ),
            _migration_phase(
                "retire-old-resources",
                "Retire old storage and old resources only after explicit approval",
                requires_customer_approval=True,
            ),
        ]
    )
    return tuple(phases)


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
            id="role-layout-review",
            title="Review role layout, NodeSets, and SlurmCluster spec changes",
            classification="customer-approval-required",
            reason=(
                "Role, NodeSet, scheduling, accounting, REST, or controller changes can affect "
                "running Slurm workflows."
            ),
            requires_customer_approval=True,
        ),
    ]
    migration_class = str((profile or {}).get("migration_class", "") or "")
    requires_aligned_sfs = bool((profile or {}).get("requires_aligned_sfs", False))
    if migration_class == "storage-and-layout-migration" or requires_aligned_sfs or not storage_present:
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


def _is_soperator_release_candidate(release: Mapping[str, Any]) -> bool:
    return _release_name(release) == "soperator" or _release_chart_identity(release) == "soperator"


def _is_compatible_soperator_release(release: Mapping[str, Any]) -> bool:
    namespace = _release_namespace(release).lower()
    chart_identity = _release_chart_identity(release)
    return (
        _release_name(release) == "soperator"
        and namespace in {"", "soperator"}
        and chart_identity in {"", "soperator"}
    )


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


def analyze_soperator_onboarding_snapshot(
    snapshot: Mapping[str, Any],
    *,
    target_ref: str,
    pinned_chart_version: str = "",
    pinned_app_version: str = "",
) -> SoperatorOnboardingReport:
    node_groups = snapshot.get("node_groups")
    if not isinstance(node_groups, Mapping):
        node_groups = {}
    cpu_groups, gpu_groups = _node_group_kinds(node_groups)
    releases = _sequence_of_mappings(snapshot.get("helm_releases"))
    soperator_candidates = tuple(release for release in releases if _is_soperator_release_candidate(release))
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
    storage_keys = {normalize_component_token(key) for key in storage} if isinstance(storage, Mapping) else set()
    rdma_groups = _node_groups_with_rdma(node_groups)
    topology_groups = _node_groups_with_topology_labels(node_groups)

    findings: list[SoperatorOnboardingFinding] = []
    actions: list[SoperatorOnboardingAction] = []
    collection_errors = _sequence_of_mappings(snapshot.get("collection_errors"))
    if collection_errors:
        findings.append(
            SoperatorOnboardingFinding(
                layer="kubernetes",
                status="blocked",
                severity="blocked",
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
            state="analysis-blocked",
            fingerprint=_analysis_fingerprint(snapshot),
            findings=tuple(findings),
            actions=(),
        )

    if not node_groups:
        findings.append(
            SoperatorOnboardingFinding(
                layer="kubernetes",
                status="blocked",
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
                    layer="role-mapping",
                    status="blocked",
                    severity="blocked",
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

    if not gpu_groups:
        findings.append(
            SoperatorOnboardingFinding(
                layer="gpu-rdma",
                status="warning",
                severity="recommended",
                message="No GPU node group was discovered; worker role mapping will be CPU-only.",
            )
        )
    elif not rdma_groups:
        findings.append(
            SoperatorOnboardingFinding(
                layer="gpu-rdma",
                status="manual-review",
                severity="recommended",
                message=(
                    "GPU node groups were discovered, but no scheduler-visible RDMA resources "
                    "were found in node allocatable data."
                ),
                action_id=ONBOARDING_ACTION_REVIEW_GPU_RDMA,
            )
        )
        actions.append(
            SoperatorOnboardingAction(
                id=ONBOARDING_ACTION_REVIEW_GPU_RDMA,
                title="Review GPU/RDMA readiness before production distributed jobs",
                layer="gpu-rdma",
                selected=True,
                reason="GPU workers exist but RDMA resources were not discovered.",
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

    storage_present = bool(storage_keys & set(ONBOARDING_REQUIRED_STORAGE_KEYS)) or bool(pvcs or pvs)
    if storage_present:
        findings.append(
            SoperatorOnboardingFinding(
                layer="storage-sfs",
                status="detected",
                severity="info",
                message="Existing storage primitives were detected for onboarding/adoption review.",
                evidence={"storage_keys": sorted(storage_keys), "pvcs": len(pvcs), "pvs": len(pvs)},
            )
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

    incompatible_release = next(
        (
            release
            for release in soperator_candidates
            if not _is_compatible_soperator_release(release)
        ),
        None,
    )

    if incompatible_release is not None:
        state = ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN
        findings.append(
            SoperatorOnboardingFinding(
                layer="soperator",
                status="blocked",
                severity="blocked",
                message=(
                    "Existing Soperator-like Helm release has incompatible release name, "
                    "namespace, or chart identity; cxcli will not take it over silently."
                ),
                evidence={"release": dict(incompatible_release)},
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

    source_version = _release_detected_version(soperator_release) if isinstance(soperator_release, Mapping) else ""
    target_version = normalize_soperator_release_version(pinned_chart_version or pinned_app_version)
    migration_profile: Mapping[str, Any] | None = None
    migration_profile_id = ""
    remediation: tuple[SoperatorRemediationItem, ...] = ()
    migration_plan: tuple[SoperatorMigrationPhase, ...] = ()

    if isinstance(soperator_release, Mapping):
        live_chart = _release_chart_version(soperator_release)
        live_app = str(soperator_release.get("app_version", "") or "").strip()
        comparison = compare_chart_versions(live_chart, pinned_chart_version)
        app_comparison = compare_chart_versions(live_app, pinned_app_version)
        migration_profile = soperator_migration_profile_for_version(source_version)
        if migration_profile is None:
            state = ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN
            findings.append(
                SoperatorOnboardingFinding(
                    layer="migration-profile",
                    status="blocked",
                    severity="blocked",
                    message=(
                        "Detected Soperator release does not match a committed cxcli "
                        "migration profile. Refresh the profile history before onboarding."
                    ),
                    evidence={"source_version": source_version or live_chart or live_app},
                )
            )
        else:
            migration_profile_id = str(migration_profile.get("profile_id", "") or "").strip()
            findings.append(
                SoperatorOnboardingFinding(
                    layer="migration-profile",
                    status="matched",
                    severity="info",
                    message=(
                        "Detected Soperator release matched migration profile "
                        f"'{migration_profile_id}'."
                    ),
                    evidence={
                        "source_version": source_version,
                        "target_version": target_version,
                        "generation": str(migration_profile.get("generation", "") or ""),
                    },
                )
            )
        if comparison == "older" or app_comparison == "older":
            if migration_profile is not None:
                state = ONBOARDING_STATE_EXISTING_SOPERATOR_SUPPORTED
                remediation = _remediation_items_for_profile(
                    profile=migration_profile,
                    storage_present=storage_present,
                    target_version=pinned_chart_version or target_version,
                )
                migration_plan = _default_soperator_migration_plan(
                    include_data_migration=any(
                        item.classification == "data-sensitive" for item in remediation
                    )
                )
            findings.append(
                SoperatorOnboardingFinding(
                    layer="versions",
                    status="upgrade-available",
                    severity="recommended",
                    message="Existing Soperator version is older than the cxcli-pinned version.",
                    action_id=ONBOARDING_ACTION_UPGRADE_SOPERATOR,
                    evidence={"live_chart": live_chart, "live_app": live_app},
                )
            )
            if migration_profile is not None:
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
                        title=(
                            "Review and approve Soperator role, storage, and SlurmCluster "
                            "remediation"
                        ),
                        layer="migration",
                        selected=True,
                        disruptive=True,
                        reason="Migration changes require customer approval before execution.",
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
                actions.append(
                    SoperatorOnboardingAction(
                        id=ONBOARDING_ACTION_PLAN_COMPUTE_MIGRATION,
                        title="Plan rolling compute migration without terminating running jobs",
                        layer="role-mapping",
                        selected=True,
                        disruptive=True,
                        reason="NodeSet/layout changes need batch drain and validation phases.",
                    )
                )
        if comparison == "newer" or app_comparison == "newer":
            state = ONBOARDING_STATE_EXISTING_SOPERATOR_NEWER
            findings.append(
                SoperatorOnboardingFinding(
                    layer="versions",
                    status="newer-than-cxcli",
                    severity="blocked",
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
                    status="manual",
                    severity="blocked",
                    message="Soperator chart version could not be compared safely.",
                    evidence={"live_chart": live_chart, "pinned_chart": pinned_chart_version},
                )
            )
        if (
            state
            not in {
                ONBOARDING_STATE_EXISTING_SOPERATOR_SUPPORTED,
                ONBOARDING_STATE_EXISTING_SOPERATOR_NEWER,
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

    if soperator_release is None and has_soperator_crds:
        state = ONBOARDING_STATE_EXISTING_SOPERATOR_UNKNOWN
        findings.append(
            SoperatorOnboardingFinding(
                layer="versions",
                status="manual",
                severity="blocked",
                message=(
                    "Soperator CRDs were detected but no compatible Helm release version was found. "
                    "Manual review is required before cxcli can plan migration."
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
        actions=tuple(actions),
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
) -> dict[str, Any]:
    snapshot = target_snapshot_from_config(payload_or_config, target_ref=target_ref)
    app_row = soperator_onboarding_app_row(payload_or_config, target_ref=target_ref)
    if not pinned_chart_version and isinstance(app_row, Mapping):
        pinned_chart_version = str(app_row.get("version", "") or "").strip()
    report = analyze_soperator_onboarding_snapshot(
        snapshot,
        target_ref=target_ref,
        pinned_chart_version=pinned_chart_version,
        pinned_app_version=pinned_app_version,
    ).to_dict()
    report["accepted_fingerprint"] = soperator_onboarding_fingerprint(
        payload_or_config,
        target_ref=target_ref,
    )
    return report


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
    path = target_dir / SOURCE_SOPERATOR_DISCOVERY_REPORT_NAME
    report_payload = report.to_dict() if isinstance(report, SoperatorOnboardingReport) else dict(report)
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
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
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
    namespace_names = [
        str(item.get("metadata", {}).get("name", "")).strip()
        for item in namespaces.get("items", []) if isinstance(item, Mapping)
    ] if isinstance(namespaces, Mapping) else []
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
    crd_names = [
        str(item.get("metadata", {}).get("name", "")).strip()
        for item in crds.get("items", []) if isinstance(item, Mapping)
    ] if isinstance(crds, Mapping) else []
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
    helm_releases = _helm_json(
        ["helm", "--kube-context", context, "list", "-A", "-o", "json"],
        timeout,
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
            "nebius.com/node-group",
            "yandex.cloud/node-group-id",
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
                } if selector_key and selector_value else {},
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
        taints = item.get("spec", {}).get("taints", []) if isinstance(item.get("spec"), Mapping) else []
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
                "labels": metadata.get("labels") if isinstance(metadata.get("labels"), Mapping) else {},
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
                "selector": spec.get("selector") if isinstance(spec.get("selector"), Mapping) else {},
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
