"""Pure helpers for external Soperator upgrade campaigns."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .runtime_config import to_plain_data

SOPERATOR_UPGRADE_CAMPAIGN_SCHEMA = "nebius-cxcli-ext-soperator-upgrade-campaign/v4"

_FINGERPRINT_EXCLUDED_KEYS = frozenset({"campaign_id", "created_at", "fingerprint"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


@dataclass(frozen=True)
class _K8sMinor:
    major: int
    minor: int

    @property
    def minor_text(self) -> str:
        return f"{self.major}.{self.minor}"


def _parse_k8s_minor(value: Any) -> _K8sMinor:
    text = _text(value).removeprefix("v")
    match = re.fullmatch(r"(?P<major>[0-9]+)\.(?P<minor>[0-9]+)(?:\.[0-9]+)?", text)
    if match is None:
        raise ValueError(
            f"Invalid Kubernetes version '{value}'. Expected major.minor or major.minor.patch."
        )
    return _K8sMinor(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
    )


def campaign_semantic_payload(campaign: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable desired material covered by the campaign fingerprint."""

    plain = to_plain_data(dict(campaign))
    payload = dict(plain) if isinstance(plain, Mapping) else {}
    for key in _FINGERPRINT_EXCLUDED_KEYS:
        payload.pop(key, None)
    return payload


def soperator_upgrade_campaign_fingerprint(campaign: Mapping[str, Any]) -> str:
    payload = campaign_semantic_payload(campaign)
    stable = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def finalize_soperator_upgrade_campaign(
    campaign: Mapping[str, Any],
    *,
    created_at: str = "",
) -> dict[str, Any]:
    """Attach deterministic identity to a compiled v4 campaign."""

    plain = to_plain_data(dict(campaign))
    result = dict(plain) if isinstance(plain, Mapping) else {}
    result["schema"] = SOPERATOR_UPGRADE_CAMPAIGN_SCHEMA
    result["locked"] = True
    fingerprint = soperator_upgrade_campaign_fingerprint(result)
    result["campaign_id"] = f"campaign-{fingerprint[:16]}"
    result["fingerprint"] = fingerprint
    if created_at:
        result["created_at"] = created_at
    return result


def provider_control_plane_versions(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    provider = _mapping(snapshot.get("provider"))
    raw_versions = provider.get("control_plane_versions")
    if not isinstance(raw_versions, Sequence) or isinstance(raw_versions, (str, bytes, bytearray)):
        return ()
    versions: dict[tuple[int, int], str] = {}
    for raw_version in raw_versions:
        try:
            parsed = _parse_k8s_minor(raw_version)
        except ValueError:
            continue
        versions[(parsed.major, parsed.minor)] = parsed.minor_text
    return tuple(versions[key] for key in sorted(versions))


def validated_control_plane_path(
    snapshot: Mapping[str, Any],
    *,
    current_version: str,
    target_version: str,
) -> tuple[str, ...]:
    """Return a contiguous provider-supported path, or fail closed."""

    current = _parse_k8s_minor(current_version)
    target = _parse_k8s_minor(target_version)
    if current.major != target.major or current.minor > target.minor:
        raise ValueError(
            "External Soperator campaign requires a non-downgrade Kubernetes path "
            f"within one major version: {current.minor_text} -> {target.minor_text}."
        )
    available = set(provider_control_plane_versions(snapshot))
    if not available:
        raise ValueError(
            "Nebius MK8s did not return any live control-plane versions; the upgrade "
            "campaign cannot be compiled safely."
        )
    path = tuple(f"{current.major}.{minor}" for minor in range(current.minor, target.minor + 1))
    missing = tuple(version for version in path[1:] if version not in available)
    if missing:
        raise ValueError(
            "Nebius MK8s does not expose every required control-plane hop. Missing: "
            + ", ".join(missing)
            + "."
        )
    return path


def _compatibility_choices(
    snapshot: Mapping[str, Any],
    *,
    version: str,
    platform: str,
) -> tuple[Mapping[str, Any], ...]:
    provider = _mapping(snapshot.get("provider"))
    matrix = _mapping(provider.get("compatibility_matrix"))
    by_platform = _mapping(matrix.get(version))
    raw_choices = by_platform.get(platform)
    if raw_choices is None and not platform:
        raw_choices = by_platform.get("_")
    choices = _sequence_of_mappings(raw_choices)
    return tuple(
        choice
        for choice in choices
        if not _text(choice.get("platform"))
        or not platform
        or _text(choice.get("platform")) == platform
    )


def _preference_index(value: str, preferences: Sequence[str]) -> int:
    try:
        return tuple(preferences).index(value)
    except ValueError:
        return len(tuple(preferences)) + 1


def _compatible_tuple_candidates(
    *,
    choices: Sequence[Mapping[str, Any]],
    gpu_software_mode: str,
    preferred_os: Sequence[str],
    preferred_drivers_presets: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    normalized = {
        (
            _text(choice.get("os")),
            _text(choice.get("drivers_preset")),
        )
        for choice in choices
        if _text(choice.get("os"))
    }
    if gpu_software_mode == "provider-managed":
        normalized = {choice for choice in normalized if choice[1]}
    else:
        # The provider matrix is authoritative for the OS/platform edge even
        # when its published tuple includes provider-managed GPU software.
        # Operator-managed and non-GPU groups deliberately leave
        # drivers_preset empty, so retain the compatible OS edge without
        # inheriting the provider driver choice.
        normalized = {(os_name, "") for os_name, _drivers_preset in normalized}
    if preferred_os:
        normalized = {choice for choice in normalized if choice[0] in set(preferred_os)}
    if gpu_software_mode == "provider-managed" and preferred_drivers_presets:
        normalized = {
            choice for choice in normalized if choice[1] in set(preferred_drivers_presets)
        }
    return tuple(
        sorted(
            normalized,
            key=lambda choice: (
                _preference_index(choice[0], preferred_os),
                _preference_index(choice[1], preferred_drivers_presets),
                choice[0],
                choice[1],
            ),
        )
    )


def _minimum_churn_tuple_path(
    *,
    initial: tuple[str, str],
    candidates_by_hop: Sequence[Sequence[tuple[str, str]]],
    preferred_os: Sequence[str],
    preferred_drivers_presets: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    """Choose the complete tuple path with minimum replacements, then policy rank."""

    # Each value is ordered by replacement count first, then cumulative catalog
    # preference rank. The full tuple path is the final deterministic tie-breaker.
    states: dict[
        tuple[str, str],
        tuple[int, int, int, tuple[tuple[str, str], ...]],
    ] = {initial: (0, 0, 0, ())}
    for candidates in candidates_by_hop:
        next_states: dict[
            tuple[str, str],
            tuple[int, int, int, tuple[tuple[str, str], ...]],
        ] = {}
        for candidate in candidates:
            os_rank = _preference_index(candidate[0], preferred_os)
            driver_rank = _preference_index(candidate[1], preferred_drivers_presets)
            for previous, state in states.items():
                candidate_path = (*state[3], candidate)
                candidate_state = (
                    state[0] + int(previous != candidate),
                    state[1] + os_rank,
                    state[2] + driver_rank,
                    candidate_path,
                )
                current = next_states.get(candidate)
                if current is None or candidate_state < current:
                    next_states[candidate] = candidate_state
        states = next_states
    if not states:
        return ()
    return min(states.values())[3]


def _available_tuple_labels(choices: Sequence[Mapping[str, Any]]) -> str:
    return (
        ", ".join(
            sorted(
                {
                    f"{_text(choice.get('os'))}/"
                    f"{_text(choice.get('drivers_preset')) or 'driverless'}"
                    for choice in choices
                }
            )
        )
        or "none"
    )


def campaign_node_group_role(group_key: str, group: Mapping[str, Any]) -> str:
    """Return the stable campaign role derived from one live node-group row."""

    explicit = _text(group.get("role"))
    if explicit:
        return explicit
    labels = _mapping(group.get("labels"))
    for label_key in (
        "slurm.nebius.ai/role",
        "slurm.nebius.ai/nodeset-name",
        "slurm.nebius.ai/nodeset",
    ):
        value = _text(labels.get(label_key))
        if value:
            return value
    normalized = re.sub(r"[^a-z0-9]+", "-", group_key.lower()).strip("-")
    for role in ("system", "controller", "login", "accounting"):
        if role in normalized:
            return role
    return normalized if normalized.startswith("worker") else "worker"


def campaign_node_group_gpu_software_mode(group: Mapping[str, Any]) -> str:
    """Return the host GPU software ownership mode used by campaign identity checks."""

    provider = _mapping(group.get("provider"))
    template = _mapping(provider.get("node_template"))
    platform = _text(template.get("platform"))
    drivers_preset = _text(template.get("gpu_stack_preset") or template.get("drivers_preset"))
    gpu = bool(group.get("gpu")) or platform.startswith("gpu-")
    if not gpu:
        return "none"
    return "provider-managed" if drivers_preset else "operator-managed"


def compile_node_group_hop_targets(
    snapshot: Mapping[str, Any],
    *,
    control_plane_path: Sequence[str],
    preferred_os: Sequence[str],
    preferred_drivers_presets: Sequence[str],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Compile exact per-node-group tuples for every control-plane hop."""

    groups = _mapping(snapshot.get("node_groups"))
    if len(control_plane_path) > 1 and not groups:
        raise ValueError("External Soperator campaign planning found no live MK8s node groups.")
    campaign_source_version = (
        _parse_k8s_minor(control_plane_path[0]).minor_text if control_plane_path else ""
    )
    result: dict[tuple[str, str], list[dict[str, Any]]] = {
        (source, target): []
        for source, target in zip(control_plane_path, control_plane_path[1:], strict=False)
    }
    for group_key, raw_group in sorted(groups.items(), key=lambda item: str(item[0])):
        if not isinstance(raw_group, Mapping):
            raise ValueError(
                f"External Soperator campaign node group '{group_key}' is not an object."
            )
        provider = _mapping(raw_group.get("provider"))
        template = _mapping(provider.get("node_template"))
        node_group_id = _text(provider.get("node_group_id")) or _text(
            raw_group.get("node_group_id")
        )
        node_group_name = (
            _text(provider.get("node_group_name"))
            or _text(raw_group.get("node_group_name"))
            or str(group_key)
        )
        platform = _text(template.get("platform"))
        preset = _text(template.get("preset"))
        raw_current_version = _text(template.get("k8s_version"))
        if not raw_current_version:
            raise ValueError(
                "External Soperator campaign cannot lock an incomplete node-group "
                f"identity for '{node_group_name}': missing source Kubernetes version."
            )
        try:
            current_version = _parse_k8s_minor(raw_current_version).minor_text
        except ValueError as exc:
            raise ValueError(
                "External Soperator campaign found an invalid live Kubernetes version "
                f"for node group '{node_group_name}': {raw_current_version or 'missing'}."
            ) from exc
        if campaign_source_version and current_version != campaign_source_version:
            raise ValueError(
                "External Soperator campaign requires every source node group to match "
                f"the control-plane source Kubernetes version {campaign_source_version}; "
                f"node group '{node_group_name}' is {current_version}. Reconcile the mixed "
                "node-group version before onboarding."
            )
        current_os = _text(template.get("os"))
        current_driver = _text(template.get("gpu_stack_preset") or template.get("drivers_preset"))
        gpu_mode = campaign_node_group_gpu_software_mode(raw_group)
        missing_identity = tuple(
            field
            for field, value in (
                ("stable provider id", node_group_id),
                ("platform", platform),
                ("hardware preset", preset),
                ("source OS", current_os),
            )
            if not value
        )
        if missing_identity:
            raise ValueError(
                "External Soperator campaign cannot lock an incomplete node-group "
                f"identity for '{node_group_name}': missing " + ", ".join(missing_identity) + "."
            )
        hop_pairs = tuple(zip(control_plane_path, control_plane_path[1:], strict=False))
        candidates_by_hop: list[tuple[tuple[str, str], ...]] = []
        for _hop_source, hop_target in hop_pairs:
            choices = _compatibility_choices(
                snapshot,
                version=hop_target,
                platform=platform,
            )
            candidates = _compatible_tuple_candidates(
                choices=choices,
                gpu_software_mode=gpu_mode,
                preferred_os=preferred_os,
                preferred_drivers_presets=preferred_drivers_presets,
            )
            if not candidates:
                raise ValueError(
                    "No policy-approved Nebius MK8s compatibility tuple exists for "
                    f"node group '{node_group_name}', Kubernetes {hop_target}, and "
                    f"platform '{platform or 'unspecified'}'. Available tuples: "
                    f"{_available_tuple_labels(choices)}."
                )
            candidates_by_hop.append(candidates)
        selected_path = _minimum_churn_tuple_path(
            initial=(current_os, current_driver),
            candidates_by_hop=candidates_by_hop,
            preferred_os=preferred_os,
            preferred_drivers_presets=preferred_drivers_presets,
        )
        source_tuple = {
            "kubernetes_version": current_version,
            "os": current_os,
            "drivers_preset": current_driver,
        }
        for (hop_source, hop_target), (selected_os, selected_driver) in zip(
            hop_pairs,
            selected_path,
            strict=True,
        ):
            target_tuple = {
                "kubernetes_version": hop_target,
                "os": selected_os,
                "drivers_preset": selected_driver,
            }
            result[(hop_source, hop_target)].append(
                {
                    "id": node_group_id,
                    "name": node_group_name,
                    "role": campaign_node_group_role(str(group_key), raw_group),
                    "platform": platform,
                    "preset": preset,
                    "gpu_software_mode": gpu_mode,
                    "source": copy.deepcopy(source_tuple),
                    "target": copy.deepcopy(target_tuple),
                    "compatibility_source": "nebius-sdk-get-compatibility-matrix",
                }
            )
            source_tuple = target_tuple
    return result


def project_campaign_node_groups_for_replacements(
    planned_groups: Sequence[Mapping[str, Any]],
    *,
    replacement_bindings: Sequence[Mapping[str, Any]] = (),
    retired_node_group_ids: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Project config-owned tuples onto journal-bound replacement identities.

    The operation journal owns only the old-to-new resource identity binding. All
    desired platform, preset, GPU ownership, and source/target tuple fields remain
    copied from the immutable campaign row for ``original_node_group_id``.
    """

    planned = [dict(copy.deepcopy(to_plain_data(dict(group)))) for group in planned_groups]
    planned_by_id: dict[str, dict[str, Any]] = {}
    for group in planned:
        group_id = _text(group.get("id"))
        if not group_id:
            raise ValueError("Campaign node-group record has no stable provider id.")
        if group_id in planned_by_id:
            raise ValueError(f"Campaign node-group id '{group_id}' is duplicated.")
        planned_by_id[group_id] = group

    retired_ids = {_text(group_id) for group_id in retired_node_group_ids if _text(group_id)}
    unknown_retired = sorted(retired_ids - set(planned_by_id))
    if unknown_retired:
        raise ValueError(
            "Journal retires node-group identities that are absent from the immutable "
            "campaign segment: " + ", ".join(unknown_retired) + "."
        )

    projected: dict[str, dict[str, Any]] = {
        group_id: copy.deepcopy(group)
        for group_id, group in planned_by_id.items()
        if group_id not in retired_ids
    }
    replacement_ids: set[str] = set()
    replacement_names: set[str] = set()
    for raw_binding in replacement_bindings:
        original_id = _text(raw_binding.get("original_node_group_id"))
        original_name = _text(raw_binding.get("original_node_group_name"))
        replacement_id = _text(raw_binding.get("replacement_node_group_id"))
        replacement_name = _text(raw_binding.get("replacement_node_group_name"))
        replacement_role = _text(raw_binding.get("replacement_role"))
        missing = [
            field
            for field, value in (
                ("original_node_group_id", original_id),
                ("replacement_node_group_id", replacement_id),
                ("replacement_node_group_name", replacement_name),
                ("replacement_role", replacement_role),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Journal replacement binding is incomplete; missing " + ", ".join(missing) + "."
            )
        original = planned_by_id.get(original_id)
        if original is None:
            raise ValueError(
                "Journal replacement binding references an original node group absent "
                f"from the immutable campaign segment: {original_id}."
            )
        planned_name = _text(original.get("name"))
        if original_name and planned_name and original_name != planned_name:
            raise ValueError(
                "Journal replacement binding original name conflicts with the immutable "
                f"campaign for {original_id}: journal={original_name}, "
                f"campaign={planned_name}."
            )
        if replacement_id in replacement_ids or (
            replacement_id in planned_by_id and replacement_id != original_id
        ):
            raise ValueError(
                "Journal replacement node-group id collides with another effective "
                f"campaign identity: {replacement_id}."
            )
        if replacement_name in replacement_names:
            raise ValueError(
                "Journal replacement node-group name is duplicated: " + replacement_name + "."
            )

        replacement = copy.deepcopy(original)
        replacement["id"] = replacement_id
        replacement["name"] = replacement_name
        replacement["role"] = replacement_role
        projected[replacement_id] = replacement
        replacement_ids.add(replacement_id)
        replacement_names.add(replacement_name)

    return sorted(
        projected.values(),
        key=lambda group: (_text(group.get("id")), _text(group.get("name"))),
    )


def effective_campaign_segment_for_replacements(
    segment: Mapping[str, Any],
    *,
    replacement_bindings: Sequence[Mapping[str, Any]] = (),
    retired_node_group_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Return an in-memory segment view for the journal-bound live inventory."""

    effective = dict(copy.deepcopy(to_plain_data(dict(segment))))
    mk8s = effective.get("mk8s")
    if not isinstance(mk8s, dict):
        return effective
    raw_groups = mk8s.get("node_groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        return effective
    mk8s["node_groups"] = project_campaign_node_groups_for_replacements(
        _sequence_of_mappings(raw_groups),
        replacement_bindings=replacement_bindings,
        retired_node_group_ids=retired_node_group_ids,
    )
    return effective


def journal_node_group_replacement_transitions(
    checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    """Extract exact replacement bindings and retirements from a v4 journal."""

    phase_states: list[Mapping[str, Any]] = []
    current_phase_state = checkpoint.get("phase_state")
    if isinstance(current_phase_state, Mapping):
        phase_states.append(current_phase_state)
    segment_state = checkpoint.get("segment_state")
    if isinstance(segment_state, Mapping):
        for segment in segment_state.values():
            evidence = segment.get("operation_evidence") if isinstance(segment, Mapping) else None
            phase_state = evidence.get("phase_state") if isinstance(evidence, Mapping) else None
            if isinstance(phase_state, Mapping):
                phase_states.append(phase_state)

    added: dict[str, dict[str, Any]] = {}
    bindings: dict[str, dict[str, str]] = {}
    retired_ids: set[str] = set()
    for phase_state in phase_states:
        rolling = phase_state.get("rolling-compute-migration")
        if isinstance(rolling, Mapping):
            target_groups = rolling.get("target_node_groups")
            if isinstance(target_groups, Mapping):
                for role, raw_state in target_groups.items():
                    if not isinstance(raw_state, Mapping) or raw_state.get("created") is not True:
                        continue
                    group_id = _text(raw_state.get("id"))
                    waypoint = raw_state.get("live_waypoint")
                    binding = raw_state.get("replacement_binding")
                    if (
                        not group_id
                        or not isinstance(waypoint, Mapping)
                        or not isinstance(binding, Mapping)
                    ):
                        raise RuntimeError(
                            "recovery-required: rolling-compute replacement node-group "
                            f"binding is incomplete for {role}."
                        )
                    operation = raw_state.get("operation")
                    if (
                        not isinstance(operation, Mapping)
                        or _text(operation.get("attempt_state"))
                        not in {"provider-terminal", "verified"}
                        or not _text(operation.get("provider_operation_id"))
                    ):
                        raise RuntimeError(
                            "recovery-required: rolling-compute replacement node group "
                            f"{group_id} lacks terminal provider-create evidence."
                        )
                    normalized_waypoint = dict(copy.deepcopy(to_plain_data(dict(waypoint))))
                    binding_row = {
                        "original_node_group_id": _text(binding.get("original_node_group_id")),
                        "original_node_group_name": _text(binding.get("original_node_group_name")),
                        "replacement_node_group_id": _text(
                            binding.get("replacement_node_group_id")
                        ),
                        "replacement_node_group_name": _text(
                            binding.get("replacement_node_group_name")
                        ),
                        "replacement_role": _text(binding.get("replacement_role")),
                    }
                    if not all(binding_row.values()):
                        raise RuntimeError(
                            "recovery-required: rolling-compute original-to-live node-group "
                            f"binding is incomplete for {role}."
                        )
                    if (
                        binding_row["replacement_node_group_id"] != group_id
                        or _text(normalized_waypoint.get("id")) != group_id
                        or binding_row["replacement_node_group_name"]
                        != _text(normalized_waypoint.get("name"))
                        or binding_row["replacement_role"] != _text(normalized_waypoint.get("role"))
                    ):
                        raise RuntimeError(
                            "recovery-required: rolling-compute replacement journal "
                            f"identity conflicts for {group_id}."
                        )
                    prior_waypoint = added.get(group_id)
                    prior_binding = bindings.get(group_id)
                    if prior_waypoint is not None and prior_waypoint != normalized_waypoint:
                        raise RuntimeError(
                            "recovery-required: conflicting replacement node-group waypoints "
                            f"exist for {group_id}."
                        )
                    if prior_binding is not None and prior_binding != binding_row:
                        raise RuntimeError(
                            "recovery-required: conflicting original-to-live node-group "
                            f"bindings exist for {group_id}."
                        )
                    added[group_id] = normalized_waypoint
                    bindings[group_id] = binding_row

        retirement = phase_state.get("retire-old-resources")
        if not isinstance(retirement, Mapping):
            continue
        operations = retirement.get("node_group_operations")
        for retired in retirement.get("retired_node_groups", []) or []:
            if not isinstance(retired, Mapping):
                continue
            group_id = _text(retired.get("node_group_id"))
            group_state = operations.get(group_id) if isinstance(operations, Mapping) else None
            delete_state = group_state.get("delete") if isinstance(group_state, Mapping) else None
            operation = delete_state.get("operation") if isinstance(delete_state, Mapping) else None
            if (
                not group_id
                or not isinstance(operation, Mapping)
                or _text(operation.get("attempt_state")) not in {"provider-terminal", "verified"}
                or not _text(operation.get("provider_operation_id"))
            ):
                raise RuntimeError(
                    "recovery-required: retired node-group binding lacks terminal provider "
                    f"delete evidence for {group_id or '<missing-id>'}."
                )
            retired_ids.add(group_id)
    if retired_ids.intersection(added):
        raise RuntimeError(
            "recovery-required: the same journal-bound node-group identity is both a "
            "replacement and retired."
        )
    return {
        "added": [added[group_id] for group_id in sorted(added)],
        "bindings": [bindings[group_id] for group_id in sorted(bindings)],
        "retired_ids": sorted(retired_ids),
    }


def _condition_is_true(resource: Mapping[str, Any], condition_type: str) -> bool:
    status = _mapping(resource.get("status"))
    for condition in _sequence_of_mappings(status.get("conditions")):
        if _text(condition.get("type")).lower() == condition_type.lower():
            return _text(condition.get("status")).lower() == "true"
    return False


def _provider_node_group_id(group_key: Any, group: Mapping[str, Any]) -> str:
    provider = _mapping(group.get("provider"))
    return (
        _text(provider.get("node_group_id"))
        or _text(group.get("node_group_id"))
        or _text(group.get("id"))
        or _text(group_key)
    )


def _final_campaign_node_groups(campaign: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    by_id: dict[str, Mapping[str, Any]] = {}
    for group in _sequence_of_mappings(_mapping(campaign.get("mk8s")).get("node_groups")):
        group_id = _text(group.get("id"))
        if group_id:
            by_id[group_id] = group
    return tuple(by_id[group_id] for group_id in sorted(by_id))


def _workload_health_conflicts(snapshot: Mapping[str, Any]) -> list[str]:
    resources = _sequence_of_mappings(snapshot.get("soperator_namespace_resources"))
    controllers = [
        resource
        for resource in resources
        if _text(resource.get("kind")) in {"Deployment", "StatefulSet", "DaemonSet"}
    ]
    conflicts: list[str] = []
    if not controllers:
        conflicts.append("kubernetes.workloads.missing")
    for resource in controllers:
        kind = _text(resource.get("kind"))
        name = _text(_mapping(resource.get("metadata")).get("name")) or "unknown"
        status = _mapping(resource.get("status"))
        if kind in {"Deployment", "StatefulSet"}:
            spec_replicas = _mapping(resource.get("spec")).get("replicas")
            desired = spec_replicas if isinstance(spec_replicas, int) else status.get("replicas")
            ready = status.get("readyReplicas")
            updated = status.get("updatedReplicas", ready)
            if (
                not isinstance(desired, int)
                or not isinstance(ready, int)
                or not isinstance(updated, int)
                or ready != desired
                or updated != desired
            ):
                conflicts.append(f"kubernetes.workloads[{kind}/{name}].not-ready")
        else:
            desired = status.get("desiredNumberScheduled")
            ready = status.get("numberReady")
            updated = status.get("updatedNumberScheduled")
            unavailable = status.get("numberUnavailable", 0)
            if (
                not isinstance(desired, int)
                or not isinstance(ready, int)
                or not isinstance(updated, int)
                or ready != desired
                or updated != desired
                or unavailable not in {0, None}
            ):
                conflicts.append(f"kubernetes.workloads[{kind}/{name}].not-ready")
    for pod in (resource for resource in resources if _text(resource.get("kind")) == "Pod"):
        name = _text(_mapping(pod.get("metadata")).get("name")) or "unknown"
        phase = _text(_mapping(pod.get("status")).get("phase"))
        if phase == "Succeeded":
            continue
        if phase != "Running" or not _condition_is_true(pod, "Ready"):
            conflicts.append(f"kubernetes.workloads[Pod/{name}].not-ready")
    return conflicts


def external_soperator_final_health_conflicts(
    *,
    campaign: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    effective_node_groups: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, ...]:
    """Return fresh final-health failures; journal history is never health evidence."""

    conflicts: list[str] = []
    if _sequence_of_mappings(snapshot.get("collection_errors")):
        conflicts.append("discovery.collection_errors")

    provider = _mapping(snapshot.get("provider"))
    cluster = _mapping(provider.get("mk8s_cluster"))
    expected_k8s = _text(_mapping(campaign.get("final_targets")).get("kubernetes"))
    if (
        not cluster
        or cluster.get("operation_terminal") is not True
        or _text(cluster.get("provider_state")).upper() != "RUNNING"
        or cluster.get("provider_reconciling") is True
    ):
        conflicts.append("mk8s.control_plane.provider-not-terminal")
    if expected_k8s and _text(cluster.get("control_plane_version")) != expected_k8s:
        conflicts.append("mk8s.control_plane.version")

    planned_groups = tuple(effective_node_groups) or _final_campaign_node_groups(campaign)
    live_groups = _mapping(snapshot.get("node_groups"))
    live_by_id: dict[str, Mapping[str, Any]] = {}
    for group_key, group in live_groups.items():
        if not isinstance(group, Mapping):
            continue
        group_id = _provider_node_group_id(group_key, group)
        if group_id and group_id not in live_by_id:
            live_by_id[group_id] = group
    for planned in planned_groups:
        group_id = _text(planned.get("id"))
        live = live_by_id.get(group_id)
        if live is None:
            conflicts.append(f"mk8s.node_groups[{group_id or 'unknown'}].missing")
            continue
        group_provider = _mapping(live.get("provider"))
        counts = (
            group_provider.get("target_node_count"),
            group_provider.get("node_count"),
            group_provider.get("ready_node_count"),
            group_provider.get("outdated_node_count"),
        )
        if (
            group_provider.get("operation_terminal") is not True
            or _text(group_provider.get("provider_state")).upper() != "RUNNING"
            or group_provider.get("provider_reconciling") is True
            or not all(isinstance(value, int) for value in counts)
            or counts[0] != counts[1]
            or counts[0] != counts[2]
            or counts[3] != 0
        ):
            conflicts.append(f"mk8s.node_groups[{group_id}].provider-not-ready")

    kubernetes_nodes = _sequence_of_mappings(snapshot.get("kubernetes_nodes"))
    expected_live_nodes = sum(
        int(_mapping(live_by_id.get(_text(group.get("id")))).get("node_count", 0) or 0)
        for group in planned_groups
    )
    if expected_live_nodes > 0 and not kubernetes_nodes:
        conflicts.append("kubernetes.nodes.missing")
    for node in kubernetes_nodes:
        name = _text(_mapping(node.get("metadata")).get("name")) or "unknown"
        if _mapping(node.get("spec")).get("unschedulable") is True or not _condition_is_true(
            node,
            "Ready",
        ):
            conflicts.append(f"kubernetes.nodes[{name}].not-ready")

    conflicts.extend(_workload_health_conflicts(snapshot))
    if not any(
        _text(release.get("status")).lower() == "deployed"
        and "soperator" in _text(release.get("name")).lower()
        for release in _sequence_of_mappings(snapshot.get("helm_releases"))
    ):
        conflicts.append("soperator.helm-not-ready")
    slurm_uid = _text(_mapping(campaign.get("identity")).get("slurmcluster_uid"))
    slurm_clusters = [
        resource
        for resource in _sequence_of_mappings(snapshot.get("soperator_resources"))
        if _text(resource.get("kind")) == "SlurmCluster"
    ]
    matching_slurm = [
        resource
        for resource in slurm_clusters
        if not slurm_uid or _text(_mapping(resource.get("metadata")).get("uid")) == slurm_uid
    ]
    if not matching_slurm or not all(
        _text(_mapping(resource.get("status")).get("phase")) == "Available"
        for resource in matching_slurm
    ):
        conflicts.append("soperator.slurmcluster-not-available")
    for nodeset in (
        resource
        for resource in _sequence_of_mappings(snapshot.get("soperator_resources"))
        if _text(resource.get("kind")) == "NodeSet"
    ):
        status = _mapping(nodeset.get("status"))
        if _text(status.get("phase") or status.get("status")).lower() != "ready":
            name = _text(_mapping(nodeset.get("metadata")).get("name")) or "unknown"
            conflicts.append(f"soperator.nodesets[{name}].not-ready")
    slurm_health = _mapping(snapshot.get("slurm_health"))
    if slurm_health.get("checked") is not True or slurm_health.get("healthy") is not True:
        conflicts.append("slurm.scontrol-ping")

    required_policy_kinds = {"gpu": "clusterpolicy", "network": "nicclusterpolicy"}
    managed_operators = _mapping(campaign.get("managed_operators"))
    policies = _sequence_of_mappings(_mapping(snapshot.get("gpu_stack")).get("policies"))
    for role, kind in required_policy_kinds.items():
        if role not in managed_operators:
            continue
        matching_policies = [
            policy for policy in policies if _text(policy.get("kind")).lower() == kind
        ]
        if not matching_policies or not all(
            _text(_mapping(policy.get("status")).get("state")).lower() == "ready"
            or _condition_is_true(policy, "Ready")
            for policy in matching_policies
        ):
            conflicts.append(f"gpu-stack.{kind}-not-ready")
    return tuple(dict.fromkeys(conflicts))


def validate_campaign_segment_capabilities(
    snapshot: Mapping[str, Any],
    *,
    segment: Mapping[str, Any],
) -> None:
    """Fail when live Nebius capabilities no longer support a locked segment."""

    mk8s = _mapping(segment.get("mk8s"))
    control_plane = _mapping(mk8s.get("control_plane"))
    target_version = _text(control_plane.get("target_version"))
    source_version = _text(control_plane.get("source_version"))
    if source_version != target_version and target_version not in set(
        provider_control_plane_versions(snapshot)
    ):
        raise ValueError(
            f"Nebius MK8s no longer advertises campaign control-plane target {target_version}."
        )
    live_groups = _mapping(snapshot.get("node_groups"))
    live_by_alias: dict[str, tuple[str, Mapping[str, Any]]] = {}
    live_ids: set[str] = set()
    for key, raw_group in live_groups.items():
        if not isinstance(raw_group, Mapping):
            continue
        provider = _mapping(raw_group.get("provider"))
        stable_id = _text(provider.get("node_group_id")) or _text(raw_group.get("node_group_id"))
        if not stable_id:
            raise ValueError(f"Live campaign node group '{key}' has no stable provider id.")
        if stable_id in live_ids:
            raise ValueError(f"Live campaign node-group id '{stable_id}' is duplicated.")
        live_ids.add(stable_id)
        for alias in (
            _text(key),
            _text(raw_group.get("node_group_id")),
            _text(raw_group.get("node_group_name")),
            _text(provider.get("node_group_id")),
            _text(provider.get("node_group_name")),
        ):
            if alias:
                live_by_alias[alias] = (str(key), raw_group)
    planned_groups = _sequence_of_mappings(mk8s.get("node_groups"))
    planned_ids = {_text(group.get("id")) for group in planned_groups}
    if planned_ids and planned_ids != live_ids:
        missing = sorted(planned_ids - live_ids)
        unexpected = sorted(live_ids - planned_ids)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ValueError(
            "Live Nebius node-group inventory differs from the locked campaign: "
            + "; ".join(details)
            + "."
        )
    for planned in planned_groups:
        group_id = _text(planned.get("id"))
        group_name = _text(planned.get("name"))
        live_match = live_by_alias.get(group_id) or live_by_alias.get(group_name)
        if live_match is None:
            raise ValueError(
                "Campaign node group is missing from live Nebius inventory: "
                f"{group_id or group_name}."
            )
        live_key, live_group = live_match
        provider = _mapping(live_group.get("provider"))
        live_template = _mapping(provider.get("node_template"))
        platform = _text(live_template.get("platform"))
        if platform != _text(planned.get("platform")):
            raise ValueError(
                f"Campaign node group {group_id or group_name} platform changed from "
                f"{_text(planned.get('platform')) or 'unspecified'} to "
                f"{platform or 'unspecified'}."
            )
        preset = _text(live_template.get("preset"))
        planned_preset = _text(planned.get("preset"))
        if preset != planned_preset:
            raise ValueError(
                f"Campaign node group {group_id or group_name} hardware preset changed from "
                f"{planned_preset or 'unspecified'} to {preset or 'unspecified'}."
            )
        role = campaign_node_group_role(live_key, live_group)
        planned_role = _text(planned.get("role"))
        if role != planned_role:
            raise ValueError(
                f"Campaign node group {group_id or group_name} role changed from "
                f"{planned_role or 'unspecified'} to {role or 'unspecified'}."
            )
        gpu_mode = campaign_node_group_gpu_software_mode(live_group)
        planned_gpu_mode = _text(planned.get("gpu_software_mode"))
        if gpu_mode != planned_gpu_mode:
            raise ValueError(
                f"Campaign node group {group_id or group_name} GPU software mode changed "
                f"from {planned_gpu_mode or 'unspecified'} to {gpu_mode or 'unspecified'}."
            )
        target = _mapping(planned.get("target"))
        choices = _compatibility_choices(
            snapshot,
            version=_text(target.get("kubernetes_version")),
            platform=platform,
        )
        exact_tuple = (
            _text(target.get("os")),
            _text(target.get("drivers_preset")),
        )
        supported = {
            (_text(choice.get("os")), _text(choice.get("drivers_preset"))) for choice in choices
        }
        if planned_gpu_mode == "provider-managed":
            target_supported = exact_tuple in supported
        else:
            # Operator-managed GPU software intentionally leaves the provider
            # drivers preset empty. The compatibility matrix still establishes
            # the platform/OS edge even when every published tuple includes a
            # provider-managed driver preset, so do not inherit or require that
            # preset during live capability revalidation.
            target_supported = not exact_tuple[1] and exact_tuple[0] in {
                supported_os for supported_os, _supported_driver in supported
            }
        if not target_supported:
            raise ValueError(
                "Nebius MK8s compatibility changed for campaign node group "
                f"{group_id or group_name}: Kubernetes "
                f"{_text(target.get('kubernetes_version'))}, OS {exact_tuple[0]}, "
                f"drivers_preset {exact_tuple[1] or 'operator-managed'} is no longer supported."
            )


def snapshot_campaign_identity(
    snapshot: Mapping[str, Any],
    *,
    project_id: str,
    cluster_id: str,
    target_ref: str,
) -> dict[str, str]:
    provider = _mapping(snapshot.get("provider"))
    cluster = _mapping(provider.get("mk8s_cluster"))
    identity = _mapping(snapshot.get("cluster_identity"))
    requested_cluster_id = _text(cluster_id)
    provider_cluster_id = _text(cluster.get("id"))
    provider_project_id = _text(cluster.get("parent_id") or cluster.get("parentId"))
    if not provider_cluster_id:
        raise ValueError(
            "External Soperator campaign identity discovery has no provider MK8s "
            "cluster id. No campaign was written."
        )
    if requested_cluster_id and requested_cluster_id != provider_cluster_id:
        raise ValueError(
            "External Soperator campaign cluster identity conflicts with live Nebius "
            f"provider state: requested {requested_cluster_id}, observed "
            f"{provider_cluster_id}. No campaign was written."
        )
    if provider_project_id and _text(project_id) != provider_project_id:
        raise ValueError(
            "External Soperator campaign project identity conflicts with live Nebius "
            f"provider state: config {_text(project_id) or '<missing>'}, observed "
            f"{provider_project_id}. No campaign was written."
        )
    result = {
        "project_id": _text(project_id),
        "cluster_id": provider_cluster_id,
        "cluster_name": _text(cluster.get("name")),
        "target_ref": _text(target_ref),
        "kubernetes_uid": _text(identity.get("kubernetes_uid")),
        "soperator_uid": _text(identity.get("soperator_uid")),
        "slurmcluster_uid": _text(identity.get("slurmcluster_uid")),
        "jail_filesystem_id": _text(identity.get("jail_filesystem_id")),
    }
    required = (
        "project_id",
        "cluster_id",
        "target_ref",
        "kubernetes_uid",
        "soperator_uid",
        "slurmcluster_uid",
        "jail_filesystem_id",
    )
    missing = tuple(key for key in required if not result[key])
    if missing:
        raise ValueError(
            "External Soperator campaign identity discovery is incomplete. Missing "
            "immutable identity field(s): " + ", ".join(missing) + ". No campaign was written."
        )
    return result
