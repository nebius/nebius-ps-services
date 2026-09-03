"""Durable parent orchestration for end-to-end Soperator upgrades.

The release reconciler and MK8s executors remain the owners of their individual
mutations.  This module freezes their shared intent, sequences those
command-neutral operations, and records which irreversible frontier has been
crossed so a retry can only continue the same campaign.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from .mk8s_upgrade import (
    DISRUPTION_POLICIES,
    DISRUPTION_POLICY_SAFE,
    minor_version_hops,
    node_group_node_template_desired_state_matches,
    node_group_node_template_rollout_complete,
    parse_k8s_version,
)
from .operation_config_authority import (
    ConfigGenerationTransition,
    config_transition_from_payload,
    upsert_config_transition,
    validate_config_transition_chain,
)
from .soperator_failures import SoperatorSafetyPauseError
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json

SOPERATOR_UPGRADE_CAMPAIGN_SCHEMA = "nebius-cxcli.soperator-upgrade-campaign.v3"
SOPERATOR_UPGRADE_CAMPAIGN_RECEIPT_SCHEMA = "nebius-cxcli.soperator-upgrade-campaign-receipt.v3"

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_SEGMENT_STATUS = frozenset({"pending", "running", "complete", "failed"})
_CAMPAIGN_STATUS = frozenset({"active", "complete"})
_JOB_POLICIES = frozenset(
    {
        "interactive",
        "wait-to-finish",
        "wait-then-cancel",
        "cancel-selected",
        "cancel-all",
        "requeue-selected",
        "requeue-all",
        "requeue-hold-selected",
        "requeue-hold-all",
    }
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _string_tuple(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"Soperator upgrade campaign {label} must be a list")
    return tuple(str(item) for item in value)


def _mapping_sequence(value: object, *, label: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"Soperator upgrade campaign {label} must be a list")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError(f"Soperator upgrade campaign {label} contains a non-object item")
    return tuple(value)


def _minor(value: str) -> str:
    return parse_k8s_version(value).minor_text


def reachable_kubernetes_versions(
    *,
    current_version: str,
    supported_versions: Sequence[str],
) -> tuple[str, ...]:
    """Return the contiguous same-major provider path above the live version."""

    current = parse_k8s_version(current_version)
    supported = {
        parsed.minor_text
        for raw in supported_versions
        for parsed in (parse_k8s_version(raw),)
        if parsed.major == current.major and parsed.minor >= current.minor
    }
    reachable: list[str] = []
    candidate_minor = current.minor + 1
    while f"{current.major}.{candidate_minor}" in supported:
        reachable.append(f"{current.major}.{candidate_minor}")
        candidate_minor += 1
    return tuple(reachable)


def resolve_kubernetes_upgrade_path(
    *,
    selector: str,
    current_version: str,
    supported_versions: Sequence[str],
) -> tuple[str, tuple[str, ...]]:
    """Resolve ``latest`` or an exact endpoint to frozen sequential minor hops."""

    current = _minor(current_version)
    normalized = str(selector or "latest").strip().lower()
    reachable = reachable_kubernetes_versions(
        current_version=current,
        supported_versions=supported_versions,
    )
    if normalized == "latest":
        target = reachable[-1] if reachable else current
    else:
        target = _minor(normalized)
        if target != current and target not in reachable:
            available = ", ".join((current, *reachable))
            raise ValueError(
                f"Kubernetes {target} is not a reachable provider-supported endpoint "
                f"from {current}. Reachable endpoints: {available}."
            )
    hops = tuple(hop.to_version for hop in minor_version_hops(current, target))
    if hops and hops != reachable[: len(hops)]:
        raise ValueError(
            "The provider version list does not contain every sequential Kubernetes "
            f"minor required for {current} -> {target}."
        )
    return target, hops


def assert_campaign_node_group_inventory(
    intent: SoperatorUpgradeCampaignIntent,
    observed_provider_ids: Sequence[str],
) -> None:
    """Require the live provider inventory to equal the frozen in-place targets."""

    observed = tuple(str(value).strip() for value in observed_provider_ids)
    frozen = tuple(group.provider_id for group in intent.node_groups)
    if (
        any(not value for value in observed)
        or len(observed) != len(set(observed))
        or set(observed) != set(frozen)
    ):
        raise SoperatorSafetyPauseError(
            "live MK8s node-group inventory differs from the frozen Soperator campaign",
            code="node-group-inventory-drift",
        )


@dataclass(frozen=True)
class FrozenCompatibilityRow:
    """Provider compatibility evidence frozen for one platform and hop."""

    group_key: str
    kubernetes_version: str
    platform: str
    os: str
    drivers_preset: str


@dataclass(frozen=True)
class FrozenNodeGroupTarget:
    """One node group's immutable host-runtime target."""

    key: str
    provider_name: str
    provider_id: str
    platform: str
    source_version: str
    source_os: str
    source_drivers_preset: str
    target_version: str
    target_os: str
    target_drivers_preset: str
    gpu: bool
    zero_sized: bool = False


@dataclass(frozen=True)
class SoperatorUpgradeCampaignIntent:
    """Immutable full-stack intent shared by all child executors."""

    schema: str
    target_ref: str
    ownership: str
    backend: str
    backend_authority_sha256: str
    provider_api_authorized: bool
    source_config_sha256: str
    source_project_snapshot_sha256: str
    cluster_id: str
    kubernetes_uid: str
    requested_release_selector: str
    source_release: str
    target_release: str
    target_jail_cuda_version: str
    requested_kubernetes_selector: str
    source_kubernetes_version: str
    target_kubernetes_version: str
    kubernetes_hops: tuple[str, ...]
    supported_kubernetes_versions: tuple[str, ...]
    target_os: str
    target_gpu_stack_preset: str
    node_group_strategy: str
    strategy_max_surge_count: int | None
    drain_timeout: str
    zero_size_gpu_validation: str
    job_policy: str
    cancel_job_ids: tuple[str, ...]
    requeue_job_ids: tuple[str, ...]
    job_wait_timeout: str
    job_refresh_interval: str
    node_groups: tuple[FrozenNodeGroupTarget, ...]
    compatibility_rows: tuple[FrozenCompatibilityRow, ...]

    @property
    def digest(self) -> str:
        return _sha256(asdict(self))

    @property
    def compatibility_versions(self) -> tuple[str, ...]:
        versions = list(self.kubernetes_hops or (self.target_kubernetes_version,))
        if any(
            group.source_version != self.source_kubernetes_version for group in self.node_groups
        ):
            versions.insert(0, self.source_kubernetes_version)
        return tuple(dict.fromkeys(versions))

    @property
    def segments(self) -> tuple[str, ...]:
        segments = ["soperator-release"]
        if any(
            group.source_version != self.source_kubernetes_version for group in self.node_groups
        ):
            segments.extend(
                (
                    f"node-templates:{self.source_kubernetes_version}",
                    f"runtime-readiness:{self.source_kubernetes_version}",
                )
            )
        for hop in self.kubernetes_hops:
            segments.extend(
                (
                    f"mk8s-hop:{hop}",
                    f"runtime-readiness:{hop}",
                )
            )
        if not self.kubernetes_hops and any(
            group.source_os != group.target_os
            or group.source_drivers_preset != group.target_drivers_preset
            for group in self.node_groups
        ):
            endpoint = self.target_kubernetes_version
            segments.extend((f"node-templates:{endpoint}", f"runtime-readiness:{endpoint}"))
        segments.append("final-readiness")
        return tuple(segments)


def assert_frozen_compatibility_row_supported(
    *,
    group: FrozenNodeGroupTarget,
    row: FrozenCompatibilityRow,
    choices: Sequence[Any],
) -> None:
    """Fail closed when the provider withdraws one frozen group/hop tuple."""

    if not any(
        (
            not str(getattr(choice, "platform", None) or "").strip()
            or str(getattr(choice, "platform", None) or "").strip() == row.platform
        )
        and str(getattr(choice, "os", None) or "").strip() == row.os
        and str(getattr(choice, "drivers_preset", None) or "").strip() == row.drivers_preset
        for choice in choices
    ):
        raise SoperatorSafetyPauseError(
            "Nebius provider compatibility no longer contains the frozen tuple for "
            f"node group {group.key!r} at Kubernetes {row.kubernetes_version}: OS "
            f"{row.os}, Nebius drivers preset "
            f"{row.drivers_preset or 'driverless/operator-managed'}",
            code="provider-compatibility-drift",
        )


def apply_frozen_node_group_rows(
    *,
    node_groups: Sequence[FrozenNodeGroupTarget],
    rows: Mapping[str, FrozenCompatibilityRow],
    compatibility_lookup: Callable[[FrozenNodeGroupTarget, FrozenCompatibilityRow], Sequence[Any]],
    apply_group: Callable[[FrozenNodeGroupTarget, FrozenCompatibilityRow], None],
) -> None:
    """Revalidate each frozen tuple immediately before its group mutation callback."""

    for group in node_groups:
        row = rows[group.key]
        assert_frozen_compatibility_row_supported(
            group=group,
            row=row,
            choices=compatibility_lookup(group, row),
        )
        apply_group(group, row)


def final_node_group_capacity_snapshot(
    *,
    intent: SoperatorUpgradeCampaignIntent,
    live_node_groups: Sequence[Any],
) -> dict[str, dict[str, object]]:
    """Freeze one ready all-group provider snapshot for final runtime validation."""

    assert_campaign_node_group_inventory(
        intent,
        tuple(
            str(getattr(getattr(group, "metadata", None), "id", None) or "").strip()
            for group in live_node_groups
        ),
    )
    live_by_id = {
        str(getattr(getattr(group, "metadata", None), "id", None) or "").strip(): group
        for group in live_node_groups
    }
    rows = {
        row.group_key: row
        for row in intent.compatibility_rows
        if row.kubernetes_version == intent.target_kubernetes_version
    }
    snapshot: dict[str, dict[str, object]] = {}
    for group in intent.node_groups:
        candidate = live_by_id[group.provider_id]
        metadata = getattr(candidate, "metadata", None)
        status = getattr(candidate, "status", None)
        resource_version = getattr(metadata, "resource_version", None)
        raw_counts = (
            getattr(status, "target_node_count", None),
            getattr(status, "ready_node_count", None),
            getattr(status, "node_count", None),
            getattr(status, "outdated_node_count", None),
        )
        if resource_version in {None, "", 0} or any(
            not isinstance(value, int) or isinstance(value, bool) for value in raw_counts
        ):
            raise RuntimeError(
                f"Final readiness could not freeze provider capacity identity for node "
                f"group {group.key!r}."
            )
        target_count, ready_count, node_count, outdated_count = (
            cast(int, value) for value in raw_counts
        )
        row = rows[group.key]
        expected_drivers = row.drivers_preset if group.gpu else None
        if target_count == 0:
            capacity_mode = "zero-capacity"
            complete = bool(
                ready_count == 0
                and node_count == 0
                and outdated_count == 0
                and not bool(getattr(status, "reconciling", False))
                and node_group_node_template_desired_state_matches(
                    candidate,
                    version=intent.target_kubernetes_version,
                    os=row.os,
                    drivers_preset=expected_drivers,
                )
            )
        elif target_count > 0:
            capacity_mode = "ready-capacity"
            complete = node_group_node_template_rollout_complete(
                candidate,
                version=intent.target_kubernetes_version,
                os=row.os,
                drivers_preset=expected_drivers,
            )
        else:
            capacity_mode = "invalid"
            complete = False
        if not complete:
            raise RuntimeError(
                f"Final readiness observed node group {group.key!r} outside its frozen "
                "ready or zero-capacity state; retrying the same campaign segment."
            )
        snapshot[group.key] = {
            "resourceVersion": str(resource_version),
            "capacityMode": capacity_mode,
            "targetNodeCount": target_count,
            "readyNodeCount": ready_count,
            "nodeCount": node_count,
            "outdatedNodeCount": outdated_count,
        }
    return snapshot


def assert_final_capacity_snapshot_unchanged(
    before: Mapping[str, Mapping[str, object]],
    after: Mapping[str, Mapping[str, object]],
) -> None:
    """Retry final readiness when capacity or provider identity changed during validation."""

    if after != before:
        raise RuntimeError(
            "MK8s node-group capacity or resource identity changed during Soperator final "
            "GPU validation; retrying final readiness against one fresh all-group snapshot."
        )


def run_final_runtime_validation_boundary(
    *,
    refresh_sources: Callable[[], Any],
    prove_release_graph: Callable[[], Any],
    freeze_capacity: Callable[[], Mapping[str, Mapping[str, object]]],
    validate_runtime: Callable[[Mapping[str, Mapping[str, object]]], Any],
) -> tuple[Any, Any, Mapping[str, Mapping[str, object]]]:
    """Order source refresh, graph proof, runtime proof, and final identity proof."""

    source_evidence = refresh_sources()
    if prove_release_graph() is None:
        raise RuntimeError("Soperator final readiness lost its rendered release graph")
    before = freeze_capacity()
    runtime_evidence = validate_runtime(before)
    if prove_release_graph() is None:
        raise RuntimeError(
            "Soperator final readiness lost its rendered release graph after runtime validation"
        )
    after = freeze_capacity()
    assert_final_capacity_snapshot_unchanged(before, after)
    return source_evidence, runtime_evidence, after


def build_campaign_intent(
    *,
    target_ref: str,
    ownership: str,
    backend: str,
    backend_authority_sha256: str,
    provider_api_authorized: bool,
    source_config_sha256: str,
    source_project_snapshot_sha256: str,
    cluster_id: str,
    kubernetes_uid: str,
    requested_release_selector: str,
    source_release: str,
    target_release: str,
    target_jail_cuda_version: str,
    requested_kubernetes_selector: str,
    source_kubernetes_version: str,
    supported_kubernetes_versions: Sequence[str],
    target_os: str,
    target_gpu_stack_preset: str,
    node_group_strategy: str,
    strategy_max_surge_count: int | None,
    drain_timeout: str,
    zero_size_gpu_validation: str,
    job_policy: str,
    cancel_job_ids: Sequence[str],
    requeue_job_ids: Sequence[str],
    job_wait_timeout: str,
    job_refresh_interval: str,
    node_groups: Sequence[FrozenNodeGroupTarget],
    compatibility_rows: Sequence[FrozenCompatibilityRow],
) -> SoperatorUpgradeCampaignIntent:
    target_version, hops = resolve_kubernetes_upgrade_path(
        selector=requested_kubernetes_selector,
        current_version=source_kubernetes_version,
        supported_versions=supported_kubernetes_versions,
    )
    intent = SoperatorUpgradeCampaignIntent(
        schema=SOPERATOR_UPGRADE_CAMPAIGN_SCHEMA,
        target_ref=str(target_ref).strip(),
        ownership=str(ownership).strip().lower(),
        backend=str(backend).strip().lower(),
        backend_authority_sha256=str(backend_authority_sha256).strip(),
        provider_api_authorized=bool(provider_api_authorized),
        source_config_sha256=str(source_config_sha256).strip(),
        source_project_snapshot_sha256=str(source_project_snapshot_sha256).strip(),
        cluster_id=str(cluster_id).strip(),
        kubernetes_uid=str(kubernetes_uid).strip(),
        requested_release_selector=str(requested_release_selector).strip(),
        source_release=str(source_release).strip(),
        target_release=str(target_release).strip(),
        target_jail_cuda_version=str(target_jail_cuda_version).strip(),
        requested_kubernetes_selector=str(requested_kubernetes_selector).strip().lower(),
        source_kubernetes_version=_minor(source_kubernetes_version),
        target_kubernetes_version=target_version,
        kubernetes_hops=hops,
        supported_kubernetes_versions=tuple(
            sorted(
                {_minor(value) for value in supported_kubernetes_versions},
                key=lambda value: (
                    parse_k8s_version(value).major,
                    parse_k8s_version(value).minor,
                ),
            )
        ),
        target_os=str(target_os).strip(),
        target_gpu_stack_preset=str(target_gpu_stack_preset).strip(),
        node_group_strategy=str(node_group_strategy).strip(),
        strategy_max_surge_count=strategy_max_surge_count,
        drain_timeout=str(drain_timeout).strip(),
        zero_size_gpu_validation=str(zero_size_gpu_validation).strip(),
        job_policy=str(job_policy).strip(),
        cancel_job_ids=tuple(sorted(set(cancel_job_ids))),
        requeue_job_ids=tuple(sorted(set(requeue_job_ids))),
        job_wait_timeout=str(job_wait_timeout).strip(),
        job_refresh_interval=str(job_refresh_interval).strip(),
        node_groups=tuple(node_groups),
        compatibility_rows=tuple(compatibility_rows),
    )
    validate_campaign_intent(intent)
    return intent


def validate_campaign_intent(intent: SoperatorUpgradeCampaignIntent) -> None:
    if intent.schema != SOPERATOR_UPGRADE_CAMPAIGN_SCHEMA:
        raise ValueError("Soperator upgrade campaign intent has an unsupported schema")
    if intent.ownership not in {"managed", "onboarded"}:
        raise ValueError("Soperator upgrade campaign ownership is invalid")
    if intent.backend not in {"terraform", "provider-api"}:
        raise ValueError("Soperator upgrade campaign backend is invalid")
    if intent.ownership == "managed" and intent.backend != "terraform":
        raise ValueError("managed Soperator upgrades require the Terraform backend")
    if intent.ownership == "onboarded" and intent.backend != "provider-api":
        raise ValueError("onboarded Soperator upgrades require the provider API backend")
    if intent.ownership == "managed" and intent.provider_api_authorized:
        raise ValueError("managed Soperator upgrades cannot carry provider API authority")
    if not _SHA256.fullmatch(intent.backend_authority_sha256):
        raise ValueError("Soperator upgrade campaign backend authority digest is invalid")
    if not _SHA256.fullmatch(intent.source_config_sha256):
        raise ValueError("Soperator upgrade campaign source config digest is invalid")
    if not _SHA256.fullmatch(intent.source_project_snapshot_sha256):
        raise ValueError("Soperator upgrade campaign source project snapshot digest is invalid")
    for label, value in (
        ("target", intent.target_ref),
        ("cluster id", intent.cluster_id),
        ("Kubernetes uid", intent.kubernetes_uid),
        ("requested release selector", intent.requested_release_selector),
        ("source release", intent.source_release),
        ("target release", intent.target_release),
        ("target Jail CUDA version", intent.target_jail_cuda_version),
        ("requested Kubernetes selector", intent.requested_kubernetes_selector),
        ("target OS", intent.target_os),
        ("node-group strategy", intent.node_group_strategy),
        ("drain timeout", intent.drain_timeout),
        ("job policy", intent.job_policy),
        ("job wait timeout", intent.job_wait_timeout),
        ("job refresh interval", intent.job_refresh_interval),
    ):
        if not value:
            raise ValueError(f"Soperator upgrade campaign requires {label}")
    if intent.node_group_strategy not in DISRUPTION_POLICIES:
        raise ValueError("Soperator upgrade campaign node-group strategy is invalid")
    if intent.node_group_strategy == DISRUPTION_POLICY_SAFE:
        if (
            isinstance(intent.strategy_max_surge_count, bool)
            or not isinstance(intent.strategy_max_surge_count, int)
            or intent.strategy_max_surge_count <= 0
        ):
            raise ValueError("safe-surge campaigns require a positive max-surge count")
    elif intent.strategy_max_surge_count not in {None, 0}:
        raise ValueError("only safe-surge campaigns may request max-surge capacity")
    if intent.job_policy not in _JOB_POLICIES:
        raise ValueError("Soperator upgrade campaign job policy is invalid")
    expected_target, expected_hops = resolve_kubernetes_upgrade_path(
        selector=intent.requested_kubernetes_selector,
        current_version=intent.source_kubernetes_version,
        supported_versions=intent.supported_kubernetes_versions,
    )
    if (
        expected_target != intent.target_kubernetes_version
        or expected_hops != intent.kubernetes_hops
    ):
        raise ValueError("Soperator upgrade campaign Kubernetes path is inconsistent")
    group_keys = [group.key for group in intent.node_groups]
    provider_ids = [group.provider_id for group in intent.node_groups]
    if len(group_keys) != len(set(group_keys)) or len(provider_ids) != len(set(provider_ids)):
        raise ValueError("Soperator upgrade campaign repeats a node-group identity")
    if any(
        group.target_version != intent.target_kubernetes_version for group in intent.node_groups
    ):
        raise ValueError("Soperator upgrade campaign node-group endpoint is inconsistent")
    if not intent.node_groups:
        raise ValueError("Soperator upgrade campaign requires at least one node group")
    source_cluster_version = parse_k8s_version(intent.source_kubernetes_version)
    for group in intent.node_groups:
        for label, value in (
            ("key", group.key),
            ("provider name", group.provider_name),
            ("provider id", group.provider_id),
            ("platform", group.platform),
            ("source OS", group.source_os),
            ("target OS", group.target_os),
        ):
            if not str(value).strip():
                raise ValueError(f"Soperator upgrade campaign node group requires {label}")
        source_group_version = parse_k8s_version(group.source_version)
        if (
            source_group_version.major != source_cluster_version.major
            or source_group_version.minor > source_cluster_version.minor
            or source_group_version.minor < source_cluster_version.minor - 1
        ):
            raise ValueError(
                "Soperator upgrade campaign requires each node group at the control-plane "
                "minor or exactly one minor behind"
            )
    expected_rows = {
        (group.key, version)
        for group in intent.node_groups
        for version in intent.compatibility_versions
    }
    actual_rows = {(row.group_key, row.kubernetes_version) for row in intent.compatibility_rows}
    if actual_rows != expected_rows or len(actual_rows) != len(intent.compatibility_rows):
        raise ValueError("Soperator upgrade campaign compatibility rows are incomplete")
    final_rows = {
        row.group_key: row
        for row in intent.compatibility_rows
        if row.kubernetes_version == intent.target_kubernetes_version
    }
    if any(
        final_rows[group.key].os != group.target_os
        or final_rows[group.key].drivers_preset != group.target_drivers_preset
        for group in intent.node_groups
    ):
        raise ValueError("Soperator upgrade campaign final compatibility row is inconsistent")
    if intent.zero_size_gpu_validation not in {"require-capacity", "skip-with-proof"}:
        raise ValueError("Soperator upgrade campaign zero-size GPU policy is invalid")


def campaign_intent_from_payload(payload: Mapping[str, Any]) -> SoperatorUpgradeCampaignIntent:
    """Rehydrate the exact frozen intent embedded in a durable receipt."""

    try:
        intent = SoperatorUpgradeCampaignIntent(
            schema=str(payload.get("schema", "")),
            target_ref=str(payload.get("target_ref", "")),
            ownership=str(payload.get("ownership", "")),
            backend=str(payload.get("backend", "")),
            backend_authority_sha256=str(payload.get("backend_authority_sha256", "")),
            provider_api_authorized=bool(payload.get("provider_api_authorized", False)),
            source_config_sha256=str(payload.get("source_config_sha256", "")),
            source_project_snapshot_sha256=str(payload.get("source_project_snapshot_sha256", "")),
            cluster_id=str(payload.get("cluster_id", "")),
            kubernetes_uid=str(payload.get("kubernetes_uid", "")),
            requested_release_selector=str(payload.get("requested_release_selector", "")),
            source_release=str(payload.get("source_release", "")),
            target_release=str(payload.get("target_release", "")),
            target_jail_cuda_version=str(payload.get("target_jail_cuda_version", "")),
            requested_kubernetes_selector=str(payload.get("requested_kubernetes_selector", "")),
            source_kubernetes_version=str(payload.get("source_kubernetes_version", "")),
            target_kubernetes_version=str(payload.get("target_kubernetes_version", "")),
            kubernetes_hops=_string_tuple(
                payload.get("kubernetes_hops", ()), label="Kubernetes hops"
            ),
            supported_kubernetes_versions=_string_tuple(
                payload.get("supported_kubernetes_versions", ()),
                label="supported Kubernetes versions",
            ),
            target_os=str(payload.get("target_os", "")),
            target_gpu_stack_preset=str(payload.get("target_gpu_stack_preset", "")),
            node_group_strategy=str(payload.get("node_group_strategy", "")),
            strategy_max_surge_count=(
                int(payload["strategy_max_surge_count"])
                if payload.get("strategy_max_surge_count") is not None
                else None
            ),
            drain_timeout=str(payload.get("drain_timeout", "")),
            zero_size_gpu_validation=str(payload.get("zero_size_gpu_validation", "")),
            job_policy=str(payload.get("job_policy", "")),
            cancel_job_ids=_string_tuple(payload.get("cancel_job_ids", ()), label="cancel job ids"),
            requeue_job_ids=_string_tuple(
                payload.get("requeue_job_ids", ()), label="requeue job ids"
            ),
            job_wait_timeout=str(payload.get("job_wait_timeout", "")),
            job_refresh_interval=str(payload.get("job_refresh_interval", "")),
            node_groups=tuple(
                FrozenNodeGroupTarget(**dict(item))
                for item in _mapping_sequence(payload.get("node_groups", ()), label="node groups")
            ),
            compatibility_rows=tuple(
                FrozenCompatibilityRow(**dict(item))
                for item in _mapping_sequence(
                    payload.get("compatibility_rows", ()),
                    label="compatibility rows",
                )
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Soperator upgrade campaign embedded intent is invalid") from exc
    validate_campaign_intent(intent)
    return intent


@dataclass(frozen=True)
class CampaignSegmentResult:
    """Evidence returned by one child executor after authoritative verification."""

    evidence: Mapping[str, Any]
    irreversible_frontier: str = ""


@dataclass(frozen=True)
class CampaignSegmentReceipt:
    name: str
    status: str
    attempts: int = 0
    irreversible_frontier: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)
    evidence_sha256: str = ""
    started_at: str = ""
    completed_at: str = ""
    failure_type: str = ""


@dataclass(frozen=True)
class SoperatorUpgradeCampaignReceipt:
    schema: str
    status: str
    intent_sha256: str
    intent: Mapping[str, Any]
    target_ref: str
    cluster_id: str
    kubernetes_uid: str
    maintenance: str
    maintenance_evidence: Mapping[str, Any]
    supervisor: Mapping[str, Any]
    config_generations: tuple[ConfigGenerationTransition, ...]
    segments: tuple[CampaignSegmentReceipt, ...]
    created_at: str
    updated_at: str


def campaign_receipt_path(project_dir: Path, *, target_ref: str) -> Path:
    token = re.sub(r"[^A-Za-z0-9.-]+", "-", str(target_ref).strip()).strip("-.")
    if not token or token in {".", ".."}:
        raise ValueError("Soperator upgrade target does not produce a safe campaign path")
    return Path(project_dir) / ".nebius-cxcli" / "soperator-upgrades" / token / "campaign.json"


def _new_receipt(intent: SoperatorUpgradeCampaignIntent) -> SoperatorUpgradeCampaignReceipt:
    now = _utc_now()
    return SoperatorUpgradeCampaignReceipt(
        schema=SOPERATOR_UPGRADE_CAMPAIGN_RECEIPT_SCHEMA,
        status="active",
        intent_sha256=intent.digest,
        intent=asdict(intent),
        target_ref=intent.target_ref,
        cluster_id=intent.cluster_id,
        kubernetes_uid=intent.kubernetes_uid,
        maintenance="pending",
        maintenance_evidence={},
        supervisor={
            "state": "pending",
            "attempt": 0,
            "disposition": "",
            "current_segment": "",
            "maintenance_state": "pending",
            "failure_type": "",
            "updated_at": now,
        },
        config_generations=(),
        segments=tuple(
            CampaignSegmentReceipt(name=name, status="pending", evidence={})
            for name in intent.segments
        ),
        created_at=now,
        updated_at=now,
    )


def _receipt_payload(receipt: SoperatorUpgradeCampaignReceipt) -> dict[str, Any]:
    return asdict(receipt)


def _receipt_from_payload(payload: object) -> SoperatorUpgradeCampaignReceipt:
    if not isinstance(payload, Mapping):
        raise RuntimeError("Soperator upgrade campaign receipt must be an object")
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list):
        raise RuntimeError("Soperator upgrade campaign receipt has no segment ledger")
    try:
        segments = tuple(
            CampaignSegmentReceipt(
                name=str(item["name"]),
                status=str(item["status"]),
                attempts=int(item.get("attempts", 0)),
                irreversible_frontier=str(item.get("irreversible_frontier", "")),
                evidence=(
                    dict(item.get("evidence", {}))
                    if isinstance(item.get("evidence"), Mapping)
                    else {}
                ),
                evidence_sha256=str(item.get("evidence_sha256", "")),
                started_at=str(item.get("started_at", "")),
                completed_at=str(item.get("completed_at", "")),
                failure_type=str(item.get("failure_type", "")),
            )
            for item in raw_segments
            if isinstance(item, Mapping)
        )
        receipt = SoperatorUpgradeCampaignReceipt(
            schema=str(payload.get("schema", "")),
            status=str(payload.get("status", "")),
            intent_sha256=str(payload.get("intent_sha256", "")),
            intent=(
                dict(payload.get("intent", {}))
                if isinstance(payload.get("intent"), Mapping)
                else {}
            ),
            target_ref=str(payload.get("target_ref", "")),
            cluster_id=str(payload.get("cluster_id", "")),
            kubernetes_uid=str(payload.get("kubernetes_uid", "")),
            maintenance=str(payload.get("maintenance", "")),
            maintenance_evidence=(
                dict(payload.get("maintenance_evidence", {}))
                if isinstance(payload.get("maintenance_evidence"), Mapping)
                else {}
            ),
            supervisor=(
                dict(payload.get("supervisor", {}))
                if isinstance(payload.get("supervisor"), Mapping)
                else {}
            ),
            config_generations=tuple(
                config_transition_from_payload(item)
                for item in _mapping_sequence(
                    payload.get("config_generations", ()),
                    label="config generations",
                )
            ),
            segments=segments,
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Soperator upgrade campaign receipt is invalid") from exc
    if receipt.schema != SOPERATOR_UPGRADE_CAMPAIGN_RECEIPT_SCHEMA:
        raise RuntimeError("Soperator upgrade campaign receipt has an unsupported schema")
    if receipt.status not in _CAMPAIGN_STATUS or receipt.maintenance not in {
        "pending",
        "entering",
        "active",
        "restoring",
        "restored",
    }:
        raise RuntimeError("Soperator upgrade campaign receipt has an invalid state")
    if not _SHA256.fullmatch(receipt.intent_sha256):
        raise RuntimeError("Soperator upgrade campaign receipt has an invalid intent digest")
    if _sha256(receipt.intent) != receipt.intent_sha256:
        raise RuntimeError("Soperator upgrade campaign receipt intent digest does not match")
    if len(segments) != len(raw_segments) or any(
        segment.status not in _SEGMENT_STATUS for segment in segments
    ):
        raise RuntimeError("Soperator upgrade campaign segment ledger is invalid")
    intent = campaign_intent_from_payload(receipt.intent)
    validate_config_transition_chain(
        receipt.config_generations,
        initial_config_sha256=intent.source_config_sha256,
    )
    for segment in segments:
        evidence = dict(segment.evidence or {})
        if segment.evidence_sha256 and _sha256(evidence) != segment.evidence_sha256:
            raise RuntimeError("Soperator upgrade campaign segment evidence digest does not match")
        if segment.status == "complete" and not segment.evidence_sha256:
            raise RuntimeError("completed Soperator upgrade segment has no evidence digest")
    required_supervisor = {
        "state",
        "attempt",
        "disposition",
        "current_segment",
        "maintenance_state",
        "failure_type",
        "updated_at",
    }
    if set(receipt.supervisor) != required_supervisor:
        raise RuntimeError("Soperator upgrade campaign supervisor state is invalid")
    return receipt


def load_campaign_receipt(path: Path) -> SoperatorUpgradeCampaignReceipt | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    try:
        payload = read_owner_only_json(path, label="Soperator upgrade campaign receipt")
    except FileNotFoundError:
        return None
    return _receipt_from_payload(payload)


def _write_receipt(path: Path, receipt: SoperatorUpgradeCampaignReceipt) -> None:
    write_owner_only_json(path, _receipt_payload(receipt))


@dataclass(frozen=True)
class CampaignConfigTransitionStore:
    """Bind generic config-generation recovery to one campaign receipt."""

    path: Path
    intent: SoperatorUpgradeCampaignIntent

    def get(self, stage: str) -> ConfigGenerationTransition | None:
        receipt = load_campaign_receipt(self.path)
        if receipt is None or receipt.intent_sha256 != self.intent.digest:
            raise RuntimeError("Soperator upgrade config authority receipt is unavailable")
        return next(
            (item for item in receipt.config_generations if item.stage == stage),
            None,
        )

    def record(self, transition: ConfigGenerationTransition) -> None:
        receipt = load_campaign_receipt(self.path)
        if receipt is None or receipt.intent_sha256 != self.intent.digest:
            raise RuntimeError("Soperator upgrade config authority receipt is unavailable")
        generations = upsert_config_transition(
            receipt.config_generations,
            transition,
            initial_config_sha256=self.intent.source_config_sha256,
        )
        _write_receipt(
            self.path,
            replace(receipt, config_generations=generations, updated_at=_utc_now()),
        )


@dataclass(frozen=True)
class CampaignControllerSpoolMigrationStore:
    """Persist protected controller-spool recovery under parent maintenance."""

    path: Path
    intent: SoperatorUpgradeCampaignIntent

    def _active_receipt(self) -> SoperatorUpgradeCampaignReceipt:
        receipt = load_campaign_receipt(self.path)
        if receipt is None or receipt.intent_sha256 != self.intent.digest:
            raise RuntimeError("Soperator upgrade spool migration authority is unavailable")
        if receipt.status != "active" or receipt.maintenance != "active":
            raise RuntimeError("Soperator upgrade spool migration requires active maintenance")
        return receipt

    def read(self) -> Mapping[str, object] | None:
        receipt = self._active_receipt()
        payload = receipt.maintenance_evidence.get("controllerSpoolMigration")
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise RuntimeError("Soperator upgrade spool migration checkpoint is invalid")
        return dict(payload)

    def write(self, payload: Mapping[str, object]) -> None:
        receipt = self._active_receipt()
        maintenance_evidence = dict(receipt.maintenance_evidence)
        maintenance_evidence["controllerSpoolMigration"] = dict(payload)
        _write_receipt(
            self.path,
            replace(
                receipt,
                maintenance_evidence=maintenance_evidence,
                updated_at=_utc_now(),
            ),
        )


def record_campaign_supervisor_state(
    *,
    path: Path,
    intent: SoperatorUpgradeCampaignIntent,
    state: str,
    attempt: int,
    disposition: str = "",
    current_segment: str = "",
    maintenance_state: str = "",
    failure_type: str = "",
) -> None:
    """Persist bounded parent-supervisor metadata without exception content."""

    receipt = load_campaign_receipt(path)
    if receipt is None or receipt.intent_sha256 != intent.digest:
        raise RuntimeError("Soperator upgrade supervisor receipt is unavailable")
    supervisor = {
        "state": str(state),
        "attempt": int(attempt),
        "disposition": str(disposition),
        "current_segment": str(current_segment),
        "maintenance_state": str(maintenance_state or receipt.maintenance),
        "failure_type": str(failure_type),
        "updated_at": _utc_now(),
    }
    _write_receipt(
        path,
        replace(receipt, supervisor=supervisor, updated_at=_utc_now()),
    )


def _replace_segment(
    receipt: SoperatorUpgradeCampaignReceipt,
    index: int,
    segment: CampaignSegmentReceipt,
) -> SoperatorUpgradeCampaignReceipt:
    segments = list(receipt.segments)
    segments[index] = segment
    return replace(receipt, segments=tuple(segments), updated_at=_utc_now())


def create_or_resume_campaign(
    *,
    path: Path,
    intent: SoperatorUpgradeCampaignIntent,
) -> SoperatorUpgradeCampaignReceipt:
    """Create one receipt or resume only the byte-equivalent frozen intent."""

    validate_campaign_intent(intent)
    receipt = load_campaign_receipt(path)
    if receipt is None:
        receipt = _new_receipt(intent)
        _write_receipt(path, receipt)
        return receipt
    if receipt.status == "complete" and receipt.intent_sha256 != intent.digest:
        history_path = path.with_name(
            "campaign-" + receipt.intent_sha256.removeprefix("sha256:")[:16] + ".json"
        )
        if not history_path.exists():
            _write_receipt(history_path, receipt)
        receipt = _new_receipt(intent)
        _write_receipt(path, receipt)
        return receipt
    if (
        receipt.intent_sha256 != intent.digest
        or receipt.target_ref != intent.target_ref
        or receipt.cluster_id != intent.cluster_id
        or receipt.kubernetes_uid != intent.kubernetes_uid
        or tuple(segment.name for segment in receipt.segments) != intent.segments
    ):
        raise RuntimeError(
            "recovery-required: the unfinished Soperator full-stack campaign belongs "
            "to different frozen intent"
        )
    return receipt


def run_campaign(
    *,
    path: Path,
    intent: SoperatorUpgradeCampaignIntent,
    segment_executors: Mapping[str, Callable[[], CampaignSegmentResult]],
    enter_maintenance: Callable[
        [Callable[[Mapping[str, Any]], None], Mapping[str, Any]],
        Mapping[str, Any],
    ],
    restore_maintenance: Callable[
        [Callable[[Mapping[str, Any]], None], Mapping[str, Any]],
        Mapping[str, Any],
    ],
    assert_fence: Callable[[], None],
) -> SoperatorUpgradeCampaignReceipt:
    """Run or resume child segments under one durable maintenance boundary."""

    receipt = create_or_resume_campaign(path=path, intent=intent)
    missing = [
        segment.name for segment in receipt.segments if segment.name not in segment_executors
    ]
    if missing:
        raise RuntimeError(
            "Soperator upgrade campaign has no executor for frozen segment(s): "
            + ", ".join(missing)
        )
    if receipt.status == "complete":
        final_index = len(receipt.segments) - 1
        final_segment = receipt.segments[final_index]
        assert_fence()
        started_at = _utc_now()
        receipt = replace(
            receipt,
            supervisor={
                **dict(receipt.supervisor),
                "state": "running",
                "disposition": "",
                "current_segment": final_segment.name,
                "maintenance_state": "restored",
                "failure_type": "",
                "updated_at": started_at,
            },
            updated_at=started_at,
        )
        _write_receipt(path, receipt)
        try:
            result = segment_executors[final_segment.name]()
            if not isinstance(result, CampaignSegmentResult):
                raise RuntimeError(
                    f"Soperator upgrade segment {final_segment.name} returned invalid evidence"
                )
            assert_fence()
        except Exception as exc:
            refreshed = load_campaign_receipt(path)
            if refreshed is None or refreshed.intent_sha256 != intent.digest:
                raise RuntimeError(
                    "Soperator campaign receipt disappeared during final revalidation"
                ) from exc
            failed_at = _utc_now()
            receipt = replace(
                refreshed,
                supervisor={
                    **dict(refreshed.supervisor),
                    "state": "retrying",
                    "disposition": "retrying",
                    "current_segment": final_segment.name,
                    "maintenance_state": "restored",
                    "failure_type": type(exc).__name__,
                    "updated_at": failed_at,
                },
                updated_at=failed_at,
            )
            _write_receipt(path, receipt)
            raise
        evidence = dict(result.evidence)
        final_segment = replace(
            final_segment,
            status="complete",
            attempts=final_segment.attempts + 1,
            irreversible_frontier=str(result.irreversible_frontier).strip(),
            evidence=evidence,
            evidence_sha256=_sha256(evidence),
            started_at=started_at,
            completed_at=_utc_now(),
            failure_type="",
        )
        receipt = replace(
            _replace_segment(receipt, final_index, final_segment),
            supervisor={
                **dict(receipt.supervisor),
                "state": "complete",
                "disposition": "complete",
                "current_segment": "",
                "maintenance_state": "restored",
                "failure_type": "",
                "updated_at": _utc_now(),
            },
            updated_at=_utc_now(),
        )
        _write_receipt(path, receipt)
        return receipt
    assert_fence()
    if receipt.maintenance in {"pending", "entering"}:
        if receipt.maintenance == "pending":
            receipt = replace(
                receipt,
                maintenance="entering",
                maintenance_evidence={"events": []},
                supervisor={
                    **dict(receipt.supervisor),
                    "state": "running",
                    "maintenance_state": "entering",
                    "updated_at": _utc_now(),
                },
                updated_at=_utc_now(),
            )
            _write_receipt(path, receipt)
        raw_events = receipt.maintenance_evidence.get("events", ())
        if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
            raise RuntimeError("Soperator maintenance entry event journal is invalid")
        events = [dict(item) for item in raw_events if isinstance(item, Mapping)]
        if len(events) != len(raw_events):
            raise RuntimeError("Soperator maintenance entry event journal is invalid")

        def _record_maintenance_event(event: Mapping[str, Any]) -> None:
            nonlocal receipt
            if not isinstance(event, Mapping):
                raise RuntimeError("Soperator maintenance entry event is invalid")
            events.append(dict(event))
            receipt = replace(
                receipt,
                maintenance_evidence={"events": events},
                updated_at=_utc_now(),
            )
            _write_receipt(path, receipt)

        maintenance_summary = enter_maintenance(
            _record_maintenance_event,
            receipt.maintenance_evidence,
        )
        if not isinstance(maintenance_summary, Mapping):
            raise RuntimeError("Soperator maintenance entry returned invalid evidence")
        receipt = replace(
            receipt,
            maintenance="active",
            maintenance_evidence={
                "summary": dict(maintenance_summary),
                "events": events,
            },
            supervisor={
                **dict(receipt.supervisor),
                "state": "running",
                "maintenance_state": "active",
                "updated_at": _utc_now(),
            },
            updated_at=_utc_now(),
        )
        _write_receipt(path, receipt)

    for index, segment in enumerate(receipt.segments):
        if segment.status == "complete":
            continue
        assert_fence()
        running = replace(
            segment,
            status="running",
            attempts=segment.attempts + 1,
            started_at=_utc_now(),
            failure_type="",
        )
        receipt = replace(
            _replace_segment(receipt, index, running),
            supervisor={
                **dict(receipt.supervisor),
                "state": "running",
                "current_segment": segment.name,
                "maintenance_state": receipt.maintenance,
                "failure_type": "",
                "updated_at": _utc_now(),
            },
        )
        _write_receipt(path, receipt)
        try:
            result = segment_executors[segment.name]()
            if not isinstance(result, CampaignSegmentResult):
                raise RuntimeError(
                    f"Soperator upgrade segment {segment.name} returned invalid evidence"
                )
            assert_fence()
        except Exception as exc:
            refreshed = load_campaign_receipt(path)
            if refreshed is None or refreshed.intent_sha256 != intent.digest:
                raise RuntimeError(
                    "Soperator campaign receipt disappeared during a segment"
                ) from exc
            receipt = refreshed
            failed = replace(running, status="failed", failure_type=type(exc).__name__)
            receipt = replace(
                _replace_segment(receipt, index, failed),
                supervisor={
                    **dict(receipt.supervisor),
                    "state": "retrying",
                    "current_segment": segment.name,
                    "maintenance_state": receipt.maintenance,
                    "failure_type": type(exc).__name__,
                    "updated_at": _utc_now(),
                },
            )
            _write_receipt(path, receipt)
            raise
        refreshed = load_campaign_receipt(path)
        if refreshed is None or refreshed.intent_sha256 != intent.digest:
            raise RuntimeError("Soperator campaign receipt disappeared during a segment")
        receipt = refreshed
        evidence = dict(result.evidence)
        complete = replace(
            running,
            status="complete",
            irreversible_frontier=str(result.irreversible_frontier).strip(),
            evidence=evidence,
            evidence_sha256=_sha256(evidence),
            completed_at=_utc_now(),
            failure_type="",
        )
        receipt = _replace_segment(receipt, index, complete)
        _write_receipt(path, receipt)

    assert_fence()
    receipt = replace(
        receipt,
        maintenance="restoring",
        supervisor={
            **dict(receipt.supervisor),
            "state": "running",
            "current_segment": "maintenance-restoration",
            "maintenance_state": "restoring",
            "failure_type": "",
            "updated_at": _utc_now(),
        },
        updated_at=_utc_now(),
    )
    _write_receipt(path, receipt)
    restoration_summary = receipt.maintenance_evidence.get("summary")
    restoration_events = receipt.maintenance_evidence.get("events", ())
    if not isinstance(restoration_summary, Mapping):
        raise RuntimeError("Soperator maintenance restoration summary is invalid")
    if not isinstance(restoration_events, Sequence) or isinstance(restoration_events, (str, bytes)):
        raise RuntimeError("Soperator maintenance restoration event journal is invalid")
    events = [dict(item) for item in restoration_events if isinstance(item, Mapping)]
    if len(events) != len(restoration_events):
        raise RuntimeError("Soperator maintenance restoration event journal is invalid")

    def _record_restoration_event(event: Mapping[str, Any]) -> None:
        nonlocal receipt
        if not isinstance(event, Mapping):
            raise RuntimeError("Soperator maintenance restoration event is invalid")
        events.append(dict(event))
        receipt = replace(
            receipt,
            maintenance_evidence={
                "summary": dict(restoration_summary),
                "events": events,
            },
            updated_at=_utc_now(),
        )
        _write_receipt(path, receipt)

    restore_evidence = restore_maintenance(
        _record_restoration_event,
        receipt.maintenance_evidence,
    )
    if not isinstance(restore_evidence, Mapping):
        raise RuntimeError("Soperator maintenance restoration returned invalid evidence")
    receipt = replace(
        receipt,
        status="complete",
        maintenance="restored",
        maintenance_evidence={
            "entry": dict(receipt.maintenance_evidence),
            "restoration": dict(restore_evidence),
        },
        supervisor={
            **dict(receipt.supervisor),
            "state": "complete",
            "disposition": "complete",
            "current_segment": "",
            "maintenance_state": "restored",
            "failure_type": "",
            "updated_at": _utc_now(),
        },
        updated_at=_utc_now(),
    )
    _write_receipt(path, receipt)
    return receipt


__all__ = [
    "CampaignConfigTransitionStore",
    "CampaignControllerSpoolMigrationStore",
    "CampaignSegmentReceipt",
    "CampaignSegmentResult",
    "FrozenCompatibilityRow",
    "FrozenNodeGroupTarget",
    "SOPERATOR_UPGRADE_CAMPAIGN_RECEIPT_SCHEMA",
    "SOPERATOR_UPGRADE_CAMPAIGN_SCHEMA",
    "SoperatorUpgradeCampaignIntent",
    "SoperatorUpgradeCampaignReceipt",
    "apply_frozen_node_group_rows",
    "assert_final_capacity_snapshot_unchanged",
    "assert_campaign_node_group_inventory",
    "assert_frozen_compatibility_row_supported",
    "build_campaign_intent",
    "campaign_intent_from_payload",
    "campaign_receipt_path",
    "create_or_resume_campaign",
    "final_node_group_capacity_snapshot",
    "load_campaign_receipt",
    "record_campaign_supervisor_state",
    "reachable_kubernetes_versions",
    "resolve_kubernetes_upgrade_path",
    "run_campaign",
    "run_final_runtime_validation_boundary",
    "validate_campaign_intent",
]
