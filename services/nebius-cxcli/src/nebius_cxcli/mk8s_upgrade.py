"""MK8s day-2 upgrade planning and live status helpers."""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .component_instances import component_instance_id, component_type_id
from .config_loader import dump_yaml
from .mk8s_node_groups import Mk8sNodeGroup, iter_node_groups

MK8S_COMPONENT_ID = "mk8s"
UPGRADE_SELECTOR_KIND = "infra"
K8S_TARGET_PREFIX = f"{UPGRADE_SELECTOR_KIND}:{MK8S_COMPONENT_ID}@"
DISRUPTION_POLICY_SAFE = "safe"
DISRUPTION_POLICY_ALLOW_UNAVAILABLE = "allow-unavailable"
DISRUPTION_POLICY_FORCE_DELETE = "force-delete"
DISRUPTION_POLICIES = frozenset(
    {
        DISRUPTION_POLICY_SAFE,
        DISRUPTION_POLICY_ALLOW_UNAVAILABLE,
        DISRUPTION_POLICY_FORCE_DELETE,
    }
)
MIN_ROLLOUT_WAIT_SECONDS = 3600
ROLLOUT_WAIT_SECONDS_PER_NODE = 600
ALLOW_UNAVAILABLE_DRAIN_TIMEOUT_SECONDS = 1800
FORCE_DELETE_DRAIN_TIMEOUT_SECONDS = 600
GO_DURATION_RE = re.compile(r"(?P<value>[0-9]+)(?P<unit>ns|us|µs|ms|s|m|h)")
PDB_BLOCKER_KIND = "pdb-blocker"
PREFLIGHT_INSPECTION_FAILED_KIND = "preflight-inspection-failed"
UNMANAGED_POD_KIND = "unmanaged-pod"
EMPTYDIR_POD_KIND = "emptydir-pod"
STUCK_TERMINATING_POD_KIND = "stuck-terminating-pod"
SOPERATOR_ACTIVE_WORKLOAD_KIND = "soperator-active-workload"


@dataclass(frozen=True)
class UpgradeTarget:
    """Parsed upgrade selector."""

    selector: str
    instance_id: str


@dataclass(frozen=True)
class DrainTimeout:
    """Resolved node drain timeout."""

    raw: str
    seconds: int | None
    label: str


@dataclass(frozen=True)
class UpgradeHop:
    """One Kubernetes minor-version hop."""

    from_version: str
    to_version: str


@dataclass(frozen=True)
class Mk8sVersion:
    """Normalized Kubernetes major/minor/patch-ish version."""

    major: int
    minor: int
    patch: int | None
    text: str

    @property
    def minor_text(self) -> str:
        return f"{self.major}.{self.minor}"


@dataclass(frozen=True)
class LiveNodeGroup:
    """Minimal live node-group shape needed by upgrade planning."""

    id: str
    name: str
    version: str
    resource_version: int | str | None
    platform: str
    preset: str
    os: str
    drivers_preset: str
    gpu: bool
    raw: Any
    source: Mk8sNodeGroup | None = None


@dataclass(frozen=True)
class CompatibilityChoice:
    """One compatible node template choice for a target Kubernetes version."""

    platform: str
    os: str
    drivers_preset: str


@dataclass(frozen=True)
class CompatibilityFailure:
    """A layer-specific incompatibility that v1 must not auto-fix."""

    node_group: str
    reason: str
    follow_up: str


@dataclass(frozen=True)
class PreflightFinding:
    """Read-only Kubernetes preflight finding."""

    kind: str
    namespace: str
    name: str
    message: str


@dataclass(frozen=True)
class Mk8sUpgradePlan:
    """Concrete K8s version upgrade plan."""

    target: UpgradeTarget
    cluster_id: str
    cluster_name: str
    current_version: str
    target_version: str
    hops: tuple[UpgradeHop, ...]
    disruption_policy: str
    drain_timeout: DrainTimeout
    node_groups: tuple[LiveNodeGroup, ...]
    compatibility_failures: tuple[CompatibilityFailure, ...]
    preflight_findings: tuple[PreflightFinding, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def node_group_updates_required(self) -> bool:
        return any(
            not _version_prefix_matches(group.version, self.target_version)
            for group in self.node_groups
        )

    @property
    def mutates(self) -> bool:
        return bool(self.hops) or self.node_group_updates_required

    @property
    def rollout_incomplete(self) -> bool:
        return any(
            not node_group_rollout_complete(group.raw, version=self.target_version)
            for group in self.node_groups
        )


@dataclass(frozen=True)
class Mk8sOsImageUpgradePlan:
    """Concrete MK8s node OS-image upgrade plan."""

    target: UpgradeTarget
    cluster_id: str
    cluster_name: str
    k8s_version: str
    target_os: str
    disruption_policy: str
    drain_timeout: DrainTimeout
    node_groups: tuple[LiveNodeGroup, ...]
    compatibility_failures: tuple[CompatibilityFailure, ...]
    preflight_findings: tuple[PreflightFinding, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def mutates(self) -> bool:
        return any(group.os != self.target_os for group in self.node_groups)

    @property
    def rollout_incomplete(self) -> bool:
        return any(
            not node_group_os_rollout_complete(group.raw, os=self.target_os)
            for group in self.node_groups
        )


@dataclass(frozen=True)
class NodeLayerUpgradeSpec:
    """One node-template field upgrade command."""

    command: str
    title: str
    source_field: str
    live_field: str
    target_label: str
    group_filter: str


@dataclass(frozen=True)
class Mk8sNodeLayerUpgradePlan:
    """Concrete MK8s node-template layer upgrade plan."""

    target: UpgradeTarget
    cluster_id: str
    cluster_name: str
    k8s_version: str
    spec: NodeLayerUpgradeSpec
    target_value: str
    disruption_policy: str
    drain_timeout: DrainTimeout
    node_groups: tuple[LiveNodeGroup, ...]
    compatibility_failures: tuple[CompatibilityFailure, ...]
    preflight_findings: tuple[PreflightFinding, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def mutates(self) -> bool:
        return any(
            _node_layer_live_value(group, self.spec.live_field) != self.target_value
            for group in self.node_groups
        )

    @property
    def rollout_incomplete(self) -> bool:
        return any(
            not node_group_layer_rollout_complete(
                group.raw,
                field=self.spec.live_field,
                value=self.target_value,
            )
            for group in self.node_groups
        )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def parse_upgrade_selector(selector: str) -> UpgradeTarget:
    """Parse the canonical v1 MK8s target selector."""

    raw = _text(selector)
    if not raw:
        raise ValueError("Target selector is required. Use infra:mk8s@<cluster-instance-id>.")
    if not raw.startswith(K8S_TARGET_PREFIX):
        raise ValueError(
            "MK8s upgrades require an explicit MK8s infra target selector: "
            "infra:mk8s@<cluster-instance-id>."
        )
    instance_id = raw[len(K8S_TARGET_PREFIX) :].strip()
    if not instance_id:
        raise ValueError("Missing MK8s target instance id. Use infra:mk8s@<cluster-instance-id>.")
    if any(char.isspace() for char in instance_id):
        raise ValueError("MK8s target instance id must not contain whitespace.")
    return UpgradeTarget(selector=raw, instance_id=instance_id)


def validate_disruption_policy(value: str) -> str:
    policy = _text(value).lower()
    if policy not in DISRUPTION_POLICIES:
        allowed = "|".join(sorted(DISRUPTION_POLICIES))
        raise ValueError(f"--disruption-policy must be one of {allowed}.")
    return policy


def parse_go_duration_seconds(value: str) -> int:
    """Parse the Go-style duration subset used by cxcli upgrade flags."""

    raw = _text(value)
    if not raw:
        raise ValueError("Duration must not be empty.")
    position = 0
    total_ns = 0
    unit_ns = {
        "ns": 1,
        "us": 1_000,
        "µs": 1_000,
        "ms": 1_000_000,
        "s": 1_000_000_000,
        "m": 60 * 1_000_000_000,
        "h": 60 * 60 * 1_000_000_000,
    }
    for match in GO_DURATION_RE.finditer(raw):
        if match.start() != position:
            raise ValueError(
                f"Invalid Go-style duration '{value}'. Use values such as 10m, 30m, or 1h."
            )
        total_ns += int(match.group("value")) * unit_ns[match.group("unit")]
        position = match.end()
    if position != len(raw) or total_ns <= 0:
        raise ValueError(
            f"Invalid Go-style duration '{value}'. Use values such as 10m, 30m, or 1h."
        )
    seconds = total_ns // 1_000_000_000
    return max(1, seconds) if total_ns else 0


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "none"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def resolve_drain_timeout(policy: str, raw_timeout: str) -> DrainTimeout:
    policy = validate_disruption_policy(policy)
    raw = _text(raw_timeout).lower() or "auto"
    if raw == "auto":
        if policy == DISRUPTION_POLICY_ALLOW_UNAVAILABLE:
            seconds = ALLOW_UNAVAILABLE_DRAIN_TIMEOUT_SECONDS
        elif policy == DISRUPTION_POLICY_FORCE_DELETE:
            seconds = FORCE_DELETE_DRAIN_TIMEOUT_SECONDS
        else:
            seconds = None
        return DrainTimeout(raw="auto", seconds=seconds, label=_format_duration(seconds))
    if raw == "none":
        return DrainTimeout(raw="none", seconds=None, label="none")
    seconds = parse_go_duration_seconds(raw_timeout)
    return DrainTimeout(raw=_text(raw_timeout), seconds=seconds, label=_format_duration(seconds))


def parse_k8s_version(value: str) -> Mk8sVersion:
    raw = _text(value).lstrip("v")
    match = re.fullmatch(r"(?P<major>[0-9]+)\.(?P<minor>[0-9]+)(?:\.(?P<patch>[0-9]+))?", raw)
    if not match:
        raise ValueError(
            f"Invalid Kubernetes version '{value}'. Use major.minor, for example 1.33."
        )
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch_raw = match.group("patch")
    patch = int(patch_raw) if patch_raw is not None else None
    return Mk8sVersion(major=major, minor=minor, patch=patch, text=raw)


def minor_version_hops(current: str, target: str) -> tuple[UpgradeHop, ...]:
    """Return one-minor-at-a-time hops and reject downgrades/skipped major jumps."""

    current_v = parse_k8s_version(current)
    target_v = parse_k8s_version(target)
    if (target_v.major, target_v.minor) < (current_v.major, current_v.minor):
        raise ValueError(
            f"Downgrades are not supported: current {current_v.minor_text}, target {target_v.minor_text}."
        )
    if current_v.major != target_v.major:
        raise ValueError(
            "Major-version Kubernetes upgrades are not supported by cxcli v1. "
            f"Current {current_v.minor_text}, target {target_v.minor_text}."
        )
    if current_v.minor == target_v.minor:
        return ()
    hops: list[UpgradeHop] = []
    source_minor = current_v.minor
    for minor in range(current_v.minor + 1, target_v.minor + 1):
        hops.append(
            UpgradeHop(
                from_version=f"{current_v.major}.{source_minor}",
                to_version=f"{current_v.major}.{minor}",
            )
        )
        source_minor = minor
    return tuple(hops)


def require_single_minor_hop(current: str, target: str) -> tuple[UpgradeHop, ...]:
    hops = minor_version_hops(current, target)
    if len(hops) > 1:
        sequence = " -> ".join(
            [parse_k8s_version(current).minor_text, *(hop.to_version for hop in hops)]
        )
        raise ValueError(
            "cxcli v1 upgrades one Kubernetes minor at a time. "
            "Upstream Kubernetes does not support skipped minors. "
            f"Requested path would skip multiple live operations: {sequence}. "
            f"Run the next hop first with --to-version {hops[0].to_version}."
        )
    return hops


def find_source_mk8s_component(payload: Mapping[str, Any], instance_id: str) -> dict[str, Any]:
    infra = _mapping(payload.get("infra"))
    components = infra.get("components")
    if not isinstance(components, list):
        raise ValueError("config.yaml does not contain infra.components[].")
    for row in components:
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        if component_type_id(row) != MK8S_COMPONENT_ID:
            continue
        if component_instance_id(row) == instance_id:
            return row
    raise ValueError(f"Could not find enabled infra:mk8s@{instance_id} in config.yaml.")


def source_mk8s_inputs(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(row.get("inputs"))


def source_mk8s_cluster_name(row: Mapping[str, Any], *, fallback: str) -> str:
    """Resolve the source-config cluster name used for SDK lookup."""

    cluster = _mapping(source_mk8s_inputs(row).get("cluster"))
    return _text(cluster.get("cluster_name")) or fallback


def _enabled_source_node_group_rows(
    row: Mapping[str, Any],
) -> tuple[tuple[str, dict[str, Any]], ...]:
    node_groups = source_mk8s_inputs(row).get("node_groups")
    if not isinstance(node_groups, dict):
        return ()
    rows: list[tuple[str, dict[str, Any]]] = []
    for raw_key, raw_group in node_groups.items():
        if not isinstance(raw_group, dict) or raw_group.get("enabled") is False:
            continue
        key = _text(raw_key)
        if key:
            rows.append((key, raw_group))
    return tuple(rows)


def _node_group_row_lookup_key(
    key: str,
    raw_group: Mapping[str, Any],
    *,
    cluster_name: str = "",
) -> tuple[str, ...]:
    name = _text(raw_group.get("name")) or key
    terraform_default_name = f"{cluster_name}-{key}" if cluster_name else ""
    return tuple(
        candidate for candidate in dict.fromkeys((key, name, terraform_default_name)) if candidate
    )


def _minor_text_or_raw(value: str) -> str:
    raw = _text(value)
    if not raw:
        return raw
    try:
        return parse_k8s_version(raw).minor_text
    except ValueError:
        return raw.lstrip("v")


def source_node_group_versions_for_plan(
    plan: Mk8sUpgradePlan,
    *,
    default_version: str | None = None,
) -> dict[str, str]:
    """Return source node-group keys mapped to live or explicit default versions."""

    versions: dict[str, str] = {}
    for group in plan.node_groups:
        if group.source is None:
            continue
        version = default_version if default_version is not None else group.version
        versions[group.source.key] = _minor_text_or_raw(version)
    return versions


def update_source_k8s_versions(
    payload: dict[str, Any],
    *,
    instance_id: str,
    target_version: str,
    node_group_versions: Mapping[str, str] | None = None,
) -> bool:
    """Update config.yaml cluster and all enabled node-group K8s version fields."""

    row = find_source_mk8s_component(payload, instance_id)
    inputs = row.setdefault("inputs", {})
    if not isinstance(inputs, dict):
        raise ValueError(f"infra:mk8s@{instance_id}.inputs must be a mapping.")
    cluster = inputs.setdefault("cluster", {})
    if not isinstance(cluster, dict):
        raise ValueError(f"infra:mk8s@{instance_id}.inputs.cluster must be a mapping.")
    changed = False
    if cluster.get("k8s_version") != target_version:
        cluster["k8s_version"] = target_version
        changed = True
    version_overrides = {
        _text(key): _minor_text_or_raw(value)
        for key, value in dict(node_group_versions or {}).items()
        if _text(key) and _text(value)
    }
    for key, raw_group in _enabled_source_node_group_rows(row):
        resolved_version = _minor_text_or_raw(target_version)
        for lookup_key in _node_group_row_lookup_key(key, raw_group):
            if lookup_key in version_overrides:
                resolved_version = version_overrides[lookup_key]
                break
        if raw_group.get("version") != resolved_version:
            raw_group["version"] = resolved_version
            changed = True
    return changed


def validate_os_image_value(value: str) -> str:
    """Validate a Nebius MK8s node template OS value."""

    raw = _text(value)
    if not raw:
        raise ValueError("--to-os must not be empty.")
    if any(char.isspace() for char in raw):
        raise ValueError("--to-os must not contain whitespace.")
    return raw


def validate_node_layer_value(value: str, *, flag_name: str) -> str:
    """Validate a generic node-template layer target value."""

    raw = _text(value)
    if not raw:
        raise ValueError(f"{flag_name} must not be empty.")
    if any(char.isspace() for char in raw):
        raise ValueError(f"{flag_name} must not contain whitespace.")
    return raw


def update_source_node_group_os(
    payload: dict[str, Any],
    *,
    instance_id: str,
    target_os: str,
    node_group_keys: Sequence[str] | None = None,
) -> bool:
    """Update config.yaml node-group OS fields for selected enabled groups."""

    target_os = validate_os_image_value(target_os)
    wanted_keys = {_text(key) for key in tuple(node_group_keys or ()) if _text(key)}
    row = find_source_mk8s_component(payload, instance_id)
    changed = False
    matched: set[str] = set()
    for key, raw_group in _enabled_source_node_group_rows(row):
        if wanted_keys and key not in wanted_keys:
            continue
        matched.add(key)
        if raw_group.get("os") != target_os:
            raw_group["os"] = target_os
            changed = True
    missing = sorted(wanted_keys - matched)
    if missing:
        raise ValueError(
            "Could not find enabled MK8s node group(s) in config.yaml: " + ", ".join(missing)
        )
    return changed


def update_source_node_group_field(
    payload: dict[str, Any],
    *,
    instance_id: str,
    field: str,
    value: str,
    node_group_keys: Sequence[str] | None = None,
) -> bool:
    """Update one config.yaml node-group field for selected enabled groups."""

    if field not in {"platform", "preset", "gpu_stack_preset"}:
        raise ValueError(f"Unsupported MK8s node-group upgrade field '{field}'.")
    target_value = validate_node_layer_value(value, flag_name=f"--to-{field.replace('_', '-')}")
    wanted_keys = {_text(key) for key in tuple(node_group_keys or ()) if _text(key)}
    row = find_source_mk8s_component(payload, instance_id)
    changed = False
    matched: set[str] = set()
    for key, raw_group in _enabled_source_node_group_rows(row):
        if wanted_keys and key not in wanted_keys:
            continue
        matched.add(key)
        if raw_group.get(field) != target_value:
            raw_group[field] = target_value
            changed = True
    missing = sorted(wanted_keys - matched)
    if missing:
        raise ValueError(
            "Could not find enabled MK8s node group(s) in config.yaml: " + ", ".join(missing)
        )
    return changed


def source_node_group_strategy_snapshot(
    payload: Mapping[str, Any],
    *,
    instance_id: str,
) -> dict[str, Any | None]:
    """Capture source node-group strategies so temporary upgrade overrides can be restored."""

    row = find_source_mk8s_component(payload, instance_id)
    snapshot: dict[str, Any | None] = {}
    for key, raw_group in _enabled_source_node_group_rows(row):
        snapshot[key] = (
            copy.deepcopy(raw_group.get("strategy")) if "strategy" in raw_group else None
        )
    return snapshot


def set_source_node_group_strategies(
    payload: dict[str, Any],
    *,
    instance_id: str,
    strategies: Mapping[str, Any | None],
) -> bool:
    """Set or remove source node-group strategy values by key or rendered name."""

    row = find_source_mk8s_component(payload, instance_id)
    changed = False
    for key, raw_group in _enabled_source_node_group_rows(row):
        matched = False
        strategy: Any | None = None
        for lookup_key in _node_group_row_lookup_key(key, raw_group):
            if lookup_key in strategies:
                matched = True
                strategy = strategies[lookup_key]
                break
        if not matched:
            continue
        if strategy is None:
            if "strategy" in raw_group:
                raw_group.pop("strategy", None)
                changed = True
            continue
        next_strategy = copy.deepcopy(strategy)
        if raw_group.get("strategy") != next_strategy:
            raw_group["strategy"] = next_strategy
            changed = True
    return changed


def render_updated_source_payload(payload: dict[str, Any]) -> str:
    return dump_yaml(payload)


def source_node_groups_by_name(row: Mapping[str, Any]) -> dict[str, Mk8sNodeGroup]:
    inputs = source_mk8s_inputs(row)
    node_groups = _mapping(inputs.get("node_groups"))
    cluster_name = source_mk8s_cluster_name(
        row,
        fallback=component_instance_id(row) or MK8S_COMPONENT_ID,
    )
    groups: dict[str, Mk8sNodeGroup] = {}
    for group in iter_node_groups(inputs):
        raw_group = _mapping(node_groups.get(group.key))
        for lookup_key in _node_group_row_lookup_key(
            group.key,
            raw_group,
            cluster_name=cluster_name,
        ):
            groups[lookup_key] = group
    return groups


def _resource_version(metadata: Any) -> int | str | None:
    value = getattr(metadata, "resource_version", None)
    if value in ("", 0):
        return None
    return value


def _node_group_name(raw: Any) -> str:
    metadata = getattr(raw, "metadata", None)
    return _text(getattr(metadata, "name", None)) or _text(getattr(metadata, "id", None))


def live_node_group_from_sdk(raw: Any, *, source: Mk8sNodeGroup | None = None) -> LiveNodeGroup:
    metadata = getattr(raw, "metadata", None)
    spec = getattr(raw, "spec", None)
    template = getattr(spec, "template", None)
    resources = getattr(template, "resources", None)
    gpu_settings = getattr(template, "gpu_settings", None)
    return LiveNodeGroup(
        id=_text(getattr(metadata, "id", None)),
        name=_node_group_name(raw),
        version=_text(getattr(spec, "version", None)),
        resource_version=_resource_version(metadata),
        platform=_text(getattr(resources, "platform", None)),
        preset=_text(getattr(resources, "preset", None)),
        os=_text(getattr(template, "os", None)),
        drivers_preset=_text(getattr(gpu_settings, "drivers_preset", None)),
        gpu=bool(source.gpu)
        if source is not None
        else bool(_text(getattr(gpu_settings, "drivers_preset", None))),
        raw=raw,
        source=source,
    )


def sort_live_node_groups(groups: Sequence[LiveNodeGroup]) -> tuple[LiveNodeGroup, ...]:
    return tuple(
        sorted(
            groups,
            key=lambda group: (
                1 if group.gpu else 0,
                0 if group.name.lower() in {"system", "cpu", "default"} else 1,
                group.name,
            ),
        )
    )


def _node_group_selector_candidates(group: LiveNodeGroup) -> tuple[str, ...]:
    source = group.source
    return tuple(
        candidate
        for candidate in dict.fromkeys(
            (
                group.name,
                group.id,
                source.key if source is not None else "",
                source.name if source is not None else "",
            )
        )
        if candidate
    )


def select_live_node_groups_for_os_image(
    groups: Sequence[LiveNodeGroup],
    *,
    node_group: str = "",
) -> tuple[LiveNodeGroup, ...]:
    """Select all node groups or one named group for an OS-image upgrade."""

    selector = _text(node_group)
    if not selector:
        return tuple(groups)
    matches = tuple(group for group in groups if selector in _node_group_selector_candidates(group))
    if not matches:
        raise ValueError(f"Could not find live MK8s node group '{selector}' for this target.")
    if len(matches) > 1:
        labels = ", ".join(group.name for group in matches)
        raise ValueError(f"MK8s node group selector '{selector}' is ambiguous: {labels}.")
    return matches


def _node_group_matches_filter(group: LiveNodeGroup, group_filter: str) -> bool:
    normalized = _text(group_filter).lower()
    if normalized == "gpu":
        return group.gpu
    if normalized == "cpu":
        return not group.gpu
    return True


def _node_group_filter_label(group_filter: str) -> str:
    normalized = _text(group_filter).lower()
    if normalized == "gpu":
        return "GPU"
    if normalized == "cpu":
        return "CPU/system"
    return "MK8s"


def select_live_node_groups_for_node_layer(
    groups: Sequence[LiveNodeGroup],
    *,
    node_group: str = "",
    group_filter: str = "all",
    command: str,
) -> tuple[LiveNodeGroup, ...]:
    """Select all applicable node groups or one named group for a node-layer upgrade."""

    selector = _text(node_group)
    label = _node_group_filter_label(group_filter)
    if not selector:
        selected = tuple(
            group for group in groups if _node_group_matches_filter(group, group_filter)
        )
        if not selected:
            raise ValueError(f"`upgrade {command}` found no {label} node groups for this target.")
        return selected

    matches = tuple(group for group in groups if selector in _node_group_selector_candidates(group))
    if not matches:
        raise ValueError(f"Could not find live MK8s node group '{selector}' for this target.")
    if len(matches) > 1:
        names = ", ".join(group.name for group in matches)
        raise ValueError(f"MK8s node group selector '{selector}' is ambiguous: {names}.")
    group = matches[0]
    if not _node_group_matches_filter(group, group_filter):
        raise ValueError(
            f"`upgrade {command}` can target only {label} node groups; "
            f"'{selector}' resolves to {'GPU' if group.gpu else 'CPU/system'} node group "
            f"'{group.name}'."
        )
    return matches


def _minor_version_key(value: str) -> tuple[int, int, str] | None:
    raw = _text(value).lstrip("v")
    match = re.match(r"(?P<major>[0-9]+)\.(?P<minor>[0-9]+)(?:$|[.+-])", raw)
    if not match:
        return None
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    return major, minor, f"{major}.{minor}"


def _minor_version_label(value: str) -> str:
    version = _minor_version_key(value)
    return version[2] if version is not None else value


def live_node_groups_above_control_plane_version(
    groups: Sequence[LiveNodeGroup],
    *,
    control_plane_version: str,
) -> tuple[LiveNodeGroup, ...]:
    """Return node groups that already exceed the requested control-plane minor."""

    control_plane = parse_k8s_version(control_plane_version)
    higher: list[LiveNodeGroup] = []
    for group in groups:
        if not group.version:
            continue
        group_version = _minor_version_key(group.version)
        if group_version is None:
            continue
        if (group_version[0], group_version[1]) > (
            control_plane.major,
            control_plane.minor,
        ):
            higher.append(group)
    return tuple(higher)


def compatibility_choices_from_response(
    response: Any, *, platform: str = ""
) -> tuple[CompatibilityChoice, ...]:
    choices: list[CompatibilityChoice] = []
    items = list(getattr(response, "items", []) or [])
    if not items:
        items = [
            item
            for version_item in getattr(response, "versions", []) or []
            for item in getattr(version_item, "items", []) or []
        ]
    for item in items:
        item_platforms = tuple(
            _text(candidate)
            for candidate in getattr(item, "compatible_platforms", []) or []
            if _text(candidate)
        )
        item_platform = platform or (item_platforms[0] if item_platforms else "")
        os_value = _text(getattr(item, "os", None))
        drivers_preset = _text(getattr(item, "drivers_preset", None))
        platforms = item_platforms or ((item_platform,) if item_platform else ("",))
        for candidate_platform in platforms:
            choices.append(
                CompatibilityChoice(
                    platform=candidate_platform,
                    os=os_value,
                    drivers_preset=drivers_preset,
                )
            )
    return tuple(choices)


def _choice_matches_node_group(choice: CompatibilityChoice, group: LiveNodeGroup) -> bool:
    if choice.platform and group.platform and choice.platform != group.platform:
        return False
    if choice.os and group.os and choice.os != group.os:
        return False
    if group.gpu and group.drivers_preset:
        return choice.drivers_preset == group.drivers_preset
    if group.gpu:
        return not choice.drivers_preset
    return True


def _node_group_config_field(group: LiveNodeGroup, field: str) -> str:
    if group.source is not None and group.source.key:
        return f"inputs.node_groups.{group.source.key}.{field}"
    return f"the config.yaml node-group entry for live node group '{group.name}' field '{field}'"


def _node_layer_follow_up(
    *,
    config_path: str,
    target_selector: str,
    group: LiveNodeGroup,
    field: str,
    value: str,
    layer_command: str,
) -> str:
    if layer_command == "os-image":
        command = f"nebius-cxcli upgrade os-image {config_path} {target_selector} --to-os {value}"
        return (
            f"Run `{command}` before rerunning upgrade k8s-version, or set "
            f"{_node_group_config_field(group, field)} to {value} in config.yaml, "
            "then render/deploy that node-layer change manually."
        )
    if layer_command == "gpu-stack-preset":
        command = (
            f"nebius-cxcli upgrade gpu-stack-preset {config_path} {target_selector} "
            f"--to-preset {value}"
        )
        return (
            f"Run `{command}` before rerunning upgrade k8s-version, or set "
            f"{_node_group_config_field(group, field)} to {value} in config.yaml, "
            "then render/deploy that node-layer change manually."
        )
    if layer_command == "platform":
        command = (
            f"nebius-cxcli upgrade platform {config_path} {target_selector} "
            f"--to-platform {value}"
        )
        return (
            f"Run `{command}` before rerunning upgrade k8s-version, or set "
            f"{_node_group_config_field(group, field)} to {value} in config.yaml, "
            "then render/deploy that node-layer change manually."
        )
    return (
        f"Set {_node_group_config_field(group, field)} to {value} in config.yaml, "
        "then render/deploy that node-layer change before rerunning upgrade k8s-version."
    )


def compatibility_failures_for_node_group(
    *,
    config_path: str,
    target_selector: str,
    target_version: str,
    group: LiveNodeGroup,
    choices: Sequence[CompatibilityChoice],
) -> tuple[CompatibilityFailure, ...]:
    if any(_choice_matches_node_group(choice, group) for choice in choices):
        return ()

    os_candidates = tuple(
        dict.fromkeys(
            choice.os for choice in choices if choice.os and choice.platform == group.platform
        )
    )
    driver_candidates = tuple(
        dict.fromkeys(
            choice.drivers_preset
            for choice in choices
            if choice.drivers_preset
            and choice.platform == group.platform
            and (not choice.os or not group.os or choice.os == group.os)
        )
    )
    if group.os and os_candidates and group.os not in os_candidates:
        return (
            CompatibilityFailure(
                node_group=group.name,
                reason=(
                    f"node group '{group.name}' uses OS '{group.os}', which is not compatible "
                    f"with Kubernetes {target_version} on platform '{group.platform}'."
                ),
                follow_up=(
                    _node_layer_follow_up(
                        config_path=config_path,
                        target_selector=target_selector,
                        group=group,
                        field="os",
                        value=os_candidates[0],
                        layer_command="os-image",
                    )
                ),
            ),
        )
    if (
        group.gpu
        and group.drivers_preset
        and driver_candidates
        and group.drivers_preset not in driver_candidates
    ):
        return (
            CompatibilityFailure(
                node_group=group.name,
                reason=(
                    f"node group '{group.name}' uses GPU stack preset '{group.drivers_preset}', "
                    f"which is not compatible with Kubernetes {target_version}."
                ),
                follow_up=(
                    _node_layer_follow_up(
                        config_path=config_path,
                        target_selector=target_selector,
                        group=group,
                        field="gpu_stack_preset",
                        value=driver_candidates[0],
                        layer_command="gpu-stack-preset",
                    )
                ),
            ),
        )
    platform_candidates = tuple(dict.fromkeys(choice.platform for choice in choices if choice.platform))
    if group.platform and platform_candidates and group.platform not in platform_candidates:
        return (
            CompatibilityFailure(
                node_group=group.name,
                reason=(
                    f"node group '{group.name}' uses platform '{group.platform}', which is not "
                    f"compatible with Kubernetes {target_version}."
                ),
                follow_up=(
                    _node_layer_follow_up(
                        config_path=config_path,
                        target_selector=target_selector,
                        group=group,
                        field="platform",
                        value=platform_candidates[0],
                        layer_command="platform",
                    )
                ),
            ),
        )
    return (
        CompatibilityFailure(
            node_group=group.name,
            reason=(
                f"node group '{group.name}' template is not present in the live Nebius "
                f"compatibility matrix for Kubernetes {target_version}."
            ),
            follow_up=(
                "Review live compatibility, then apply the required node-layer change "
                f"outside upgrade k8s-version before rerunning "
                f"nebius-cxcli upgrade k8s-version {config_path} {target_selector} "
                f"--to-version {target_version}."
            ),
        ),
    )


def _choice_matches_os_image(
    choice: CompatibilityChoice,
    group: LiveNodeGroup,
    *,
    target_os: str,
) -> bool:
    if choice.platform and group.platform and choice.platform != group.platform:
        return False
    if choice.os != target_os:
        return False
    if group.gpu and group.drivers_preset:
        return choice.drivers_preset == group.drivers_preset
    if group.gpu:
        return not choice.drivers_preset
    return True


def _os_image_driver_label(group: LiveNodeGroup) -> str:
    if not group.gpu:
        return "driverless"
    return group.drivers_preset or "driverless/operator-managed"


def os_image_compatibility_failures_for_node_group(
    *,
    target_version: str,
    target_os: str,
    group: LiveNodeGroup,
    choices: Sequence[CompatibilityChoice],
) -> tuple[CompatibilityFailure, ...]:
    """Return OS-image compatibility blockers for one node group."""

    target_os = validate_os_image_value(target_os)
    if any(_choice_matches_os_image(choice, group, target_os=target_os) for choice in choices):
        return ()
    compatible_os_values = tuple(
        dict.fromkeys(
            choice.os
            for choice in choices
            if choice.os
            and (not choice.platform or not group.platform or choice.platform == group.platform)
            and (
                not group.gpu
                or choice.drivers_preset == group.drivers_preset
                or (not group.drivers_preset and not choice.drivers_preset)
            )
        )
    )
    same_os_other_driver = any(
        choice.os == target_os
        and (not choice.platform or not group.platform or choice.platform == group.platform)
        for choice in choices
    )
    if same_os_other_driver and group.gpu:
        follow_up = (
            "The requested OS exists in the live compatibility matrix for "
            f"platform '{group.platform}', but not with GPU stack preset "
            f"'{_os_image_driver_label(group)}'. Apply a compatible "
            "`upgrade gpu-stack-preset` change first, or choose an OS that is "
            "compatible with the current GPU stack."
        )
    elif compatible_os_values:
        follow_up = (
            "Choose one of the OS values returned by the live Nebius MK8s "
            "compatibility matrix for this node group: " + ", ".join(compatible_os_values) + "."
        )
    else:
        follow_up = (
            "Review the live Nebius MK8s compatibility matrix for Kubernetes "
            f"{target_version}, platform '{group.platform}', and GPU stack "
            f"'{_os_image_driver_label(group)}'."
        )
    return (
        CompatibilityFailure(
            node_group=group.name,
            reason=(
                f"node group '{group.name}' cannot use OS '{target_os}' on "
                f"Kubernetes {target_version}, platform '{group.platform}', "
                f"GPU stack '{_os_image_driver_label(group)}'."
            ),
            follow_up=follow_up,
        ),
    )


def _node_layer_live_value(group: LiveNodeGroup, field: str) -> str:
    if field == "platform":
        return group.platform
    if field == "preset":
        return group.preset
    if field == "drivers_preset":
        return group.drivers_preset
    raise ValueError(f"Unsupported live node-group field '{field}'.")


def _node_layer_choice_matches(
    choice: CompatibilityChoice,
    group: LiveNodeGroup,
    *,
    spec: NodeLayerUpgradeSpec,
    target_value: str,
) -> bool:
    platform = target_value if spec.live_field == "platform" else group.platform
    drivers_preset = target_value if spec.live_field == "drivers_preset" else group.drivers_preset
    if choice.platform and platform and choice.platform != platform:
        return False
    if choice.os and group.os and choice.os != group.os:
        return False
    if group.gpu:
        return choice.drivers_preset == drivers_preset
    return not choice.drivers_preset


def node_layer_compatibility_failures_for_node_group(
    *,
    k8s_version: str,
    group: LiveNodeGroup,
    spec: NodeLayerUpgradeSpec,
    target_value: str,
    choices: Sequence[CompatibilityChoice],
) -> tuple[CompatibilityFailure, ...]:
    """Return compatibility blockers for node-template fields covered by MK8s matrix."""

    if spec.live_field not in {"platform", "drivers_preset"}:
        return ()
    if any(
        _node_layer_choice_matches(
            choice,
            group,
            spec=spec,
            target_value=target_value,
        )
        for choice in choices
    ):
        return ()
    current_value = _node_layer_live_value(group, spec.live_field) or "unknown"
    return (
        CompatibilityFailure(
            node_group=group.name,
            reason=(
                f"node group '{group.name}' cannot use {spec.target_label} "
                f"'{target_value}' on Kubernetes {k8s_version}, OS "
                f"'{group.os or 'unknown'}', platform "
                f"'{target_value if spec.live_field == 'platform' else group.platform or 'unknown'}', "
                f"GPU stack "
                f"'{target_value if spec.live_field == 'drivers_preset' else _os_image_driver_label(group)}'."
            ),
            follow_up=(
                f"Current live {spec.target_label} is '{current_value}'. "
                "Choose a value returned by the live Nebius MK8s compatibility "
                "matrix for this node group, or make the required combined "
                "config.yaml node-layer changes manually and rerender/deploy."
            ),
        ),
    )


def blocking_preflight_findings(
    findings: Sequence[PreflightFinding],
    *,
    disruption_policy: str,
) -> tuple[PreflightFinding, ...]:
    policy = validate_disruption_policy(disruption_policy)
    if policy == DISRUPTION_POLICY_FORCE_DELETE:
        return tuple(
            finding
            for finding in findings
            if finding.kind
            in {
                PREFLIGHT_INSPECTION_FAILED_KIND,
                SOPERATOR_ACTIVE_WORKLOAD_KIND,
                STUCK_TERMINATING_POD_KIND,
            }
        )
    return tuple(
        finding
        for finding in findings
        if finding.kind
        in {
            PDB_BLOCKER_KIND,
            PREFLIGHT_INSPECTION_FAILED_KIND,
            UNMANAGED_POD_KIND,
            STUCK_TERMINATING_POD_KIND,
            SOPERATOR_ACTIVE_WORKLOAD_KIND,
        }
    )


def collect_kubernetes_preflight_findings(
    *,
    kube_env: Mapping[str, str] | None,
    timeout_seconds: int = 30,
) -> tuple[PreflightFinding, ...]:
    """Collect read-only Kubernetes blockers for the upgrade preflight."""

    if not kube_env:
        return ()
    findings: list[PreflightFinding] = []
    findings.extend(_pdb_findings(kube_env=kube_env, timeout_seconds=timeout_seconds))
    findings.extend(_pod_findings(kube_env=kube_env, timeout_seconds=timeout_seconds))
    findings.extend(_soperator_findings(kube_env=kube_env, timeout_seconds=timeout_seconds))
    return tuple(findings)


def _kubectl_json(
    args: Sequence[str],
    *,
    kube_env: Mapping[str, str],
    timeout_seconds: int,
) -> dict[str, Any]:
    cp = subprocess.run(
        ["kubectl", *args, "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **dict(kube_env)},
        timeout=timeout_seconds,
    )
    payload = json.loads(cp.stdout or "{}")
    return payload if isinstance(payload, dict) else {}


def _owner_refs(item: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    metadata = _mapping(item.get("metadata"))
    refs = metadata.get("ownerReferences", [])
    return tuple(ref for ref in refs if isinstance(ref, Mapping)) if isinstance(refs, list) else ()


def _is_daemonset_pod(item: Mapping[str, Any]) -> bool:
    return any(_text(ref.get("kind")) == "DaemonSet" for ref in _owner_refs(item))


def _is_mirror_or_static_pod(item: Mapping[str, Any]) -> bool:
    annotations = _mapping(_mapping(item.get("metadata")).get("annotations"))
    return bool(
        annotations.get("kubernetes.io/config.mirror")
        or annotations.get("kubernetes.io/config.source") == "file"
    )


def _pod_namespace_name(item: Mapping[str, Any]) -> tuple[str, str]:
    metadata = _mapping(item.get("metadata"))
    return _text(metadata.get("namespace")) or "default", _text(metadata.get("name"))


def _pdb_findings(
    *,
    kube_env: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[PreflightFinding, ...]:
    try:
        payload = _kubectl_json(
            ["get", "poddisruptionbudgets", "--all-namespaces"],
            kube_env=kube_env,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return (
            PreflightFinding(
                kind=PREFLIGHT_INSPECTION_FAILED_KIND,
                namespace="",
                name="poddisruptionbudgets",
                message=f"Could not inspect PodDisruptionBudgets: {exc}",
            ),
        )
    findings: list[PreflightFinding] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        namespace, name = _pod_namespace_name(item)
        status = _mapping(item.get("status"))
        disruptions_allowed = int(status.get("disruptionsAllowed") or 0)
        desired_healthy = int(status.get("desiredHealthy") or 0)
        current_healthy = int(status.get("currentHealthy") or 0)
        expected_pods = int(status.get("expectedPods") or 0)
        if expected_pods > 0 and disruptions_allowed <= 0 and desired_healthy >= current_healthy:
            findings.append(
                PreflightFinding(
                    kind=PDB_BLOCKER_KIND,
                    namespace=namespace,
                    name=name,
                    message=(
                        "PDB allows zero disruptions "
                        f"(currentHealthy={current_healthy}, desiredHealthy={desired_healthy})."
                    ),
                )
            )
    return tuple(findings)


def _pod_findings(
    *,
    kube_env: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[PreflightFinding, ...]:
    try:
        payload = _kubectl_json(
            ["get", "pods", "--all-namespaces"],
            kube_env=kube_env,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return (
            PreflightFinding(
                kind=PREFLIGHT_INSPECTION_FAILED_KIND,
                namespace="",
                name="pods",
                message=f"Could not inspect Pods: {exc}",
            ),
        )
    findings: list[PreflightFinding] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        namespace, name = _pod_namespace_name(item)
        if not name or _is_daemonset_pod(item) or _is_mirror_or_static_pod(item):
            continue
        spec = _mapping(item.get("spec"))
        metadata = _mapping(item.get("metadata"))
        owner_refs = _owner_refs(item)
        if not owner_refs:
            findings.append(
                PreflightFinding(
                    kind=UNMANAGED_POD_KIND,
                    namespace=namespace,
                    name=name,
                    message="Pod has no ownerReferences; drain cannot recreate unmanaged Pods.",
                )
            )
        if metadata.get("deletionTimestamp"):
            findings.append(
                PreflightFinding(
                    kind=STUCK_TERMINATING_POD_KIND,
                    namespace=namespace,
                    name=name,
                    message="Pod is already terminating before the upgrade starts.",
                )
            )
        volumes = spec.get("volumes", [])
        if isinstance(volumes, list) and any(
            isinstance(volume, Mapping) and "emptyDir" in volume for volume in volumes
        ):
            findings.append(
                PreflightFinding(
                    kind=EMPTYDIR_POD_KIND,
                    namespace=namespace,
                    name=name,
                    message="Pod uses emptyDir; local ephemeral data is lost during node replacement.",
                )
            )
    return tuple(findings)


def _soperator_findings(
    *,
    kube_env: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[PreflightFinding, ...]:
    try:
        payload = _kubectl_json(
            ["get", "pods", "--all-namespaces", "-l", "app.kubernetes.io/name=soperator"],
            kube_env=kube_env,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return (
            PreflightFinding(
                kind=PREFLIGHT_INSPECTION_FAILED_KIND,
                namespace="",
                name="soperator-pods",
                message=f"Could not inspect Soperator Pods: {exc}",
            ),
        )
    findings: list[PreflightFinding] = []
    for item in payload.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        namespace, name = _pod_namespace_name(item)
        phase = _text(_mapping(item.get("status")).get("phase"))
        if phase not in {"Running", "Succeeded"}:
            findings.append(
                PreflightFinding(
                    kind=SOPERATOR_ACTIVE_WORKLOAD_KIND,
                    namespace=namespace,
                    name=name,
                    message=f"Soperator pod phase is {phase or 'unknown'} before maintenance.",
                )
            )
    return tuple(findings)


def _preflight_finding_summary_lines(findings: Sequence[PreflightFinding]) -> tuple[str, ...]:
    emptydir_count = sum(1 for finding in findings if finding.kind == EMPTYDIR_POD_KIND)
    lines = [
        f"  - {finding.kind}: {finding.namespace}/{finding.name} - {finding.message}"
        for finding in findings
        if finding.kind != EMPTYDIR_POD_KIND
    ]
    if emptydir_count:
        pod_label = "pod" if emptydir_count == 1 else "pods"
        lines.append(
            "  - "
            f"{EMPTYDIR_POD_KIND}: {emptydir_count} {pod_label} use emptyDir; local "
            "ephemeral data is lost during node replacement. This is expected for "
            "scratch or intermediate data when persistent data uses PVC-backed volumes."
        )
    return tuple(lines)


def format_upgrade_plan(
    plan: Mk8sUpgradePlan,
    *,
    dry_run: bool,
    repeat_dry_run_command: str | None = None,
) -> tuple[str, ...]:
    lines = [
        "MK8s Kubernetes version upgrade plan",
        f"- target: {plan.target.selector}",
        f"- cluster: {plan.cluster_name or plan.cluster_id} ({plan.cluster_id})",
        f"- current version: {plan.current_version}",
        f"- target version: {plan.target_version}",
        f"- disruption policy: {plan.disruption_policy}",
        f"- drain timeout: {plan.drain_timeout.label}",
    ]
    if plan.hops:
        lines.append("- version hops:")
        lines.extend(
            f"  - control plane: {hop.from_version} -> {hop.to_version}" for hop in plan.hops
        )
    else:
        lines.append("- version hops: none; cluster is already on the requested minor version")
    if plan.node_groups:
        lines.append("- node-group order:")
        lines.extend(
            f"  - {group.name}: {group.version or 'unknown'} -> {plan.target_version}"
            f" ({'gpu' if group.gpu else 'cpu/system'})"
            for group in plan.node_groups
        )
    if plan.preflight_findings:
        lines.append("- preflight findings:")
        lines.extend(_preflight_finding_summary_lines(plan.preflight_findings))
    if plan.compatibility_failures:
        lines.append("- compatibility blockers:")
        for failure in plan.compatibility_failures:
            lines.append(f"  - {failure.node_group}: {failure.reason}")
            lines.append(f"    follow-up: {failure.follow_up}")
    if plan.warnings:
        lines.append("- warnings:")
        lines.extend(f"  - {warning}" for warning in plan.warnings)
    if dry_run:
        if repeat_dry_run_command:
            lines.append("- repeat dry-run command:")
            lines.append(f"  {repeat_dry_run_command}")
        lines.append(
            "Dry run only: no config.yaml write, generated bundle render, or Terraform plan/apply was performed."
        )
    return tuple(lines)


def format_os_image_upgrade_plan(
    plan: Mk8sOsImageUpgradePlan,
    *,
    dry_run: bool,
    repeat_dry_run_command: str | None = None,
) -> tuple[str, ...]:
    lines = [
        "MK8s OS image upgrade plan",
        f"- target: {plan.target.selector}",
        f"- cluster: {plan.cluster_name or plan.cluster_id} ({plan.cluster_id})",
        f"- Kubernetes version: {plan.k8s_version}",
        f"- target OS image: {plan.target_os}",
        f"- disruption policy: {plan.disruption_policy}",
        f"- drain timeout: {plan.drain_timeout.label}",
    ]
    if plan.node_groups:
        lines.append("- node-group order:")
        lines.extend(
            f"  - {group.name}: {group.os or 'unknown'} -> {plan.target_os}"
            f" ({'gpu' if group.gpu else 'cpu/system'})"
            for group in plan.node_groups
        )
    if plan.preflight_findings:
        lines.append("- preflight findings:")
        lines.extend(_preflight_finding_summary_lines(plan.preflight_findings))
    if plan.compatibility_failures:
        lines.append("- compatibility blockers:")
        for failure in plan.compatibility_failures:
            lines.append(f"  - {failure.node_group}: {failure.reason}")
            lines.append(f"    follow-up: {failure.follow_up}")
    if plan.warnings:
        lines.append("- warnings:")
        lines.extend(f"  - {warning}" for warning in plan.warnings)
    if dry_run:
        if repeat_dry_run_command:
            lines.append("- repeat dry-run command:")
            lines.append(f"  {repeat_dry_run_command}")
        lines.append(
            "Dry run only: no config.yaml write, generated bundle render, "
            "or Terraform plan/apply was performed."
        )
    return tuple(lines)


def format_node_layer_upgrade_plan(
    plan: Mk8sNodeLayerUpgradePlan,
    *,
    dry_run: bool,
    repeat_dry_run_command: str | None = None,
) -> tuple[str, ...]:
    lines = [
        f"{plan.spec.title} upgrade plan",
        f"- target: {plan.target.selector}",
        f"- cluster: {plan.cluster_name or plan.cluster_id} ({plan.cluster_id})",
        f"- Kubernetes version: {plan.k8s_version}",
        f"- target {plan.spec.target_label}: {plan.target_value}",
        f"- disruption policy: {plan.disruption_policy}",
        f"- drain timeout: {plan.drain_timeout.label}",
    ]
    if plan.node_groups:
        lines.append("- node-group order:")
        lines.extend(
            f"  - {group.name}: "
            f"{_node_layer_live_value(group, plan.spec.live_field) or 'unknown'} "
            f"-> {plan.target_value} ({'gpu' if group.gpu else 'cpu/system'})"
            for group in plan.node_groups
        )
    if plan.preflight_findings:
        lines.append("- preflight findings:")
        lines.extend(_preflight_finding_summary_lines(plan.preflight_findings))
    if plan.compatibility_failures:
        lines.append("- compatibility blockers:")
        for failure in plan.compatibility_failures:
            lines.append(f"  - {failure.node_group}: {failure.reason}")
            lines.append(f"    follow-up: {failure.follow_up}")
    if plan.warnings:
        lines.append("- warnings:")
        lines.extend(f"  - {warning}" for warning in plan.warnings)
    if dry_run:
        if repeat_dry_run_command:
            lines.append("- repeat dry-run command:")
            lines.append(f"  {repeat_dry_run_command}")
        lines.append(
            "Dry run only: no config.yaml write, generated bundle render, "
            "or Terraform plan/apply was performed."
        )
    return tuple(lines)


def _metadata_name(metadata: Any) -> str:
    return _text(getattr(metadata, "name", None))


def _version_prefix_matches(raw: str, version: str) -> bool:
    current = _text(raw).lstrip("v")
    target = parse_k8s_version(version).minor_text
    return current == target or current.startswith(f"{target}.")


def node_group_rollout_complete(node_group: Any, *, version: str) -> bool:
    spec = getattr(node_group, "spec", None)
    if not _version_prefix_matches(_text(getattr(spec, "version", None)), version):
        return False
    status = getattr(node_group, "status", None)
    if status is None:
        return False
    status_version = _text(getattr(status, "version", None))
    if status_version and not _version_prefix_matches(status_version, version):
        return False
    ready = getattr(status, "ready_node_count", None)
    target = getattr(status, "target_node_count", None)
    node_count = getattr(status, "node_count", None)
    outdated = getattr(status, "outdated_node_count", None)
    if isinstance(ready, int) and isinstance(target, int) and ready < target:
        return False
    if isinstance(node_count, int) and isinstance(target, int) and node_count != target:
        return False
    if isinstance(outdated, int) and outdated > 0:
        return False
    return not bool(getattr(status, "reconciling", False))


def _node_group_template_os(node_group: Any) -> str:
    spec = getattr(node_group, "spec", None)
    template = getattr(spec, "template", None)
    return _text(getattr(template, "os", None))


def _node_group_template_layer_value(node_group: Any, *, field: str) -> str:
    spec = getattr(node_group, "spec", None)
    template = getattr(spec, "template", None)
    if field == "platform":
        resources = getattr(template, "resources", None)
        return _text(getattr(resources, "platform", None))
    if field == "preset":
        resources = getattr(template, "resources", None)
        return _text(getattr(resources, "preset", None))
    if field == "drivers_preset":
        gpu_settings = getattr(template, "gpu_settings", None)
        return _text(getattr(gpu_settings, "drivers_preset", None))
    raise ValueError(f"Unsupported live node-group field '{field}'.")


def _node_group_status_ready(node_group: Any) -> bool:
    status = getattr(node_group, "status", None)
    if status is None:
        return False
    ready = getattr(status, "ready_node_count", None)
    target = getattr(status, "target_node_count", None)
    node_count = getattr(status, "node_count", None)
    outdated = getattr(status, "outdated_node_count", None)
    if isinstance(ready, int) and isinstance(target, int) and ready < target:
        return False
    if isinstance(node_count, int) and isinstance(target, int) and node_count != target:
        return False
    if isinstance(outdated, int) and outdated > 0:
        return False
    return not bool(getattr(status, "reconciling", False))


def node_group_os_rollout_complete(node_group: Any, *, os: str) -> bool:
    if _node_group_template_os(node_group) != validate_os_image_value(os):
        return False
    return _node_group_status_ready(node_group)


def node_group_layer_rollout_complete(
    node_group: Any,
    *,
    field: str,
    value: str,
) -> bool:
    if _node_group_template_layer_value(node_group, field=field) != validate_node_layer_value(
        value,
        flag_name="node layer value",
    ):
        return False
    return _node_group_status_ready(node_group)


def node_group_rollout_summary(node_group: Any) -> str:
    metadata = getattr(node_group, "metadata", None)
    status = getattr(node_group, "status", None)
    name = _metadata_name(metadata) or _text(getattr(metadata, "id", None)) or "unknown"
    return (
        f"{name}: spec={_text(getattr(getattr(node_group, 'spec', None), 'version', None)) or 'unknown'}, "
        f"status={_text(getattr(status, 'version', None)) or 'unknown'}, "
        f"ready={getattr(status, 'ready_node_count', None)}/"
        f"{getattr(status, 'target_node_count', None)}, "
        f"nodes={getattr(status, 'node_count', None)}, "
        f"outdated={getattr(status, 'outdated_node_count', None)}, "
        f"reconciling={getattr(status, 'reconciling', None)}"
    )


def node_group_os_rollout_summary(node_group: Any) -> str:
    metadata = getattr(node_group, "metadata", None)
    status = getattr(node_group, "status", None)
    name = _metadata_name(metadata) or _text(getattr(metadata, "id", None)) or "unknown"
    return (
        f"{name}: os={_node_group_template_os(node_group) or 'unknown'}, "
        f"ready={getattr(status, 'ready_node_count', None)}/"
        f"{getattr(status, 'target_node_count', None)}, "
        f"nodes={getattr(status, 'node_count', None)}, "
        f"outdated={getattr(status, 'outdated_node_count', None)}, "
        f"reconciling={getattr(status, 'reconciling', None)}"
    )


def node_group_layer_rollout_summary(node_group: Any, *, field: str) -> str:
    metadata = getattr(node_group, "metadata", None)
    status = getattr(node_group, "status", None)
    name = _metadata_name(metadata) or _text(getattr(metadata, "id", None)) or "unknown"
    return (
        f"{name}: {field}={_node_group_template_layer_value(node_group, field=field) or 'unknown'}, "
        f"ready={getattr(status, 'ready_node_count', None)}/"
        f"{getattr(status, 'target_node_count', None)}, "
        f"nodes={getattr(status, 'node_count', None)}, "
        f"outdated={getattr(status, 'outdated_node_count', None)}, "
        f"reconciling={getattr(status, 'reconciling', None)}"
    )


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def node_group_target_size(group: LiveNodeGroup) -> int:
    """Return the best available target size for sizing node-group rollout waits."""

    status = getattr(group.raw, "status", None)
    for value in (
        getattr(status, "target_node_count", None),
        getattr(status, "node_count", None),
        getattr(status, "ready_node_count", None),
    ):
        if (count := _positive_int(value)) is not None:
            return count
    if group.source is not None:
        for value in (
            group.source.autoscaling_max_node_count,
            group.source.node_count,
            group.source.autoscaling_min_node_count,
        ):
            if (count := _positive_int(value)) is not None:
                return count
    return 1


def node_group_rollout_wait_seconds(group: LiveNodeGroup) -> int:
    """Return the SDK status-watch timeout for one whole node-group rollout."""

    return max(
        MIN_ROLLOUT_WAIT_SECONDS,
        node_group_target_size(group) * ROLLOUT_WAIT_SECONDS_PER_NODE,
    )


def terraform_node_group_strategy_for_policy(
    policy: str, timeout: DrainTimeout
) -> dict[str, Any] | None:
    """Return the Terraform node-group strategy override for a disruption policy."""

    policy = validate_disruption_policy(policy)
    if policy == DISRUPTION_POLICY_SAFE:
        return None
    strategy: dict[str, Any] = {
        "max_surge": {"count": 0},
        "max_unavailable": {"count": 1},
    }
    if timeout.seconds is not None:
        strategy["drain_timeout"] = timeout.label
    return strategy


class Mk8sKubernetesVersionExecutor:
    """SDK adapter for MK8s Kubernetes version upgrades."""

    def __init__(self, sdk: Any):
        from nebius.api.nebius.mk8s.v1 import ClusterServiceClient, NodeGroupServiceClient

        self._cluster_client = ClusterServiceClient(sdk)
        self._node_group_client = NodeGroupServiceClient(sdk)

    def get_cluster(self, cluster_id: str) -> Any:
        from nebius.api.nebius.mk8s.v1 import GetClusterRequest

        return self._cluster_client.get(GetClusterRequest(id=cluster_id)).wait()

    def get_cluster_by_name(self, *, project_id: str, name: str) -> Any:
        from nebius.api.nebius.common.v1 import GetByNameRequest

        return self._cluster_client.get_by_name(
            GetByNameRequest(parent_id=project_id, name=name)
        ).wait()

    def list_node_groups(self, cluster_id: str) -> tuple[Any, ...]:
        from nebius.api.nebius.mk8s.v1 import ListNodeGroupsRequest

        items: list[Any] = []
        token = ""
        while True:
            response = self._node_group_client.list(
                ListNodeGroupsRequest(parent_id=cluster_id, page_token=token or None)
            ).wait()
            items.extend(list(getattr(response, "items", []) or []))
            token = _text(getattr(response, "next_page_token", None))
            if not token:
                return tuple(items)

    def control_plane_versions(self) -> tuple[str, ...]:
        from nebius.api.nebius.mk8s.v1 import ListClusterControlPlaneVersionsRequest

        response = self._cluster_client.list_control_plane_versions(
            ListClusterControlPlaneVersionsRequest()
        ).wait()
        return tuple(
            version
            for item in getattr(response, "items", []) or []
            if (version := _text(getattr(item, "version", None)))
        )

    def compatibility_choices(
        self, *, target_version: str, platform: str
    ) -> tuple[CompatibilityChoice, ...]:
        from nebius.api.nebius.mk8s.v1 import GetNodeGroupCompatibilityMatrixRequest

        response = self._node_group_client.get_compatibility_matrix(
            GetNodeGroupCompatibilityMatrixRequest(
                cluster_kubernetes_version=target_version,
                platform=platform or None,
            )
        ).wait()
        return compatibility_choices_from_response(response, platform=platform)

    def wait_cluster_version(
        self,
        *,
        cluster_id: str,
        version: str,
        timeout_seconds: int = 3600,
        poll_seconds: float = 15.0,
    ) -> Any:
        deadline = time.monotonic() + timeout_seconds
        last_cluster: Any = None
        while True:
            last_cluster = self.get_cluster(cluster_id)
            spec_version = _text(
                getattr(
                    getattr(getattr(last_cluster, "spec", None), "control_plane", None),
                    "version",
                    None,
                )
            )
            if spec_version == version:
                return last_cluster
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for MK8s control plane {cluster_id} to report Kubernetes {version}."
                )
            time.sleep(poll_seconds)

    def wait_node_group_version(
        self,
        *,
        cluster_id: str,
        node_group_id: str,
        version: str,
        timeout_seconds: int = 3600,
        poll_seconds: float = 15.0,
    ) -> Any:
        deadline = time.monotonic() + timeout_seconds
        while True:
            for candidate in self.list_node_groups(cluster_id):
                metadata = getattr(candidate, "metadata", None)
                if _text(getattr(metadata, "id", None)) != node_group_id:
                    continue
                if node_group_rollout_complete(candidate, version=version):
                    return candidate
                last_summary = node_group_rollout_summary(candidate)
                break
            else:
                last_summary = f"node group {node_group_id} was not found"
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Timed out waiting for MK8s node group "
                    f"{node_group_id} to finish Kubernetes {version} rollout: {last_summary}."
                )
            time.sleep(poll_seconds)

    def wait_node_group_os(
        self,
        *,
        cluster_id: str,
        node_group_id: str,
        os: str,
        timeout_seconds: int = 3600,
        poll_seconds: float = 15.0,
    ) -> Any:
        target_os = validate_os_image_value(os)
        deadline = time.monotonic() + timeout_seconds
        while True:
            for candidate in self.list_node_groups(cluster_id):
                metadata = getattr(candidate, "metadata", None)
                if _text(getattr(metadata, "id", None)) != node_group_id:
                    continue
                if node_group_os_rollout_complete(candidate, os=target_os):
                    return candidate
                last_summary = node_group_os_rollout_summary(candidate)
                break
            else:
                last_summary = f"node group {node_group_id} was not found"
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Timed out waiting for MK8s node group "
                    f"{node_group_id} to finish OS image rollout to "
                    f"{target_os}: {last_summary}."
                )
            time.sleep(poll_seconds)

    def wait_node_group_layer(
        self,
        *,
        cluster_id: str,
        node_group_id: str,
        field: str,
        value: str,
        timeout_seconds: int = 3600,
        poll_seconds: float = 15.0,
    ) -> Any:
        target_value = validate_node_layer_value(value, flag_name="node layer value")
        deadline = time.monotonic() + timeout_seconds
        while True:
            for candidate in self.list_node_groups(cluster_id):
                metadata = getattr(candidate, "metadata", None)
                if _text(getattr(metadata, "id", None)) != node_group_id:
                    continue
                if node_group_layer_rollout_complete(
                    candidate,
                    field=field,
                    value=target_value,
                ):
                    return candidate
                last_summary = node_group_layer_rollout_summary(candidate, field=field)
                break
            else:
                last_summary = f"node group {node_group_id} was not found"
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Timed out waiting for MK8s node group "
                    f"{node_group_id} to finish {field} rollout to "
                    f"{target_value}: {last_summary}."
                )
            time.sleep(poll_seconds)


def plan_k8s_upgrade(
    *,
    config_path: str,
    target: UpgradeTarget,
    cluster: Any,
    cluster_id: str,
    source_component: Mapping[str, Any],
    target_version: str,
    disruption_policy: str,
    drain_timeout: DrainTimeout,
    live_node_groups: Sequence[Any],
    compatibility_lookup,
    preflight_findings: Sequence[PreflightFinding] = (),
) -> Mk8sUpgradePlan:
    """Build a Kubernetes version upgrade plan from live SDK objects."""

    cluster_metadata = getattr(cluster, "metadata", None)
    cluster_name = _text(getattr(cluster_metadata, "name", None)) or target.instance_id
    current_version = _text(
        getattr(getattr(getattr(cluster, "spec", None), "control_plane", None), "version", None)
    )
    if not current_version:
        raise ValueError(f"MK8s cluster '{cluster_id}' did not return spec.control_plane.version.")
    hops = require_single_minor_hop(current_version, target_version)

    source_groups = source_node_groups_by_name(source_component)
    live_groups = sort_live_node_groups(
        tuple(
            live_node_group_from_sdk(raw, source=source_groups.get(_node_group_name(raw)))
            for raw in live_node_groups
        )
    )
    higher_live_groups = live_node_groups_above_control_plane_version(
        live_groups,
        control_plane_version=target_version,
    )
    if higher_live_groups:
        target_minor = parse_k8s_version(target_version).minor_text
        details = ", ".join(
            f"{group.name} ({_minor_version_label(group.version)})" for group in higher_live_groups
        )
        raise ValueError(
            "MK8s node groups must not run a Kubernetes minor version above the "
            f"target/control-plane version {target_minor}. Resolve live version "
            f"skew before running the upgrade: {details}."
        )
    failures: list[CompatibilityFailure] = []
    for group in live_groups:
        choices = compatibility_lookup(target_version=target_version, platform=group.platform)
        failures.extend(
            compatibility_failures_for_node_group(
                config_path=config_path,
                target_selector=target.selector,
                target_version=target_version,
                group=group,
                choices=choices,
            )
        )
    warnings: list[str] = []
    if disruption_policy == DISRUPTION_POLICY_SAFE:
        warnings.append(
            "safe mode uses Nebius rolling node replacement and generally requires spare "
            "quota/capacity; this note is always shown for safe mode. cxcli fails "
            "preflight when quota assessment reports a shortage."
        )
    if disruption_policy == DISRUPTION_POLICY_ALLOW_UNAVAILABLE:
        warnings.append(
            "allow-unavailable sets zero surge, one unavailable node, and a finite "
            "drain_timeout; workloads may become unavailable and provider drain fallback "
            "can occur after the timeout."
        )
    if disruption_policy == DISRUPTION_POLICY_FORCE_DELETE:
        warnings.append(
            "force-delete never deletes PVC/PV objects, but it sets a finite Terraform "
            "node-group drain_timeout. If Managed Kubernetes has to delete Pods after "
            "that timeout, applications that need graceful shutdown, single-writer locks, "
            "or coordinated external API updates can lose in-flight state."
        )
    return Mk8sUpgradePlan(
        target=target,
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        current_version=current_version,
        target_version=parse_k8s_version(target_version).minor_text,
        hops=hops,
        disruption_policy=disruption_policy,
        drain_timeout=drain_timeout,
        node_groups=live_groups,
        compatibility_failures=tuple(failures),
        preflight_findings=tuple(preflight_findings),
        warnings=tuple(warnings),
    )


def plan_os_image_upgrade(
    *,
    target: UpgradeTarget,
    cluster: Any,
    cluster_id: str,
    source_component: Mapping[str, Any],
    target_os: str,
    disruption_policy: str,
    drain_timeout: DrainTimeout,
    live_node_groups: Sequence[Any],
    compatibility_lookup,
    node_group: str = "",
    preflight_findings: Sequence[PreflightFinding] = (),
) -> Mk8sOsImageUpgradePlan:
    """Build an OS-image upgrade plan from live SDK objects."""

    target_os = validate_os_image_value(target_os)
    cluster_metadata = getattr(cluster, "metadata", None)
    cluster_name = _text(getattr(cluster_metadata, "name", None)) or target.instance_id
    current_version = _text(
        getattr(getattr(getattr(cluster, "spec", None), "control_plane", None), "version", None)
    )
    if not current_version:
        raise ValueError(f"MK8s cluster '{cluster_id}' did not return spec.control_plane.version.")
    k8s_version = parse_k8s_version(current_version).minor_text
    source_groups = source_node_groups_by_name(source_component)
    live_groups = sort_live_node_groups(
        tuple(
            live_node_group_from_sdk(raw, source=source_groups.get(_node_group_name(raw)))
            for raw in live_node_groups
        )
    )
    selected_groups = select_live_node_groups_for_os_image(
        live_groups,
        node_group=node_group,
    )
    unmanaged = tuple(group.name for group in selected_groups if group.source is None)
    if unmanaged:
        raise ValueError(
            "Live MK8s node groups are not declared in config.yaml, so cxcli cannot "
            "safely upgrade their OS image through Terraform: " + ", ".join(unmanaged)
        )
    failures: list[CompatibilityFailure] = []
    for group in selected_groups:
        choices = compatibility_lookup(target_version=k8s_version, platform=group.platform)
        failures.extend(
            os_image_compatibility_failures_for_node_group(
                target_version=k8s_version,
                target_os=target_os,
                group=group,
                choices=choices,
            )
        )
    warnings: list[str] = []
    if disruption_policy == DISRUPTION_POLICY_SAFE:
        warnings.append(
            "safe mode uses Nebius rolling node replacement and generally requires spare "
            "quota/capacity; this note is always shown for safe mode. cxcli fails "
            "preflight when quota assessment reports a shortage."
        )
    if disruption_policy == DISRUPTION_POLICY_ALLOW_UNAVAILABLE:
        warnings.append(
            "allow-unavailable sets zero surge, one unavailable node, and a finite "
            "drain_timeout; workloads may become unavailable and provider drain fallback "
            "can occur after the timeout."
        )
    if disruption_policy == DISRUPTION_POLICY_FORCE_DELETE:
        warnings.append(
            "force-delete never deletes PVC/PV objects, but it sets a finite Terraform "
            "node-group drain_timeout. If Managed Kubernetes has to delete Pods after "
            "that timeout, applications that need graceful shutdown, single-writer locks, "
            "or coordinated external API updates can lose in-flight state."
        )
    return Mk8sOsImageUpgradePlan(
        target=target,
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        k8s_version=k8s_version,
        target_os=target_os,
        disruption_policy=disruption_policy,
        drain_timeout=drain_timeout,
        node_groups=selected_groups,
        compatibility_failures=tuple(failures),
        preflight_findings=tuple(preflight_findings),
        warnings=tuple(warnings),
    )


def _node_layer_warning_lines(disruption_policy: str) -> tuple[str, ...]:
    warnings: list[str] = []
    if disruption_policy == DISRUPTION_POLICY_SAFE:
        warnings.append(
            "safe mode uses Nebius rolling node replacement and generally requires spare "
            "quota/capacity; this note is always shown for safe mode. cxcli fails "
            "preflight when quota assessment reports a shortage."
        )
    if disruption_policy == DISRUPTION_POLICY_ALLOW_UNAVAILABLE:
        warnings.append(
            "allow-unavailable sets zero surge, one unavailable node, and a finite "
            "drain_timeout; workloads may become unavailable and provider drain fallback "
            "can occur after the timeout."
        )
    if disruption_policy == DISRUPTION_POLICY_FORCE_DELETE:
        warnings.append(
            "force-delete never deletes PVC/PV objects, but it sets a finite Terraform "
            "node-group drain_timeout. If Managed Kubernetes has to delete Pods after "
            "that timeout, applications that need graceful shutdown, single-writer locks, "
            "or coordinated external API updates can lose in-flight state."
        )
    return tuple(warnings)


def plan_node_layer_upgrade(
    *,
    target: UpgradeTarget,
    cluster: Any,
    cluster_id: str,
    source_component: Mapping[str, Any],
    spec: NodeLayerUpgradeSpec,
    target_value: str,
    disruption_policy: str,
    drain_timeout: DrainTimeout,
    live_node_groups: Sequence[Any],
    compatibility_lookup,
    node_group: str = "",
    preflight_findings: Sequence[PreflightFinding] = (),
) -> Mk8sNodeLayerUpgradePlan:
    """Build a node-template layer upgrade plan from live SDK objects."""

    target_value = validate_node_layer_value(
        target_value,
        flag_name="--to-platform" if spec.source_field == "platform" else "--to-preset",
    )
    cluster_metadata = getattr(cluster, "metadata", None)
    cluster_name = _text(getattr(cluster_metadata, "name", None)) or target.instance_id
    current_version = _text(
        getattr(getattr(getattr(cluster, "spec", None), "control_plane", None), "version", None)
    )
    if not current_version:
        raise ValueError(f"MK8s cluster '{cluster_id}' did not return spec.control_plane.version.")
    k8s_version = parse_k8s_version(current_version).minor_text
    source_groups = source_node_groups_by_name(source_component)
    live_groups = sort_live_node_groups(
        tuple(
            live_node_group_from_sdk(raw, source=source_groups.get(_node_group_name(raw)))
            for raw in live_node_groups
        )
    )
    selected_groups = select_live_node_groups_for_node_layer(
        live_groups,
        node_group=node_group,
        group_filter=spec.group_filter,
        command=spec.command,
    )
    unmanaged = tuple(group.name for group in selected_groups if group.source is None)
    if unmanaged:
        raise ValueError(
            "Live MK8s node groups are not declared in config.yaml, so cxcli cannot "
            f"safely upgrade their {spec.target_label} through Terraform: " + ", ".join(unmanaged)
        )
    failures: list[CompatibilityFailure] = []
    for group in selected_groups:
        choices: Sequence[CompatibilityChoice] = ()
        if spec.live_field in {"platform", "drivers_preset"}:
            matrix_platform = target_value if spec.live_field == "platform" else group.platform
            choices = compatibility_lookup(
                target_version=k8s_version,
                platform=matrix_platform,
            )
        failures.extend(
            node_layer_compatibility_failures_for_node_group(
                k8s_version=k8s_version,
                group=group,
                spec=spec,
                target_value=target_value,
                choices=choices,
            )
        )
    return Mk8sNodeLayerUpgradePlan(
        target=target,
        cluster_id=cluster_id,
        cluster_name=cluster_name,
        k8s_version=k8s_version,
        spec=spec,
        target_value=target_value,
        disruption_policy=disruption_policy,
        drain_timeout=drain_timeout,
        node_groups=selected_groups,
        compatibility_failures=tuple(failures),
        preflight_findings=tuple(preflight_findings),
        warnings=_node_layer_warning_lines(disruption_policy),
    )


def wait_for_node_group_rollout(
    *,
    executor: Mk8sKubernetesVersionExecutor,
    plan: Mk8sUpgradePlan,
    planned_group: LiveNodeGroup,
) -> None:
    """Wait for one Terraform-requested node-group rollout to finish."""

    executor.wait_node_group_version(
        cluster_id=plan.cluster_id,
        node_group_id=planned_group.id,
        version=plan.target_version,
        timeout_seconds=node_group_rollout_wait_seconds(planned_group),
    )


def wait_for_os_image_rollout(
    *,
    executor: Mk8sKubernetesVersionExecutor,
    plan: Mk8sOsImageUpgradePlan,
    planned_group: LiveNodeGroup,
) -> None:
    """Wait for one Terraform-requested node-group OS-image rollout to finish."""

    executor.wait_node_group_os(
        cluster_id=plan.cluster_id,
        node_group_id=planned_group.id,
        os=plan.target_os,
        timeout_seconds=node_group_rollout_wait_seconds(planned_group),
    )


def wait_for_node_layer_rollout(
    *,
    executor: Mk8sKubernetesVersionExecutor,
    plan: Mk8sNodeLayerUpgradePlan,
    planned_group: LiveNodeGroup,
) -> None:
    """Wait for one Terraform-requested node-template layer rollout to finish."""

    executor.wait_node_group_layer(
        cluster_id=plan.cluster_id,
        node_group_id=planned_group.id,
        field=plan.spec.live_field,
        value=plan.target_value,
        timeout_seconds=node_group_rollout_wait_seconds(planned_group),
    )
