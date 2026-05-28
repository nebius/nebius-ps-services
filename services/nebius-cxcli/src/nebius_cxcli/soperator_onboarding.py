"""Read-only Soperator onboarding analysis helpers."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .component_instances import normalize_component_token
from .runtime_config import to_plain_data

ONBOARDING_SCHEMA = "nebius-cxcli-soperator-onboarding/v1"
ONBOARDING_REPORT_DIR = "reports"
ONBOARDING_ACTION_INSTALL_SOPERATOR = "install-soperator"
ONBOARDING_ACTION_ADOPT_SOPERATOR = "adopt-soperator"
ONBOARDING_ACTION_UPGRADE_SOPERATOR = "upgrade-soperator"
ONBOARDING_ACTION_CONFIGURE_STORAGE = "configure-soperator-storage"
ONBOARDING_ACTION_ENABLE_TOPOLOGY = "enable-slurm-topology"
ONBOARDING_ACTION_REVIEW_GPU_RDMA = "review-gpu-rdma"
ONBOARDING_REQUIRED_STORAGE_KEYS = ("jail", "controller-spool", "accounting")
ONBOARDING_ACCEPTABLE_STATES = frozenset({"vanilla-mk8s", "existing-soperator"})
SOPERATOR_CRD_RESOURCE_KINDS = (
    ("slurmclusters.slurm.nebius.ai", "slurmclusters"),
    ("nodeconfigurators.slurm.nebius.ai", "nodeconfigurators"),
    ("nodesets.slurm.nebius.ai", "nodesets"),
)
SOPERATOR_CRD_NAMES = frozenset(name for name, _resource_kind in SOPERATOR_CRD_RESOURCE_KINDS)
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
class SoperatorOnboardingReport:
    schema: str
    target_ref: str
    analyzed_at: str
    state: str
    fingerprint: str
    findings: tuple[SoperatorOnboardingFinding, ...]
    actions: tuple[SoperatorOnboardingAction, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "target_ref": self.target_ref,
            "analyzed_at": self.analyzed_at,
            "state": self.state,
            "fingerprint": self.fingerprint,
            "findings": [asdict(item) for item in self.findings],
            "actions": [asdict(item) for item in self.actions],
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
                "helm_releases": _stable_helm_releases(onboarding.get("helm_releases", [])),
                "crds": list(onboarding.get("crds", []) or []),
                "collection_errors": list(onboarding.get("collection_errors", []) or []),
            },
        },
        "soperator": {
            "install_mode": str((app_row or {}).get("install_mode", "") or "").strip(),
            "profile": str((app_row or {}).get("profile", "") or "").strip(),
            "repo": str((app_row or {}).get("repo", "") or "").strip(),
            "version": str((app_row or {}).get("version", "") or "").strip(),
            "namespace": str((app_row or {}).get("namespace", "") or "").strip(),
            "release-name": str((app_row or {}).get("release-name", "") or "").strip(),
            "values": (app_row or {}).get("values", {}),
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
    return "equal"


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
                    "Select create-sfs or provide existing PVC/StorageClass values."
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
        state = "partial-soperator"
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
        state = "vanilla-mk8s"
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
                reason="Required for a vanilla MK8s target.",
            )
        )
    else:
        state = "existing-soperator" if soperator_release is not None else "partial-soperator"
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
        actions.append(
            SoperatorOnboardingAction(
                id=ONBOARDING_ACTION_ADOPT_SOPERATOR,
                title="Adopt compatible existing Soperator release",
                layer="soperator",
                selected=True,
                reason="Existing resources must be adopted cautiously before cxcli manages them.",
            )
        )

    if isinstance(soperator_release, Mapping):
        live_chart = _release_chart_version(soperator_release)
        live_app = str(soperator_release.get("app_version", "") or "").strip()
        comparison = compare_chart_versions(live_chart, pinned_chart_version)
        app_comparison = compare_chart_versions(live_app, pinned_app_version)
        if comparison == "older" or app_comparison == "older":
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
            actions.append(
                SoperatorOnboardingAction(
                    id=ONBOARDING_ACTION_UPGRADE_SOPERATOR,
                    title="Upgrade Soperator to the cxcli-pinned version",
                    layer="versions",
                    selected=True,
                    reason="Upgrades are allowed when live version is older.",
                )
            )
        if comparison == "newer" or app_comparison == "newer":
            findings.append(
                SoperatorOnboardingFinding(
                    layer="versions",
                    status="newer-than-cxcli",
                    severity="warning",
                    message=(
                        "Existing Soperator version is newer than the cxcli-pinned version; "
                        "cxcli will not downgrade it and defaults to adopt/audit only."
                    ),
                    evidence={"live_chart": live_chart, "live_app": live_app},
                )
            )
        if comparison == "unknown" and (live_chart or pinned_chart_version):
            findings.append(
                SoperatorOnboardingFinding(
                    layer="versions",
                    status="manual",
                    severity="blocked",
                    message="Soperator chart version could not be compared safely.",
                    evidence={"live_chart": live_chart, "pinned_chart": pinned_chart_version},
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
    )


def target_snapshot_from_config(
    payload_or_config: Any,
    *,
    target_ref: str,
) -> dict[str, Any]:
    target = soperator_onboarding_target(payload_or_config, target_ref=target_ref)
    node_groups = _node_group_inventory_from_target(target)
    onboarding = target.get("soperator_onboarding") if isinstance(target, Mapping) else {}
    helm_releases = onboarding.get("helm_releases") if isinstance(onboarding, Mapping) else ()
    crds = onboarding.get("crds") if isinstance(onboarding, Mapping) else ()
    namespaces = onboarding.get("namespaces") if isinstance(onboarding, Mapping) else ()
    collection_errors = (
        onboarding.get("collection_errors") if isinstance(onboarding, Mapping) else ()
    )
    storage = onboarding.get("storage") if isinstance(onboarding, Mapping) else {}
    return {
        "node_groups": dict(node_groups),
        "helm_releases": list(helm_releases) if isinstance(helm_releases, list) else [],
        "crds": list(crds) if isinstance(crds, list) else [],
        "namespaces": list(namespaces) if isinstance(namespaces, list) else [],
        "collection_errors": list(collection_errors) if isinstance(collection_errors, list) else [],
        "storage": dict(storage) if isinstance(storage, Mapping) else {},
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


def collect_kubectl_soperator_snapshot(
    *,
    kube_context: str,
    timeout: int = 30,
) -> dict[str, Any]:
    context = str(kube_context or "").strip()
    if not context:
        return {"node_groups": {}, "helm_releases": [], "crds": []}
    collection_errors: list[dict[str, Any]] = []
    nodes = _kubectl_json(
        ["kubectl", "--context", context, "get", "nodes", "-o", "json"],
        timeout,
        errors=collection_errors,
    )
    crds = _kubectl_json(
        ["kubectl", "--context", context, "get", "crd", "-o", "json"],
        timeout,
        errors=collection_errors,
    )
    namespaces = _kubectl_json(
        ["kubectl", "--context", context, "get", "namespace", "-o", "json"],
        timeout,
        errors=collection_errors,
    )
    pvs = _kubectl_json(
        ["kubectl", "--context", context, "get", "pv", "-o", "json"],
        timeout,
        errors=collection_errors,
    )
    pvcs = _kubectl_json(
        ["kubectl", "--context", context, "get", "pvc", "-A", "-o", "json"],
        timeout,
        errors=collection_errors,
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
        )
    helm_releases = _helm_json(
        ["helm", "--kube-context", context, "list", "-A", "-o", "json"],
        timeout,
        errors=collection_errors,
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
    namespace_names = [
        str(item.get("metadata", {}).get("name", "")).strip()
        for item in namespaces.get("items", []) if isinstance(item, Mapping)
    ] if isinstance(namespaces, Mapping) else []
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
        "collection_errors": collection_errors,
    }


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
) -> Mapping[str, Any]:
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
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
) -> Any:
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
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
