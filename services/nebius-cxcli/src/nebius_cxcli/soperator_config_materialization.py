"""Canonical Soperator configuration materialization services."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import Any

from .component_instances import (
    INSTANCE_ID_FIELD,
    component_instance_id,
    component_type_id,
    ensure_component_instance_id,
    normalize_component_token,
)
from .components import ComponentScope
from .deploy_targets import (
    app_chart_target_ref,
    deploy_target_is_external_mk8s,
    enabled_cluster_target_refs,
)
from .mk8s_node_groups import iter_node_groups as iter_mk8s_node_groups
from .runtime_config import to_plain_data
from .soperator_gpu_driver_jail import ensure_soperator_gpu_driver_jail_values
from .soperator_wizard import soperator_wizard_settings

_SOPERATOR_ACTIVECHECKS_READY_PARTITION_PATH = "soperator-activechecks.srunReadyPartition"


_SOPERATOR_ACTIVECHECKS_HIDDEN_PARTITION_NAME = "hidden"


_SOPERATOR_GUIDED_SSSD_ENABLED_PATH = "sssd.enabled"


_SOPERATOR_APP_ID = "soperator"


_SOPERATOR_TARGET_MODE_MANAGED = "managed"


_SOPERATOR_TARGET_MODE_REGISTERED = "registered"


_SOPERATOR_LEGACY_WORKER_INPUT_FIELDS = (
    "worker_total_nodes",
    "worker_nodes_per_group",
    "worker_autoscaling",
    "worker_cpu_autoscaling",
    "worker_gpu_autoscaling",
)


_SOPERATOR_WORKER_EPHEMERAL_INPUT = "soperator.worker_ephemeral_nodes"


_SOPERATOR_WORKER_NODE_GROUPS_INPUT = "soperator.worker_node_groups"


_SOPERATOR_STATIC_CONFIG_REVISION_ANNOTATION = (
    "nebius.ai/cxcli-static-slurm-config-sha256"
)


_SOPERATOR_STATIC_CONFIG_REVISION_ENV = "CXCLI_STATIC_SLURM_CONFIG_SHA256"


_SOPERATOR_STATIC_CONFIG_REVISION_SCHEMA = (
    "nebius-cxcli.soperator-static-slurm-config.v2"
)


_SOPERATOR_STATIC_CONFIG_REVISION_LEGACY_SCHEMA = (
    "nebius-cxcli.soperator-static-slurm-config.v1"
)


_SOPERATOR_REGISTERED_RUNTIME_MOUNTS = (
    {
        "name": "slurm-scripts",
        "mountPath": "/opt/slurm_scripts/",
        "readOnly": True,
        "volumeSource": {
            "configMap": {"name": "slurm-scripts", "defaultMode": 493},
        },
    },
    {
        "name": "slurm-scripts-jail",
        "mountPath": "/mnt/jail.upper/opt/slurm_scripts/",
        "readOnly": True,
        "volumeSource": {
            "configMap": {"name": "slurm-scripts", "defaultMode": 493},
        },
    },
    {
        "name": "hpc-jobs-dir",
        "mountPath": "/var/run/nebius/slurm",
        "readOnly": False,
        "volumeSource": {
            "hostPath": {
                "path": "/var/run/nebius/slurm",
                "type": "DirectoryOrCreate",
            },
        },
    },
)


def _non_empty_text(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _soperator_nodeset_values_are_gpu(nodeset: Mapping[str, Any]) -> bool:
    gpu = nodeset.get("gpu")
    if isinstance(gpu, Mapping) and gpu.get("enabled") is True:
        return True
    resources = _mapping_path_value(nodeset, "slurmd.resources")
    if not isinstance(resources, Mapping):
        return False
    return any(resources.get(key) not in (None, "", 0, "0") for key in ("gpu", "nvidia.com/gpu"))


def _external_mk8s_target_refs(payload: Mapping[str, Any]) -> set[str]:
    deploy = payload.get("deploy")
    targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
    if not isinstance(targets, list):
        return set()
    return {
        target_ref
        for row in targets
        if isinstance(row, Mapping) and deploy_target_is_external_mk8s(row)
        if (target_ref := normalize_component_token(row.get(INSTANCE_ID_FIELD)))
    }


def _merge_missing_mapping(target: dict[str, Any], defaults: Mapping[str, Any]) -> None:
    for key, value in defaults.items():
        plain_value = to_plain_data(value)
        if key not in target:
            target[key] = copy.deepcopy(plain_value)
            continue
        if isinstance(target[key], dict) and isinstance(plain_value, Mapping):
            _merge_missing_mapping(target[key], plain_value)


def _merge_replace_mapping(target: dict[str, Any], values: Mapping[str, Any]) -> None:
    for key, value in values.items():
        plain_value = to_plain_data(value)
        if isinstance(target.get(key), dict) and isinstance(plain_value, Mapping):
            _merge_replace_mapping(target[key], plain_value)
            continue
        target[key] = copy.deepcopy(plain_value)


_MISSING_PROFILE_VALUE = object()


def _soperator_named_mapping_list_names(value: Any) -> set[str]:
    if not isinstance(value, list) or not value:
        return set()
    names: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return set()
        name = _non_empty_text(item.get("name"))
        if not name:
            return set()
        names.add(name)
    return names


def _soperator_merge_profile_nodesets_with_existing(
    existing_nodesets: Any,
    profile_nodesets: list[Any],
    *,
    replaceable_nodeset_lists: Sequence[Any] = (),
) -> list[Any]:
    if not isinstance(existing_nodesets, list):
        existing_nodesets = []
    existing_by_name = {
        _non_empty_text(item.get("name")): item
        for item in existing_nodesets
        if isinstance(item, Mapping)
        if _non_empty_text(item.get("name"))
    }
    merged_nodesets: list[Any] = []
    for item in profile_nodesets:
        if not isinstance(item, Mapping):
            merged_nodesets.append(copy.deepcopy(item))
            continue
        name = _non_empty_text(item.get("name"))
        existing = existing_by_name.get(name)
        if isinstance(existing, Mapping):
            merged = _soperator_merge_nodeset_with_generated(existing, item)
            generated_node_config = item.get("nodeConfig")
            existing_node_config = existing.get("nodeConfig")
            if isinstance(generated_node_config, Mapping) and isinstance(
                existing_node_config,
                Mapping,
            ):
                generated_features = generated_node_config.get("features")
                existing_features = existing_node_config.get("features")
                replaceable_features = [
                    candidate_features
                    for candidate_list in replaceable_nodeset_lists
                    if isinstance(candidate_list, list)
                    for candidate in candidate_list
                    if isinstance(candidate, Mapping)
                    and _non_empty_text(candidate.get("name")) == name
                    if isinstance(candidate_node_config := candidate.get("nodeConfig"), Mapping)
                    if isinstance(candidate_features := candidate_node_config.get("features"), list)
                ]
                if isinstance(generated_features, list) and any(
                    existing_features == candidate for candidate in replaceable_features
                ):
                    node_config = merged.setdefault("nodeConfig", {})
                    if isinstance(node_config, dict):
                        node_config["features"] = copy.deepcopy(generated_features)
            merged_nodesets.append(merged)
        else:
            merged_nodesets.append(copy.deepcopy(dict(item)))
    return merged_nodesets


def _merge_soperator_profile_values(
    target: dict[str, Any],
    profile_values: Mapping[str, Any],
    base_values: Mapping[str, Any],
    *,
    replaceable_base_values: Sequence[Mapping[str, Any]] = (),
) -> None:
    replaceable_bases = tuple(
        to_plain_data(item) for item in replaceable_base_values if isinstance(item, Mapping)
    )
    for key, value in profile_values.items():
        plain_value = to_plain_data(value)
        target_value = target.get(key, _MISSING_PROFILE_VALUE)
        base_value = (
            to_plain_data(base_values.get(key, _MISSING_PROFILE_VALUE))
            if isinstance(base_values, Mapping)
            else _MISSING_PROFILE_VALUE
        )
        replaceable_values = [
            to_plain_data(item[key])
            for item in replaceable_bases
            if isinstance(item, Mapping) and key in item
        ]
        if isinstance(target_value, dict) and isinstance(plain_value, Mapping):
            base_child = base_value if isinstance(base_value, Mapping) else {}
            replaceable_children = [
                item[key]
                for item in replaceable_bases
                if isinstance(item, Mapping) and isinstance(item.get(key), Mapping)
            ]
            _merge_soperator_profile_values(
                target_value,
                plain_value,
                base_child,
                replaceable_base_values=replaceable_children,
            )
            continue
        target_names = _soperator_named_mapping_list_names(target_value)
        if target_names and isinstance(plain_value, list):
            replaceable_name_sets = [
                candidate_names
                for candidate in replaceable_values
                if (candidate_names := _soperator_named_mapping_list_names(candidate))
            ]
            if any(target_names <= candidate_names for candidate_names in replaceable_name_sets):
                if key == "nodesets":
                    base_nodeset_lists = [base_value] if isinstance(base_value, list) else []
                    target[key] = _soperator_merge_profile_nodesets_with_existing(
                        target_value,
                        plain_value,
                        replaceable_nodeset_lists=(*base_nodeset_lists, *replaceable_values),
                    )
                else:
                    target[key] = copy.deepcopy(plain_value)
                continue
        if (
            target_value is _MISSING_PROFILE_VALUE
            or target_value == base_value
            or any(target_value == candidate for candidate in replaceable_values)
        ):
            target[key] = copy.deepcopy(plain_value)

    selected_keys = set(profile_values)
    generated_keys: set[str] = set()
    for base in replaceable_bases:
        generated_keys.update(str(key) for key in base)
    for key in generated_keys - selected_keys:
        if key not in target:
            continue
        target_value = target[key]
        replaceable_values = [
            to_plain_data(item[key])
            for item in replaceable_bases
            if isinstance(item, Mapping) and key in item
        ]
        if any(target_value == candidate for candidate in replaceable_values):
            target.pop(key, None)


def _merge_missing_soperator_profile_partitions(
    values: dict[str, Any],
    chart_profile: Mapping[str, Any],
) -> None:
    profile_partition_config = chart_profile.get("partitionConfiguration")
    if not isinstance(profile_partition_config, Mapping):
        return
    profile_partitions = profile_partition_config.get("partitions")
    if not isinstance(profile_partitions, list) or not profile_partitions:
        return

    partition_config = values.get("partitionConfiguration")
    if not isinstance(partition_config, dict):
        return
    config_type = _non_empty_text(partition_config.get("configType")) or _non_empty_text(
        profile_partition_config.get("configType")
    )
    if config_type and config_type != "structured":
        return
    partitions = partition_config.get("partitions")
    if not isinstance(partitions, list):
        return

    existing_names = {
        name
        for partition in partitions
        if isinstance(partition, Mapping)
        if (name := _non_empty_text(partition.get("name")))
    }
    missing = [
        copy.deepcopy(partition)
        for partition in profile_partitions
        if isinstance(partition, Mapping)
        if (name := _non_empty_text(partition.get("name"))) and name not in existing_names
    ]
    if missing:
        partition_config["partitions"] = [*missing, *partitions]


def _soperator_app_target_refs(payload: Mapping[str, Any]) -> tuple[str, ...]:
    target_refs = set(enabled_cluster_target_refs(payload))
    apps_node = payload.get("apps")
    charts = apps_node.get("charts") if isinstance(apps_node, Mapping) else None
    if not isinstance(charts, list):
        return ()
    refs: list[str] = []
    seen: set[str] = set()
    for row in charts:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != _SOPERATOR_APP_ID:
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if target_refs and target_ref not in target_refs:
            continue
        if target_ref and target_ref not in seen:
            refs.append(target_ref)
            seen.add(target_ref)
    return tuple(refs)


def _external_mk8s_inputs_by_target(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    deploy = payload.get("deploy")
    targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
    if not isinstance(targets, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in targets:
        if not isinstance(row, Mapping) or not deploy_target_is_external_mk8s(row):
            continue
        target_ref = normalize_component_token(row.get(INSTANCE_ID_FIELD))
        if not target_ref:
            continue
        inventory = row.get("inventory")
        node_groups = inventory.get("node_groups") if isinstance(inventory, Mapping) else None
        if isinstance(node_groups, Mapping):
            result[target_ref] = {"node_groups": copy.deepcopy(dict(node_groups))}
    return result


def _default_soperator_target_mode() -> str:
    return _SOPERATOR_TARGET_MODE_MANAGED


def _default_soperator_wizard_text(field_path: str, *, fallback: str) -> str:
    wizard_fields = soperator_wizard_settings().wizard_fields
    spec = wizard_fields.get(field_path)
    if isinstance(spec, Mapping) and "default" in spec:
        return _non_empty_text(spec.get("default")) or fallback
    return fallback


def _default_soperator_profile_name() -> str:
    settings_default, _profiles = _soperator_nodesets_profiles()
    return _default_soperator_wizard_text("profile", fallback=settings_default)


def _default_soperator_partition_profile_name() -> str:
    return _default_soperator_wizard_text("values.partitionProfile", fallback="shape-default")


def _default_soperator_topology_profile_name() -> str:
    return _default_soperator_wizard_text("values.topologyProfile", fallback="disabled")


def _soperator_target_mode_by_target(payload: Mapping[str, Any]) -> dict[str, str]:
    apps_node = payload.get("apps")
    charts = apps_node.get("charts") if isinstance(apps_node, Mapping) else None
    if not isinstance(charts, list):
        return {}
    external_targets = _external_mk8s_target_refs(payload)
    selected: dict[str, str] = {}
    for row in charts:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != _SOPERATOR_APP_ID:
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if not target_ref:
            continue
        if "install_mode" in row:
            raise ValueError(
                "apps:soperator.install_mode was removed. Target kind/ownership is the "
                "only Soperator lifecycle routing authority."
            )
        selected[target_ref] = (
            _SOPERATOR_TARGET_MODE_REGISTERED
            if target_ref in external_targets
            else _SOPERATOR_TARGET_MODE_MANAGED
        )
    return selected


def _soperator_nodesets_profiles() -> tuple[str, Mapping[str, Mapping[str, Any]]]:
    settings = soperator_wizard_settings().nodesets
    default = _non_empty_text(settings.default)
    profiles = settings.profiles
    if not isinstance(profiles, Mapping):
        profiles = {}
    return default, profiles


def _soperator_profile_by_target(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    default_profile, profiles = _soperator_nodesets_profiles()
    apps_node = payload.get("apps")
    charts = apps_node.get("charts") if isinstance(apps_node, Mapping) else None
    if not isinstance(charts, list):
        return {}

    selected: dict[str, Mapping[str, Any]] = {}
    for row in charts:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != _SOPERATOR_APP_ID:
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if not target_ref:
            continue
        profile_name = _non_empty_text(row.get("profile")) or default_profile
        if not profile_name:
            selected[target_ref] = {}
            continue
        profile = profiles.get(profile_name)
        if not isinstance(profile, Mapping):
            available = ", ".join(sorted(str(name) for name in profiles)) or "(none)"
            raise ValueError(
                f"apps.charts[{target_ref}].profile references unknown Soperator "
                f"nodesets profile '{profile_name}'. Available profiles: {available}"
            )
        selected[target_ref] = profile
    return selected


def _profile_mapping(profile: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = profile.get(key)
    return value if isinstance(value, Mapping) else {}


def _soperator_profile_activechecks_ready_partition(profile: Mapping[str, Any]) -> str:
    activechecks = _profile_mapping(_profile_mapping(profile, "chart"), "activechecks")
    return _non_empty_text(activechecks.get("srunReadyPartition"))


def _soperator_internal_activechecks_partition() -> dict[str, Any]:
    return {
        "name": _SOPERATOR_ACTIVECHECKS_HIDDEN_PARTITION_NAME,
        "isAll": True,
        "policy": {
            "default": False,
            "hidden": True,
            "state": "UP",
            "maxTime": "INFINITE",
            "priorityTier": 10,
            "preemptMode": "OFF",
            "overSubscribe": "YES",
        },
    }


def _soperator_registered_default_partitions() -> list[dict[str, Any]]:
    """Return the structured equivalent of upstream's default partitions.

    Upstream 4.1.7 emits static ``NodeName`` records for NodeSet workers only
    when partition configuration is structured. Registered legacy clusters may
    legitimately report ``configType=default``; carrying that value into a
    static NodeSet target creates Ready pods whose slurmd cannot resolve its
    node name. Preserve the two default partition policies while selecting the
    target API mode that actually renders the adopted workers.
    """

    return [
        {
            "name": "main",
            "isAll": True,
            "policy": {
                "default": True,
                "state": "UP",
                "maxTime": "INFINITE",
                "priorityTier": 10,
                "overSubscribe": "YES",
            },
        },
        {
            "name": "hidden",
            "isAll": True,
            "policy": {
                "default": False,
                "hidden": True,
                "state": "UP",
                "maxTime": "INFINITE",
                "priorityTier": 10,
                "preemptMode": "OFF",
                "overSubscribe": "YES",
            },
        },
    ]


def _materialize_soperator_registered_static_partitions(values: dict[str, Any]) -> bool:
    """Make an adopted default partition topology valid for static NodeSets."""

    nodesets = values.get("nodesets")
    if not isinstance(nodesets, list) or not any(
        isinstance(item, Mapping)
        and _non_empty_text(item.get("name"))
        and _positive_int(item.get("replicas"), default=0) > 0
        for item in nodesets
    ):
        return False
    partition_config = values.get("partitionConfiguration")
    if (
        not isinstance(partition_config, Mapping)
        or _non_empty_text(partition_config.get("configType")) != "default"
    ):
        return False
    replacement = {
        "configType": "structured",
        "partitions": _soperator_registered_default_partitions(),
    }
    if partition_config == replacement:
        return False
    values["partitionConfiguration"] = replacement
    return True


def _soperator_registered_static_config_revision(values: Mapping[str, Any]) -> str:
    """Hash the static node-registration config shared by controller and workers."""

    partition_config = values.get("partitionConfiguration")
    if (
        not isinstance(partition_config, Mapping)
        or _non_empty_text(partition_config.get("configType")) != "structured"
    ):
        return ""
    nodesets = values.get("nodesets")
    if not isinstance(nodesets, list):
        return ""
    static_nodesets = sorted(
        (
            {
                "name": name,
                "replicas": _positive_int(item.get("replicas"), default=0),
            }
            for item in nodesets
            if isinstance(item, Mapping)
            if (name := _non_empty_text(item.get("name")))
            if _positive_int(item.get("replicas"), default=0) > 0
        ),
        key=lambda item: item["name"],
    )
    if not static_nodesets:
        return ""
    payload = {
        "schema": _SOPERATOR_STATIC_CONFIG_REVISION_SCHEMA,
        "nodeSets": static_nodesets,
        "partitionConfiguration": to_plain_data(partition_config),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _soperator_registered_legacy_static_config_revision(values: Mapping[str, Any]) -> str:
    """Return the exact pre-v2 rollout token for bounded repair admission."""

    partition_config = values.get("partitionConfiguration")
    if (
        not isinstance(partition_config, Mapping)
        or _non_empty_text(partition_config.get("configType")) != "structured"
    ):
        return ""
    nodesets = values.get("nodesets")
    if not isinstance(nodesets, list):
        return ""
    static_nodesets = sorted(
        (
            {
                "name": name,
                "replicas": _positive_int(item.get("replicas"), default=0),
            }
            for item in nodesets
            if isinstance(item, Mapping)
            if (name := _non_empty_text(item.get("name")))
            if _positive_int(item.get("replicas"), default=0) > 0
        ),
        key=lambda item: item["name"],
    )
    if not static_nodesets:
        return ""
    encoded = json.dumps(
        {
            "schema": _SOPERATOR_STATIC_CONFIG_REVISION_LEGACY_SCHEMA,
            "nodeSets": static_nodesets,
            "partitionConfigType": "structured",
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _materialize_soperator_registered_runtime_mounts(values: dict[str, Any]) -> bool:
    """Restore operational mounts supplied by the pre-split worker chart.

    The 4.1.7 SlurmCluster still enables ``hc_program.sh`` and owns the
    ``slurm-scripts`` ConfigMap, but split NodeSets do not inherit that volume.
    Registered workers also retain the node-local job metrics directory used by
    the Soperator/DCGM runtime. Reserved name or path collisions fail closed.
    """

    nodesets = values.get("nodesets")
    if not isinstance(nodesets, list):
        return False
    changed = False
    for index, item in enumerate(nodesets):
        if not isinstance(item, dict) or not _non_empty_text(item.get("name")):
            continue
        slurmd = item.get("slurmd")
        if not isinstance(slurmd, dict):
            raise ValueError(f"Soperator NodeSet values[{index}].slurmd must be a mapping")
        volumes = slurmd.get("volumes")
        if volumes is None:
            volumes = {}
            slurmd["volumes"] = volumes
        if not isinstance(volumes, dict):
            raise ValueError(
                f"Soperator NodeSet values[{index}].slurmd.volumes must be a mapping"
            )
        mounts = volumes.get("customVolumeMounts")
        if mounts is None:
            mounts = []
            volumes["customVolumeMounts"] = mounts
        if not isinstance(mounts, list):
            raise ValueError(
                f"Soperator NodeSet values[{index}].slurmd.volumes.customVolumeMounts "
                "must be a list"
            )
        matched_ids: set[int] = set()
        for expected in _SOPERATOR_REGISTERED_RUNTIME_MOUNTS:
            collisions = [
                candidate
                for candidate in mounts
                if isinstance(candidate, Mapping)
                and (
                    _non_empty_text(candidate.get("name")) == expected["name"]
                    or _non_empty_text(candidate.get("mountPath")) == expected["mountPath"]
                )
            ]
            if collisions:
                if len(collisions) != 1 or to_plain_data(collisions[0]) != expected:
                    raise ValueError(
                        f"Soperator NodeSet values[{index}] collides with cxcli-owned "
                        f"runtime mount {expected['name']}"
                    )
                matched_ids.add(id(collisions[0]))
        unrelated = [mount for mount in mounts if id(mount) not in matched_ids]
        normalized_mounts = [
            *(copy.deepcopy(item) for item in _SOPERATOR_REGISTERED_RUNTIME_MOUNTS),
            *unrelated,
        ]
        if mounts != normalized_mounts:
            volumes["customVolumeMounts"] = normalized_mounts
            changed = True
    return changed


def _materialize_soperator_registered_static_worker_rollout(
    values: dict[str, Any],
) -> bool:
    """Force static workers to restart after controller-side node records change."""

    revision = _soperator_registered_static_config_revision(values)
    nodesets = values.get("nodesets")
    if not revision or not isinstance(nodesets, list):
        return False
    changed = False
    for index, item in enumerate(nodesets):
        if not isinstance(item, dict):
            continue
        if not _non_empty_text(item.get("name")) or _positive_int(
            item.get("replicas"), default=0
        ) <= 0:
            continue
        annotations = item.get("workerAnnotations")
        if annotations is None:
            annotations = {}
            item["workerAnnotations"] = annotations
        if not isinstance(annotations, dict):
            raise ValueError(
                f"Soperator NodeSet values[{index}].workerAnnotations must be a mapping"
            )
        if annotations.get(_SOPERATOR_STATIC_CONFIG_REVISION_ANNOTATION) != revision:
            annotations[_SOPERATOR_STATIC_CONFIG_REVISION_ANNOTATION] = revision
            changed = True
        slurmd = item.get("slurmd")
        if not isinstance(slurmd, dict):
            raise ValueError(
                f"Soperator NodeSet values[{index}].slurmd must be a mapping"
            )
        custom_env = slurmd.get("customEnv")
        if custom_env is None:
            custom_env = []
            slurmd["customEnv"] = custom_env
        if not isinstance(custom_env, list):
            raise ValueError(
                f"Soperator NodeSet values[{index}].slurmd.customEnv must be a list"
            )
        revision_indexes = [
            env_index
            for env_index, env in enumerate(custom_env)
            if isinstance(env, Mapping)
            and _non_empty_text(env.get("name"))
            == _SOPERATOR_STATIC_CONFIG_REVISION_ENV
        ]
        if len(revision_indexes) > 1:
            raise ValueError(
                f"Soperator NodeSet values[{index}].slurmd.customEnv contains duplicate "
                f"{_SOPERATOR_STATIC_CONFIG_REVISION_ENV} entries"
            )
        revision_env = {
            "name": _SOPERATOR_STATIC_CONFIG_REVISION_ENV,
            "value": revision,
        }
        if revision_indexes:
            env_index = revision_indexes[0]
            if custom_env[env_index] != revision_env:
                custom_env[env_index] = revision_env
                changed = True
        else:
            custom_env.append(revision_env)
            changed = True
    return changed


def _remove_internal_activechecks_partition(values: dict[str, Any]) -> bool:
    partition_config = values.get("partitionConfiguration")
    if not isinstance(partition_config, dict):
        return False
    partitions = partition_config.get("partitions")
    if not isinstance(partitions, list):
        return False
    internal_partition = _soperator_internal_activechecks_partition()
    filtered = [partition for partition in partitions if partition != internal_partition]
    if len(filtered) == len(partitions):
        return False
    partition_config["partitions"] = filtered
    return True


def _prepend_internal_activechecks_partition(values: dict[str, Any]) -> bool:
    partition_config = values.setdefault("partitionConfiguration", {})
    if not isinstance(partition_config, dict):
        return False
    config_type = _non_empty_text(partition_config.get("configType"))
    if config_type and config_type != "structured":
        return False
    partition_config["configType"] = "structured"
    existing = partition_config.get("partitions")
    partitions = list(existing) if isinstance(existing, list) else []
    internal_partition = _soperator_internal_activechecks_partition()
    if any(
        isinstance(partition, Mapping)
        and _non_empty_text(partition.get("name"))
        == _SOPERATOR_ACTIVECHECKS_HIDDEN_PARTITION_NAME
        for partition in partitions
    ):
        return False
    next_partitions = [internal_partition, *partitions]
    if partitions == next_partitions:
        return False
    partition_config["partitions"] = next_partitions
    return True


def _remove_guided_sssd_helper(values: dict[str, Any]) -> bool:
    changed = _delete_mapping_path_value(values, _SOPERATOR_GUIDED_SSSD_ENABLED_PATH)
    helper = values.get("sssd")
    if isinstance(helper, dict) and not helper:
        values.pop("sssd", None)
        changed = True
    return changed


def _materialize_soperator_render_only_values(payload: dict[str, Any]) -> bool:
    """Materialize Soperator values that should not be source-config knobs."""
    profile_by_target = _soperator_profile_by_target(payload)
    install_mode_by_target = _soperator_target_mode_by_target(payload)
    mk8s_inputs_by_target = _external_mk8s_inputs_by_target(payload)
    apps_node = payload.get("apps")
    charts = apps_node.get("charts") if isinstance(apps_node, Mapping) else None
    if not isinstance(charts, list):
        return False

    changed = False
    for row in charts:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != _SOPERATOR_APP_ID:
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if not target_ref:
            continue
        values = row.get("values")
        if not isinstance(values, dict):
            continue
        before = copy.deepcopy(values)
        if install_mode_by_target.get(target_ref) == _SOPERATOR_TARGET_MODE_REGISTERED:
            _soperator_hydrate_registered_nodesets_from_profile(
                values,
                profile=profile_by_target.get(target_ref, {}),
                inputs=mk8s_inputs_by_target.get(target_ref, {}),
                placements=_soperator_row_placements(row),
            )
        _materialize_soperator_guided_sssd_values(values)
        _remove_guided_sssd_helper(values)
        _remove_internal_activechecks_partition(values)
        activechecks_enabled = _mapping_path_value(values, "soperator-activechecks.enabled") is True
        if activechecks_enabled:
            partition = _soperator_profile_activechecks_ready_partition(
                profile_by_target.get(target_ref, {})
            )
            if partition:
                _set_mapping_path_value(
                    values,
                    _SOPERATOR_ACTIVECHECKS_READY_PARTITION_PATH,
                    partition,
                )
                if partition == _SOPERATOR_ACTIVECHECKS_HIDDEN_PARTITION_NAME:
                    _prepend_internal_activechecks_partition(values)
        else:
            _delete_mapping_path_value(values, _SOPERATOR_ACTIVECHECKS_READY_PARTITION_PATH)
        if install_mode_by_target.get(target_ref) == _SOPERATOR_TARGET_MODE_REGISTERED:
            _materialize_soperator_registered_static_partitions(values)
            _materialize_soperator_registered_runtime_mounts(values)
            _materialize_soperator_registered_static_worker_rollout(values)
        if values != before:
            changed = True
    return changed


def _profile_list(profile: Mapping[str, Any], key: str) -> list[Any]:
    value = profile.get(key)
    return list(value) if isinstance(value, list) else []


def _soperator_all_profile_jail_node_group_keys() -> list[str]:
    _default_profile, profiles = _soperator_nodesets_profiles()
    selected: list[str] = []

    def _remember(value: Any) -> None:
        key = _non_empty_text(value)
        if key and key not in selected:
            selected.append(key)

    for profile in profiles.values():
        if not isinstance(profile, Mapping):
            continue
        mk8s_profile = _profile_mapping(profile, "mk8s")
        for group_key, group in _profile_mapping(mk8s_profile, "node_groups").items():
            if isinstance(group, Mapping) and group.get("jail") is True:
                _remember(group_key)
        for worker in _profile_list(mk8s_profile, "worker_nodesets"):
            if not isinstance(worker, Mapping) or worker.get("jail") is not True:
                continue
            _remember(worker.get("node_group_prefix"))
            _remember(worker.get("name"))
    return selected


def _render_soperator_profile_value(value: Any, *, target_ref: str) -> Any:
    if isinstance(value, str):
        return value.replace("{target}", target_ref)
    if isinstance(value, list):
        return [_render_soperator_profile_value(item, target_ref=target_ref) for item in value]
    if isinstance(value, dict):
        return {
            key: _render_soperator_profile_value(item, target_ref=target_ref)
            for key, item in value.items()
        }
    return copy.deepcopy(value)


def _positive_int(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, float):
        return int(value) if value > 0 else default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _required_nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} must be an integer >= 0")
    if isinstance(value, int):
        if value >= 0:
            return value
        raise ValueError(f"{field} must be an integer >= 0")
    if isinstance(value, float):
        if value.is_integer() and value >= 0:
            return int(value)
        raise ValueError(f"{field} must be an integer >= 0")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer >= 0") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be an integer >= 0")
    return parsed


def _required_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} must be a positive integer")
    if isinstance(value, int):
        if value > 0:
            return value
        raise ValueError(f"{field} must be a positive integer")
    if isinstance(value, float):
        if value.is_integer() and value > 0:
            return int(value)
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _config_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return _non_empty_text(value).lower() in {"1", "true", "yes", "on"}


def _soperator_worker_ephemeral_input(inputs: Mapping[str, Any]) -> Mapping[str, Any]:
    raw_value = _mapping_path_value(inputs, _SOPERATOR_WORKER_EPHEMERAL_INPUT)
    return raw_value if isinstance(raw_value, Mapping) else {}


def _soperator_worker_ephemeral_suspend_time_seconds(inputs: Mapping[str, Any]) -> int:
    raw_value = _soperator_worker_ephemeral_input(inputs)
    if raw_value.get("suspend_time_seconds") is None:
        return 300
    return _required_nonnegative_int(
        raw_value.get("suspend_time_seconds"),
        field=f"{_SOPERATOR_WORKER_EPHEMERAL_INPUT}.suspend_time_seconds",
    )


def _soperator_worker_node_groups_input(inputs: Mapping[str, Any]) -> Mapping[str, Any]:
    raw_value = _mapping_path_value(inputs, _SOPERATOR_WORKER_NODE_GROUPS_INPUT)
    if raw_value is None:
        return {}
    if not isinstance(raw_value, Mapping):
        raise ValueError(f"{_SOPERATOR_WORKER_NODE_GROUPS_INPUT} must be a mapping")
    return raw_value


def _soperator_worker_node_groups_mutable(inputs: dict[str, Any]) -> dict[str, Any]:
    soperator_inputs = inputs.setdefault("soperator", {})
    if not isinstance(soperator_inputs, dict):
        soperator_inputs = {}
        inputs["soperator"] = soperator_inputs
    worker_node_groups = soperator_inputs.get("worker_node_groups")
    if worker_node_groups is None:
        worker_node_groups = {}
        soperator_inputs["worker_node_groups"] = worker_node_groups
    if not isinstance(worker_node_groups, dict):
        raise ValueError(f"{_SOPERATOR_WORKER_NODE_GROUPS_INPUT} must be a mapping")
    return worker_node_groups


def _soperator_worker_node_group_control(
    inputs: Mapping[str, Any],
    group_key: str,
) -> Mapping[str, Any]:
    raw_groups = _soperator_worker_node_groups_input(inputs)
    raw_control = raw_groups.get(group_key)
    return raw_control if isinstance(raw_control, Mapping) else {}


def _soperator_worker_node_group_ephemeral_enabled(
    inputs: Mapping[str, Any],
    group_key: str,
) -> bool:
    raw_control = _soperator_worker_node_group_control(inputs, group_key)
    raw_ephemeral = raw_control.get("ephemeral_nodes")
    if not isinstance(raw_ephemeral, Mapping):
        return False
    return _config_bool(raw_ephemeral.get("enabled"), default=False)


def _soperator_any_worker_node_group_ephemeral_enabled(inputs: Mapping[str, Any]) -> bool:
    raw_groups = _soperator_worker_node_groups_input(inputs)
    return any(
        _soperator_worker_node_group_ephemeral_enabled(inputs, str(group_key))
        for group_key in raw_groups
    )


def _soperator_worker_node_group_autoscaling(
    *,
    inputs: Mapping[str, Any],
    group_key: str,
    shard_size: int,
) -> dict[str, int] | None:
    raw_control = _soperator_worker_node_group_control(inputs, group_key)
    raw_autoscaling = raw_control.get("autoscaling")
    field_prefix = f"{_SOPERATOR_WORKER_NODE_GROUPS_INPUT}.{group_key}.autoscaling"
    if not isinstance(raw_autoscaling, Mapping):
        return None
    if not _config_bool(raw_autoscaling.get("enabled"), default=False):
        return None
    min_node_count = (
        0
        if raw_autoscaling.get("min_node_count") is None
        else _required_nonnegative_int(
            raw_autoscaling.get("min_node_count"),
            field=f"{field_prefix}.min_node_count",
        )
    )
    max_node_count = (
        shard_size
        if raw_autoscaling.get("max_node_count") is None
        else _required_nonnegative_int(
            raw_autoscaling.get("max_node_count"),
            field=f"{field_prefix}.max_node_count",
        )
    )
    if max_node_count < min_node_count:
        raise ValueError(
            f"{field_prefix}.max_node_count must be greater than or equal to "
            f"{field_prefix}.min_node_count"
        )
    if max_node_count > shard_size:
        raise ValueError(
            f"{field_prefix}.max_node_count must be less than or equal to "
            f"the shard capacity {shard_size}"
        )
    return {
        "min_node_count": min_node_count,
        "max_node_count": max_node_count,
    }


def _soperator_worker_control_key_matches_prefix(key: str, key_prefix: str) -> bool:
    return key == key_prefix or bool(re.fullmatch(rf"{re.escape(key_prefix)}-[0-9]+", key))


def _required_profile_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Soperator nodesets profile field '{field}' must be a positive integer.")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Soperator nodesets profile field '{field}' must be a positive integer."
        ) from exc
    if parsed <= 0:
        raise ValueError(f"Soperator nodesets profile field '{field}' must be a positive integer.")
    return parsed


def _soperator_nodeset_group_keys(
    node_groups: Mapping[str, Any],
    *,
    nodeset_name: str,
    key_prefix: str,
) -> list[str]:
    keys: list[str] = []
    for key, group in node_groups.items():
        key_text = str(key)
        if key_text == key_prefix or re.fullmatch(rf"{re.escape(key_prefix)}-[0-9]+", key_text):
            keys.append(key_text)
            continue
        if isinstance(group, Mapping) and (
            str(group.get("nodeset_name", "")).strip() == nodeset_name
            or str(group.get("placement_name", "")).strip() == nodeset_name
        ):
            keys.append(key_text)
    return keys


def _soperator_profile_managed_node_group_keys(profile: Mapping[str, Any]) -> set[str]:
    mk8s_profile = _profile_mapping(profile, "mk8s")
    managed = {str(key) for key in _profile_mapping(mk8s_profile, "node_groups")}
    for raw_worker in _profile_list(mk8s_profile, "worker_nodesets"):
        if not isinstance(raw_worker, Mapping):
            continue
        nodeset_name = _non_empty_text(raw_worker.get("nodeset_name")) or _non_empty_text(
            raw_worker.get("name")
        )
        key_prefix = _non_empty_text(raw_worker.get("node_group_prefix")) or nodeset_name
        if key_prefix:
            managed.add(key_prefix)
    return managed


def _soperator_profile_static_node_group_keys(profile: Mapping[str, Any]) -> set[str]:
    mk8s_profile = _profile_mapping(profile, "mk8s")
    return {str(key) for key in _profile_mapping(mk8s_profile, "node_groups")}


def _soperator_node_group_key_matches_managed(key: str, managed_keys: set[str]) -> bool:
    if key in managed_keys:
        return True
    return any(re.fullmatch(rf"{re.escape(prefix)}-[0-9]+", key) for prefix in managed_keys)


def _soperator_all_profile_managed_node_group_keys() -> set[str]:
    _default_profile, profiles = _soperator_nodesets_profiles()
    managed: set[str] = set()
    for profile in profiles.values():
        if isinstance(profile, Mapping):
            managed.update(_soperator_profile_managed_node_group_keys(profile))
    return managed


def _soperator_profile_managed_gpu_cluster_keys(profile: Mapping[str, Any]) -> set[str]:
    mk8s_profile = _profile_mapping(profile, "mk8s")
    managed = {str(key) for key in _profile_mapping(mk8s_profile, "gpu_clusters")}
    gpu_cluster_key = _non_empty_text(mk8s_profile.get("gpu_cluster_key"))
    if gpu_cluster_key:
        managed.add(gpu_cluster_key)
    for raw_worker in _profile_list(mk8s_profile, "worker_nodesets"):
        if not isinstance(raw_worker, Mapping):
            continue
        node_group = raw_worker.get("node_group")
        if not isinstance(node_group, Mapping):
            continue
        gpu_cluster_key = _non_empty_text(node_group.get("gpu_cluster_key"))
        if gpu_cluster_key:
            managed.add(gpu_cluster_key)
    return managed


def _soperator_all_profile_managed_gpu_cluster_keys() -> set[str]:
    _default_profile, profiles = _soperator_nodesets_profiles()
    managed: set[str] = set()
    for profile in profiles.values():
        if isinstance(profile, Mapping):
            managed.update(_soperator_profile_managed_gpu_cluster_keys(profile))
    return managed


def _soperator_prune_stale_profile_node_groups(
    *,
    inputs: dict[str, Any],
    profile: Mapping[str, Any],
) -> None:
    node_groups = inputs.get("node_groups")
    if not isinstance(node_groups, dict) or not node_groups:
        return
    all_managed = _soperator_all_profile_managed_node_group_keys()
    selected_managed = _soperator_profile_managed_node_group_keys(profile)
    if not all_managed or not selected_managed:
        return
    for raw_key in list(node_groups):
        key = str(raw_key)
        if not _soperator_node_group_key_matches_managed(key, all_managed):
            continue
        if _soperator_node_group_key_matches_managed(key, selected_managed):
            continue
        node_groups.pop(raw_key, None)


def _soperator_prune_stale_profile_gpu_clusters(
    *,
    inputs: dict[str, Any],
    profile: Mapping[str, Any],
) -> None:
    gpu_clusters = inputs.get("gpu_clusters")
    if not isinstance(gpu_clusters, dict) or not gpu_clusters:
        return
    all_managed = _soperator_all_profile_managed_gpu_cluster_keys()
    selected_managed = _soperator_profile_managed_gpu_cluster_keys(profile)
    if not all_managed:
        return
    for raw_key in list(gpu_clusters):
        key = str(raw_key)
        if key in all_managed and key not in selected_managed:
            gpu_clusters.pop(raw_key, None)
    if not gpu_clusters:
        inputs.pop("gpu_clusters", None)


def _soperator_selected_gpu_defaults_are_ethernet_only(inputs: Mapping[str, Any]) -> bool:
    defaults = inputs.get("node_group_defaults")
    if not isinstance(defaults, Mapping):
        return False
    gpu_defaults = defaults.get("gpu")
    if not isinstance(gpu_defaults, Mapping):
        return False
    preset = _non_empty_text(gpu_defaults.get("preset")).lower()
    return preset.startswith("1gpu-")


def _soperator_prune_profile_gpu_cluster_path_for_selected_shape(
    *,
    inputs: dict[str, Any],
    profile: Mapping[str, Any],
) -> None:
    if not _soperator_selected_gpu_defaults_are_ethernet_only(inputs):
        return
    managed_keys = _soperator_profile_managed_gpu_cluster_keys(profile)
    if not managed_keys:
        return
    gpu_clusters = inputs.get("gpu_clusters")
    if isinstance(gpu_clusters, dict):
        for raw_key in list(gpu_clusters):
            if str(raw_key) in managed_keys:
                gpu_clusters.pop(raw_key, None)
        if not gpu_clusters:
            inputs.pop("gpu_clusters", None)
    node_groups = inputs.get("node_groups")
    if isinstance(node_groups, dict):
        for group in node_groups.values():
            if not isinstance(group, dict):
                continue
            if _non_empty_text(group.get("gpu_cluster_key")) in managed_keys:
                group.pop("gpu_cluster_key", None)


def _soperator_has_external_node_groups(
    *,
    inputs: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> bool:
    node_groups = inputs.get("node_groups")
    if not isinstance(node_groups, Mapping) or not node_groups:
        return False
    mk8s_profile = _profile_mapping(profile, "mk8s")
    static_role_keys = {str(key) for key in _profile_mapping(mk8s_profile, "node_groups")}
    present_keys = {str(key) for key in node_groups}
    if static_role_keys and not static_role_keys.issubset(present_keys):
        return True
    managed_keys = _soperator_all_profile_managed_node_group_keys()
    for raw_key in node_groups:
        key = str(raw_key)
        if _soperator_node_group_key_matches_managed(key, managed_keys):
            continue
        return True
    return False


def _soperator_placements_cover_existing_profile_groups(
    *,
    inputs: Mapping[str, Any],
    profile: Mapping[str, Any],
    placements: Mapping[str, list[str]],
) -> bool:
    node_groups = inputs.get("node_groups")
    if not isinstance(node_groups, Mapping) or not node_groups or not placements:
        return False
    present_keys = {str(key) for key in node_groups}
    required_placements = [
        str(placement).strip()
        for placement in _soperator_profile_placements(profile)
        if str(placement).strip()
    ]
    if not required_placements:
        return False
    for placement in required_placements:
        group_keys = placements.get(placement, [])
        if not group_keys:
            return False
        if any(str(group_key) not in present_keys for group_key in group_keys):
            return False
    return True


def _raise_if_soperator_production_missing_service_node_groups(
    *,
    inputs: Mapping[str, Any],
    profile: Mapping[str, Any],
    placements: Mapping[str, list[str]],
) -> None:
    node_groups = inputs.get("node_groups")
    if not isinstance(node_groups, Mapping) or not node_groups:
        return
    required_keys = _soperator_profile_static_node_group_keys(profile)
    if not required_keys:
        return
    present_keys = {str(key) for key in node_groups}
    missing_keys = sorted(required_keys - present_keys)
    if not missing_keys:
        return
    if _soperator_placements_cover_existing_profile_groups(
        inputs=inputs,
        profile=profile,
        placements=placements,
    ):
        return
    raise ValueError(
        "apps:soperator production-cluster cannot be added to the selected managed MK8s "
        "target because infra.components[].inputs.node_groups is missing required "
        f"Soperator service-role node groups: {', '.join(missing_keys)}. "
        f"Existing node groups: {', '.join(sorted(present_keys))}. "
        "Use a fresh Soperator production bundle, add the missing service-role node "
        "groups before adding Soperator, or onboard the existing cluster with explicit "
        "placements."
    )


def _mapping_path_value_with_presence(
    node: Mapping[str, Any],
    dotted_path: str,
) -> tuple[bool, Any]:
    current: Any = node
    for raw_segment in dotted_path.split("."):
        segment = raw_segment.strip()
        if not segment or not isinstance(current, Mapping):
            return False, None
        candidates = (segment, segment.replace("-", "_"), segment.replace("_", "-"))
        found = False
        for candidate in candidates:
            if candidate in current:
                current = current[candidate]
                found = True
                break
        if not found:
            return False, None
    return True, current


def _soperator_worker_template_gpu_flags(profile: Mapping[str, Any]) -> dict[str, bool]:
    mk8s_profile = _profile_mapping(profile, "mk8s")
    flags: dict[str, bool] = {}
    for raw_worker in _profile_list(mk8s_profile, "worker_nodesets"):
        if not isinstance(raw_worker, Mapping):
            continue
        gpu = bool(raw_worker.get("gpu", True))
        for field_name in ("name", "nodeset_name", "node_group_prefix"):
            template_name = normalize_component_token(raw_worker.get(field_name))
            if template_name:
                flags[template_name] = gpu
    return flags


def _raise_if_legacy_soperator_worker_inputs(inputs: Mapping[str, Any]) -> None:
    soperator_inputs = inputs.get("soperator")
    if not isinstance(soperator_inputs, Mapping):
        return
    legacy_fields = [
        field for field in _SOPERATOR_LEGACY_WORKER_INPUT_FIELDS if field in soperator_inputs
    ]
    worker_ephemeral_nodes = soperator_inputs.get("worker_ephemeral_nodes")
    if isinstance(worker_ephemeral_nodes, Mapping) and "enabled" in worker_ephemeral_nodes:
        legacy_fields.append("worker_ephemeral_nodes.enabled")
    if legacy_fields:
        joined = ", ".join(f"inputs.soperator.{field}" for field in legacy_fields)
        verb = "is" if len(legacy_fields) == 1 else "are"
        raise ValueError(
            f"{joined} {verb} no longer supported. Use worker_cpu_total_nodes, "
            "worker_cpu_nodes_per_group, worker_gpu_total_nodes, worker_gpu_nodes_per_group, "
            "worker_node_groups.<worker>.autoscaling, and "
            "worker_node_groups.<worker>.ephemeral_nodes instead."
        )


def _soperator_worker_profile_total_nodes(
    *,
    inputs: Mapping[str, Any],
    raw_worker: Mapping[str, Any],
    default_total_nodes: int,
    default_nodes_per_group: int,
) -> int:
    total_nodes_input = _non_empty_text(raw_worker.get("total_nodes_input"))
    node_groups_input = _non_empty_text(raw_worker.get("node_groups_input"))
    nodes_per_group_input = _non_empty_text(raw_worker.get("nodes_per_group_input"))
    if total_nodes_input:
        present, value = _mapping_path_value_with_presence(inputs, total_nodes_input)
        if not present:
            return default_total_nodes
        return _required_positive_int(value, field=total_nodes_input)
    if node_groups_input or nodes_per_group_input:
        groups_present, raw_groups = _mapping_path_value_with_presence(inputs, node_groups_input)
        requested_groups = (
            _required_positive_int(raw_groups, field=node_groups_input) if groups_present else 1
        )
        nodes_present, raw_nodes_per_group = _mapping_path_value_with_presence(
            inputs,
            nodes_per_group_input,
        )
        requested_nodes_per_group = (
            _required_positive_int(raw_nodes_per_group, field=nodes_per_group_input)
            if nodes_present
            else default_nodes_per_group
        )
        return requested_groups * requested_nodes_per_group
    return default_total_nodes


def _soperator_worker_profile_nodes_per_group(
    *,
    inputs: Mapping[str, Any],
    raw_worker: Mapping[str, Any],
    default_nodes_per_group: int,
    max_nodes_per_group: int,
) -> int:
    nodes_per_group_input = _non_empty_text(raw_worker.get("nodes_per_group_input"))
    if not nodes_per_group_input:
        return min(default_nodes_per_group, max_nodes_per_group)
    present, value = _mapping_path_value_with_presence(inputs, nodes_per_group_input)
    requested_nodes_per_group = (
        _required_positive_int(value, field=nodes_per_group_input)
        if present
        else default_nodes_per_group
    )
    if requested_nodes_per_group > max_nodes_per_group:
        nodeset_name = _non_empty_text(raw_worker.get("nodeset_name")) or _non_empty_text(
            raw_worker.get("name")
        )
        suffix = f" for worker_nodesets[{nodeset_name}]" if nodeset_name else ""
        raise ValueError(
            f"{nodes_per_group_input} must be less than or equal to {max_nodes_per_group}{suffix}"
        )
    return requested_nodes_per_group


def _soperator_node_group_shape_defaults(
    *,
    inputs: Mapping[str, Any],
    gpu: bool,
) -> Mapping[str, Any]:
    defaults = inputs.get("node_group_defaults")
    if not isinstance(defaults, Mapping):
        return {}
    shape_key = "gpu" if gpu else "cpu"
    value = defaults.get(shape_key)
    return value if isinstance(value, Mapping) else {}


def _materialize_soperator_node_group_shape(
    *,
    group: dict[str, Any],
    inputs: Mapping[str, Any],
    gpu: bool,
    prefer_shape_defaults: bool = False,
) -> None:
    defaults = _soperator_node_group_shape_defaults(inputs=inputs, gpu=gpu)
    for key in ("platform", "preset", "os", "boot_disk", "public_ips", "preemptible"):
        if key not in defaults:
            continue
        if key == "boot_disk" and isinstance(defaults[key], Mapping):
            current = group.get("boot_disk")
            merged = dict(current) if isinstance(current, Mapping) else {}
            for disk_key, disk_value in defaults[key].items():
                if disk_value is None:
                    continue
                if prefer_shape_defaults:
                    merged[str(disk_key)] = copy.deepcopy(disk_value)
                else:
                    merged.setdefault(str(disk_key), copy.deepcopy(disk_value))
            if merged:
                group["boot_disk"] = merged
            continue
        if prefer_shape_defaults:
            group[key] = copy.deepcopy(defaults[key])
        else:
            group.setdefault(key, copy.deepcopy(defaults[key]))
    if gpu:
        default_stack_source = _non_empty_text(defaults.get("gpu_stack_source"))
        if prefer_shape_defaults and default_stack_source:
            group["gpu_stack_source"] = default_stack_source
        else:
            group.setdefault("gpu_stack_source", default_stack_source or "nebius_image")
        if _non_empty_text(defaults.get("gpu_stack_preset")):
            if prefer_shape_defaults:
                group["gpu_stack_preset"] = defaults["gpu_stack_preset"]
            else:
                group.setdefault("gpu_stack_preset", defaults["gpu_stack_preset"])
        default_reservation = defaults.get("reservation")
        if isinstance(default_reservation, Mapping):
            reservation_policy = _non_empty_text(default_reservation.get("policy")).upper()
            if reservation_policy:
                if prefer_shape_defaults or not isinstance(group.get("reservation"), Mapping):
                    group["reservation"] = {"policy": reservation_policy}
                else:
                    reservation = group.setdefault("reservation", {})
                    if isinstance(reservation, dict):
                        reservation.setdefault("policy", reservation_policy)


def _materialize_soperator_node_group_labels(group: dict[str, Any], *, group_key: str) -> None:
    node_labels = group.setdefault("node_labels", {})
    if not isinstance(node_labels, dict):
        return
    nodeset_name = _non_empty_text(group.get("nodeset_name"))
    placement_name = _non_empty_text(group.get("placement_name"))
    workload = _non_empty_text(group.get("workload"))
    if group_key:
        node_labels.setdefault("nebius.com/node-group", group_key)
    if nodeset_name:
        node_labels.setdefault("slurm.nebius.ai/nodeset-name", nodeset_name)
    elif placement_name:
        node_labels.setdefault("slurm.nebius.ai/nodeset-name", placement_name)
    if workload:
        node_labels.setdefault("slurm.nebius.ai/workload", workload)
    if bool(group.get("jail", False)):
        node_labels.setdefault("slurm.nebius.ai/jail", "true")
    if bool(group.get("gpu", False)):
        node_labels.setdefault("nebius.com/gpu", "true")


def _materialize_soperator_node_group_filesystems(group: dict[str, Any]) -> None:
    filesystem_keys: list[str] = []
    if bool(group.get("jail", False)):
        filesystem_keys.append("jail")
    explicit_keys = group.get("sfs_filesystem_keys", group.get("filesystem_keys"))
    if isinstance(explicit_keys, str):
        filesystem_keys.extend(key.strip() for key in explicit_keys.split(",") if key.strip())
    elif isinstance(explicit_keys, list):
        filesystem_keys.extend(str(key).strip() for key in explicit_keys if str(key).strip())
    if filesystem_keys:
        group["sfs_filesystem_keys"] = list(dict.fromkeys(filesystem_keys))


def _soperator_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _soperator_profile_placements(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = profile.get("placements")
    return raw if isinstance(raw, Mapping) else {}


def _soperator_placement_list(value: Any) -> list[str]:
    return list(dict.fromkeys(_soperator_string_list(value)))


def _soperator_row_placements(row: Mapping[str, Any]) -> dict[str, list[str]]:
    values = row.get("values")
    if isinstance(values, Mapping) and "nodeGroupMapping" in values:
        raise ValueError(
            "apps.charts[] soperator values.nodeGroupMapping is no longer supported; "
            "use apps.charts[].placements instead."
        )
    raw = row.get("placements")
    if not isinstance(raw, Mapping):
        return {}
    mapping: dict[str, list[str]] = {}
    for raw_placement, raw_groups in raw.items():
        placement = str(raw_placement).strip()
        groups = _soperator_placement_list(raw_groups)
        if placement and groups:
            mapping[placement] = groups
    return mapping


def _soperator_store_placement_value(placement: str, groups: Sequence[str]) -> str | list[str]:
    normalized = [str(group).strip() for group in groups if str(group).strip()]
    if len(normalized) == 1 and not placement.startswith("worker"):
        return normalized[0]
    return normalized


def _soperator_set_app_placements(
    row: dict[str, Any],
    mapping: Mapping[str, list[str]],
    *,
    replace: bool = False,
) -> None:
    if not mapping:
        return
    if replace:
        row["placements"] = {
            str(placement): _soperator_store_placement_value(str(placement), groups)
            for placement, groups in mapping.items()
            if groups
        }
        return
    target = row.setdefault("placements", {})
    if not isinstance(target, dict):
        target = {}
        row["placements"] = target
    for placement, groups in mapping.items():
        if placement == "worker" and any(
            str(existing_placement).startswith("worker-")
            for existing_placement in target
            if str(existing_placement).strip()
        ):
            continue
        if placement not in target and groups:
            target[placement] = _soperator_store_placement_value(placement, groups)


def _soperator_node_group_keys_by_kind(
    inputs: Mapping[str, Any],
    *,
    kind: str,
) -> list[str]:
    groups = iter_mk8s_node_groups(inputs)
    normalized_kind = kind.strip().lower()
    if normalized_kind == "gpu":
        selected = [group.key for group in groups if group.gpu]
    elif normalized_kind == "cpu":
        selected = [group.key for group in groups if not group.gpu]
    else:
        selected = [group.key for group in groups]
    return list(dict.fromkeys(selected))


def _soperator_role_nodeset_template_names(raw_role_config: Mapping[str, Any]) -> list[str]:
    template_names = _soperator_string_list(raw_role_config.get("nodeset_templates"))
    if not template_names:
        template_name = _non_empty_text(raw_role_config.get("nodeset_template"))
        template_names = [template_name] if template_name else []
    return list(dict.fromkeys(template_names))


def _soperator_worker_nodeset_key_prefixes(profile: Mapping[str, Any]) -> dict[str, str]:
    mk8s_profile = _profile_mapping(profile, "mk8s")
    prefixes: dict[str, str] = {}
    for raw_worker in _profile_list(mk8s_profile, "worker_nodesets"):
        if not isinstance(raw_worker, Mapping):
            continue
        nodeset_name = _non_empty_text(raw_worker.get("nodeset_name")) or _non_empty_text(
            raw_worker.get("name")
        )
        worker_name = _non_empty_text(raw_worker.get("name"))
        key_prefix = _non_empty_text(raw_worker.get("node_group_prefix")) or nodeset_name
        if nodeset_name and key_prefix:
            prefixes[nodeset_name] = key_prefix
        if worker_name and key_prefix:
            prefixes.setdefault(worker_name, key_prefix)
    return prefixes


def _soperator_node_group_keys_by_nodeset_templates(
    inputs: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
    template_names: Sequence[str],
) -> list[str]:
    node_groups = inputs.get("node_groups")
    if not isinstance(node_groups, Mapping) or not template_names:
        return []
    key_prefixes = _soperator_worker_nodeset_key_prefixes(profile)
    selected: list[str] = []
    for template_name in template_names:
        selected.extend(
            _soperator_nodeset_group_keys(
                node_groups,
                nodeset_name=template_name,
                key_prefix=key_prefixes.get(template_name, template_name),
            )
        )
    return list(dict.fromkeys(selected))


def _soperator_infer_placements(
    *,
    inputs: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, list[str]]:
    placements = _soperator_profile_placements(profile)
    if not placements or not iter_mk8s_node_groups(inputs):
        return {}

    inferred: dict[str, list[str]] = {}
    all_keys = _soperator_node_group_keys_by_kind(inputs, kind="all")
    for placement, raw_placement_config in placements.items():
        if not isinstance(raw_placement_config, Mapping):
            continue
        placement_name = str(placement).strip()
        if not placement_name:
            continue
        explicit_node_group = _non_empty_text(raw_placement_config.get("node_group"))
        exact = [key for key in all_keys if key == (explicit_node_group or placement_name)]
        if exact:
            inferred[placement_name] = exact
            continue
        template_selected = _soperator_node_group_keys_by_nodeset_templates(
            inputs,
            profile=profile,
            template_names=_soperator_role_nodeset_template_names(raw_placement_config),
        )
        if template_selected:
            inferred[placement_name] = template_selected
            continue
        selector = _non_empty_text(raw_placement_config.get("default_node_group_kind")) or "all"
        selected = _soperator_node_group_keys_by_kind(inputs, kind=selector)
        if not selected:
            fallback_selector = _non_empty_text(
                raw_placement_config.get("fallback_node_group_kind")
            )
            if fallback_selector:
                selected = _soperator_node_group_keys_by_kind(inputs, kind=fallback_selector)
        if selected:
            inferred[placement_name] = selected
    return inferred


def _soperator_placements_match_generated_profile(
    *,
    row: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> bool:
    placements = _soperator_row_placements(row)
    if not placements:
        return False
    _default_profile, profiles = _soperator_nodesets_profiles()
    for profile in profiles.values():
        if not isinstance(profile, Mapping):
            continue
        inferred = _soperator_infer_placements(inputs=inputs, profile=profile)
        if inferred and placements == inferred:
            return True
    return False


def _soperator_profile_template_placement_refs(
    profile: Mapping[str, Any],
    *,
    placement_name: str,
) -> set[str]:
    refs: set[str] = set()
    raw_config = _soperator_profile_placements(profile).get(placement_name)
    if isinstance(raw_config, Mapping):
        explicit_node_group = _non_empty_text(raw_config.get("node_group"))
        if explicit_node_group:
            refs.add(explicit_node_group)
        refs.update(_soperator_role_nodeset_template_names(raw_config))
    if placement_name:
        refs.add(placement_name)
    mk8s_profile = _profile_mapping(profile, "mk8s")
    if placement_name in _profile_mapping(mk8s_profile, "node_groups"):
        refs.add(placement_name)
    for raw_worker in _profile_list(mk8s_profile, "worker_nodesets"):
        if not isinstance(raw_worker, Mapping):
            continue
        for field_name in ("name", "nodeset_name", "node_group_prefix"):
            ref = _non_empty_text(raw_worker.get(field_name))
            if ref:
                refs.add(ref)
    return refs


def _soperator_placements_are_stale_profile_templates(
    *,
    placements: Mapping[str, list[str]],
    profile: Mapping[str, Any],
    inferred_placements: Mapping[str, list[str]],
) -> bool:
    if not placements or not inferred_placements:
        return False
    profile_placements = _soperator_profile_placements(profile)
    stale = False
    for placement_name, refs in placements.items():
        if placement_name not in profile_placements:
            return False
        inferred_refs = set(inferred_placements.get(placement_name, []))
        if not inferred_refs:
            return False
        template_refs = _soperator_profile_template_placement_refs(
            profile,
            placement_name=placement_name,
        )
        if not refs or not set(refs) <= (template_refs | inferred_refs):
            return False
        if list(refs) != list(inferred_placements.get(placement_name, [])):
            stale = True
    return stale


def _soperator_role_filesystem_keys(raw_role_config: Mapping[str, Any]) -> list[str]:
    keys = _soperator_string_list(raw_role_config.get("filesystem_keys"))
    return list(dict.fromkeys(keys))


def _soperator_append_filesystem_keys(group: dict[str, Any], keys: Sequence[str]) -> None:
    if not keys:
        return
    existing = _soperator_string_list(
        group.get("sfs_filesystem_keys", group.get("filesystem_keys"))
    )
    group["sfs_filesystem_keys"] = list(dict.fromkeys([*existing, *keys]))


def _soperator_apply_filesystem_node_labels(group: dict[str, Any], keys: Sequence[str]) -> None:
    if "jail" not in keys:
        return
    labels = group.setdefault("node_labels", {})
    if isinstance(labels, dict):
        labels.setdefault("slurm.nebius.ai/jail", "true")


def _soperator_apply_node_group_label(group: dict[str, Any], *, group_key: str) -> None:
    if not group_key:
        return
    labels = group.setdefault("node_labels", {})
    if isinstance(labels, dict):
        labels.setdefault("nebius.com/node-group", group_key)


def _soperator_node_group_selector_expression(
    inputs: Mapping[str, Any],
    group_key: str,
) -> dict[str, Any]:
    node_groups = inputs.get("node_groups")
    group = node_groups.get(group_key) if isinstance(node_groups, Mapping) else None
    if isinstance(group, Mapping):
        selector = group.get("selector")
        if isinstance(selector, Mapping):
            selector_key = _non_empty_text(selector.get("key"))
            selector_operator = _non_empty_text(selector.get("operator")) or "In"
            selector_values = _soperator_string_list(selector.get("values"))
            selector_value = _non_empty_text(selector.get("value"))
            if selector_value and not selector_values:
                selector_values = [selector_value]
            if selector_key and selector_values:
                return {
                    "key": selector_key,
                    "operator": selector_operator,
                    "values": selector_values,
                }
        labels = group.get("labels")
        if isinstance(labels, Mapping):
            value = _non_empty_text(labels.get("nebius.com/node-group"))
            if value:
                return {"key": "nebius.com/node-group", "operator": "In", "values": [value]}
            for fallback_key in (
                "nebius.com/node-group-id",
                "yandex.cloud/node-group-id",
                "node.kubernetes.io/instance-type",
            ):
                fallback_value = _non_empty_text(labels.get(fallback_key))
                if fallback_value:
                    return {"key": fallback_key, "operator": "In", "values": [fallback_value]}
        labels = group.get("node_labels")
        if isinstance(labels, Mapping):
            value = _non_empty_text(labels.get("nebius.com/node-group"))
            if value:
                return {"key": "nebius.com/node-group", "operator": "In", "values": [value]}
    return {"key": "nebius.com/node-group", "operator": "In", "values": [group_key]}


def _soperator_node_group_selector_terms(
    inputs: Mapping[str, Any],
    group_keys: Sequence[str],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[str]] = {}
    expressions: list[dict[str, Any]] = []
    for group_key in dict.fromkeys(key for key in group_keys if key):
        expression = _soperator_node_group_selector_expression(inputs, group_key)
        key = _non_empty_text(expression.get("key"))
        operator = _non_empty_text(expression.get("operator")) or "In"
        values = _soperator_string_list(expression.get("values"))
        if key and operator == "In" and values:
            bucket = grouped.setdefault((key, operator), [])
            for value in values:
                if value not in bucket:
                    bucket.append(value)
            continue
        if expression not in expressions:
            expressions.append(expression)
    for (key, operator), values in grouped.items():
        expressions.append({"key": key, "operator": operator, "values": values})
    return [{"matchExpressions": expressions}] if expressions else []


def _materialize_soperator_existing_node_group_mapping(
    *,
    inputs: dict[str, Any],
    profile: Mapping[str, Any],
    mapping: Mapping[str, list[str]],
) -> None:
    node_groups = inputs.get("node_groups")
    if not isinstance(node_groups, dict):
        return
    placements = _soperator_profile_placements(profile)
    groups_with_explicit_sfs_keys = {
        str(group_key)
        for group_key, group in node_groups.items()
        if isinstance(group, dict)
        and _soperator_string_list(group.get("sfs_filesystem_keys", group.get("filesystem_keys")))
    }
    for placement, group_keys in mapping.items():
        raw_placement_config = placements.get(placement)
        if not isinstance(raw_placement_config, Mapping) and str(placement).startswith("worker"):
            raw_placement_config = placements.get("worker")
        if not isinstance(raw_placement_config, Mapping):
            continue
        filesystem_keys = _soperator_role_filesystem_keys(raw_placement_config)
        for group_key in group_keys:
            group = node_groups.get(group_key)
            if isinstance(group, dict):
                _soperator_apply_node_group_label(group, group_key=group_key)
                if group_key in groups_with_explicit_sfs_keys:
                    explicit_keys = _soperator_string_list(
                        group.get("sfs_filesystem_keys", group.get("filesystem_keys"))
                    )
                    group["sfs_filesystem_keys"] = list(dict.fromkeys(explicit_keys))
                else:
                    _soperator_append_filesystem_keys(group, filesystem_keys)
                _soperator_apply_filesystem_node_labels(group, filesystem_keys)


def _soperator_toleration_effect(value: Any) -> str | None:
    text = _non_empty_text(value)
    if not text:
        return None
    normalized = text.replace("-", "_").replace(" ", "_").upper()
    return {
        "NO_SCHEDULE": "NoSchedule",
        "PREFER_NO_SCHEDULE": "PreferNoSchedule",
        "NO_EXECUTE": "NoExecute",
    }.get(normalized, text)


def _soperator_toleration_from_taint(taint: Mapping[str, Any]) -> dict[str, Any] | None:
    key = _non_empty_text(taint.get("key"))
    if not key:
        return None
    value = _non_empty_text(taint.get("value"))
    toleration: dict[str, Any] = {
        "key": key,
        "operator": "Equal" if value else "Exists",
    }
    if value:
        toleration["value"] = value
    effect = _soperator_toleration_effect(taint.get("effect"))
    if effect:
        toleration["effect"] = effect
    return toleration


def _soperator_node_group_tolerations(
    inputs: Mapping[str, Any],
    group_keys: Sequence[str],
) -> list[dict[str, Any]]:
    node_groups = inputs.get("node_groups")
    if not isinstance(node_groups, Mapping):
        return []
    collected: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for group_key in group_keys:
        group = node_groups.get(group_key)
        if not isinstance(group, Mapping):
            continue
        taints = group.get("taints")
        if not isinstance(taints, list):
            continue
        for taint in taints:
            if not isinstance(taint, Mapping):
                continue
            toleration = _soperator_toleration_from_taint(taint)
            if not toleration:
                continue
            identity = _soperator_toleration_identity(toleration)
            if identity in seen:
                continue
            collected.append(toleration)
            seen.add(identity)
    return collected


def _soperator_merge_tolerations(
    existing: Any,
    additions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged = (
        [to_plain_data(item) for item in existing if isinstance(item, Mapping)]
        if isinstance(existing, list)
        else []
    )
    seen = {_soperator_toleration_identity(item) for item in merged}
    for addition in additions:
        item = to_plain_data(addition)
        identity = _soperator_toleration_identity(item)
        if identity in seen:
            continue
        merged.append(item)
        seen.add(identity)
    return merged


def _soperator_profile_node_group_count_input(
    *,
    inputs: Mapping[str, Any],
    raw_group: Mapping[str, Any],
) -> int | None:
    node_count_input = _non_empty_text(raw_group.get("node_count_input"))
    if not node_count_input:
        return None
    raw_value = _mapping_path_value(inputs, node_count_input)
    if raw_value is None:
        return None
    default_node_count = _positive_int(raw_group.get("node_count"), default=1)
    return _positive_int(raw_value, default=default_node_count)


def _soperator_profile_autoscaling_input(
    *,
    inputs: Mapping[str, Any],
    raw_group: Mapping[str, Any],
    default_min_node_count: int,
    default_max_node_count: int,
    min_max_node_count: int = 0,
) -> dict[str, int] | None:
    autoscaling_input = _non_empty_text(raw_group.get("autoscaling_input"))
    if not autoscaling_input:
        return None
    raw_value = _mapping_path_value(inputs, autoscaling_input)
    if not isinstance(raw_value, Mapping):
        return None
    if not _config_bool(raw_value.get("enabled"), default=False):
        return None
    min_default = max(0, default_min_node_count)
    max_default = max(min_default, default_max_node_count)
    min_node_count = (
        min_default
        if raw_value.get("min_node_count") is None
        else _required_nonnegative_int(
            raw_value.get("min_node_count"),
            field=f"{autoscaling_input}.min_node_count",
        )
    )
    max_node_count = (
        max_default
        if raw_value.get("max_node_count") is None
        else _required_nonnegative_int(
            raw_value.get("max_node_count"),
            field=f"{autoscaling_input}.max_node_count",
        )
    )
    if max_node_count < min_node_count:
        raise ValueError(
            f"{autoscaling_input}.max_node_count must be greater than or equal to "
            f"{autoscaling_input}.min_node_count"
        )
    if max_node_count < min_max_node_count:
        raise ValueError(
            f"{autoscaling_input}.max_node_count must be at least {min_max_node_count}"
        )
    return {
        "min_node_count": min_node_count,
        "max_node_count": max_node_count,
    }


def _soperator_set_node_group_scale(
    group: dict[str, Any],
    *,
    node_count: int,
    autoscaling: Mapping[str, Any] | None,
) -> None:
    if autoscaling:
        group.pop("node_count", None)
        group["autoscaling"] = {
            "min_node_count": int(autoscaling["min_node_count"]),
            "max_node_count": int(autoscaling["max_node_count"]),
        }
        return
    group.pop("autoscaling", None)
    group["node_count"] = node_count


def _soperator_profile_node_group_defaults(
    profile_node_groups: Mapping[str, Any],
) -> dict[str, Any]:
    materialized: dict[str, Any] = {}
    for group_key, raw_group in profile_node_groups.items():
        if not isinstance(raw_group, Mapping):
            materialized[group_key] = copy.deepcopy(to_plain_data(raw_group))
            continue
        group = copy.deepcopy(to_plain_data(raw_group))
        if isinstance(group, dict):
            group.pop("node_count_input", None)
            group.pop("autoscaling_input", None)
        materialized[group_key] = group
    return materialized


def _soperator_apply_profile_node_group_count_inputs(
    *,
    inputs: Mapping[str, Any],
    node_groups: dict[str, Any],
    profile_node_groups: Mapping[str, Any],
) -> None:
    for group_key, raw_group in profile_node_groups.items():
        if not isinstance(raw_group, Mapping):
            continue
        group = node_groups.get(group_key)
        if isinstance(group, dict):
            group.pop("node_count_input", None)
            group.pop("autoscaling_input", None)
        node_count = _soperator_profile_node_group_count_input(
            inputs=inputs,
            raw_group=raw_group,
        )
        if isinstance(group, dict):
            default_node_count = _positive_int(raw_group.get("node_count"), default=1)
            scale_node_count = node_count if node_count is not None else default_node_count
            default_autoscaling = raw_group.get("autoscaling")
            default_autoscaling_min_node_count = scale_node_count
            default_autoscaling_max_node_count = scale_node_count
            if isinstance(default_autoscaling, Mapping):
                default_autoscaling_min_node_count = _positive_int(
                    default_autoscaling.get("min_node_count"),
                    default=scale_node_count,
                )
                default_autoscaling_max_node_count = _positive_int(
                    default_autoscaling.get("max_node_count"),
                    default=max(default_autoscaling_min_node_count, scale_node_count),
                )
            autoscaling = _soperator_profile_autoscaling_input(
                inputs=inputs,
                raw_group=raw_group,
                default_min_node_count=default_autoscaling_min_node_count,
                default_max_node_count=default_autoscaling_max_node_count,
                min_max_node_count=1,
            )
            autoscaling_input = _non_empty_text(raw_group.get("autoscaling_input"))
            raw_autoscaling_input = (
                _mapping_path_value(inputs, autoscaling_input) if autoscaling_input else None
            )
            if (
                autoscaling is None
                and node_count is None
                and not isinstance(raw_autoscaling_input, Mapping)
                and isinstance(default_autoscaling, Mapping)
            ):
                autoscaling = {
                    "min_node_count": default_autoscaling_min_node_count,
                    "max_node_count": default_autoscaling_max_node_count,
                }
            _soperator_set_node_group_scale(
                group,
                node_count=scale_node_count,
                autoscaling=autoscaling,
            )


def _soperator_node_group_filter(
    *,
    name: str,
    group_keys: Sequence[str],
    inputs: Mapping[str, Any],
    tolerations: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    filter_item: dict[str, Any] = {
        "name": name,
        "affinity": {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": _soperator_node_group_selector_terms(inputs, group_keys)
                }
            }
        },
    }
    merged_tolerations = _soperator_merge_tolerations([], tolerations or [])
    if merged_tolerations:
        filter_item["tolerations"] = merged_tolerations
    return filter_item


def _soperator_storage_value_key(filesystem_key: str) -> str | None:
    return {
        "accounting": "accounting",
        "controller-spool": "controllerSpool",
        "jail": "jail",
    }.get(filesystem_key)


def _soperator_set_storage_node_group_selector(
    values: dict[str, Any],
    *,
    filesystem_key: str,
    group_keys: Sequence[str],
    inputs: Mapping[str, Any],
    tolerations: Sequence[Mapping[str, Any]] | None = None,
    include_profile_jail_aliases: bool = False,
    preserve_existing_match_expressions: bool = False,
) -> None:
    storage_key = _soperator_storage_value_key(filesystem_key)
    selected_group_keys = list(group_keys)
    if filesystem_key == "jail" and include_profile_jail_aliases:
        selected_group_keys.extend(_soperator_all_profile_jail_node_group_keys())
    selected_groups = [group for group in dict.fromkeys(selected_group_keys) if group]
    if not storage_key or not selected_groups:
        return
    storage = values.setdefault("storage", {})
    if not isinstance(storage, dict):
        storage = {}
        values["storage"] = storage
    storage_section = storage.setdefault(storage_key, {})
    if not isinstance(storage_section, dict):
        storage_section = {}
        storage[storage_key] = storage_section
    existing_match_expressions = storage_section.get("matchExpressions")
    if not (
        preserve_existing_match_expressions
        and isinstance(existing_match_expressions, list)
        and existing_match_expressions
    ):
        storage_section["matchExpressions"] = [
            expression
            for term in _soperator_node_group_selector_terms(inputs, selected_groups)
            for expression in term.get("matchExpressions", [])
        ]
    merged_tolerations = _soperator_merge_tolerations(
        storage_section.get("tolerations"),
        tolerations or [],
    )
    if merged_tolerations:
        storage_section["tolerations"] = merged_tolerations


def _soperator_upsert_filter(
    values: dict[str, Any],
    *,
    filter_item: Mapping[str, Any],
) -> None:
    filters = values.setdefault("k8sNodeFilters", [])
    if not isinstance(filters, list):
        filters = []
        values["k8sNodeFilters"] = filters
    name = _non_empty_text(filter_item.get("name"))
    if not name:
        return
    for index, existing in enumerate(filters):
        if isinstance(existing, Mapping) and _non_empty_text(existing.get("name")) == name:
            merged = copy.deepcopy(dict(existing))
            _merge_missing_mapping(merged, filter_item)
            for field in ("affinity", "nodeSelector", "tolerations"):
                if field in filter_item:
                    merged[field] = copy.deepcopy(filter_item[field])
            if "affinity" in filter_item:
                merged.pop("nodeSelector", None)
            if "nodeSelector" in filter_item:
                merged.pop("affinity", None)
            filters[index] = merged
            return
    filters.append(copy.deepcopy(dict(filter_item)))


def _soperator_filter_by_name(values: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    filters = values.get("k8sNodeFilters")
    if not isinstance(filters, list):
        return None
    for item in filters:
        if isinstance(item, Mapping) and _non_empty_text(item.get("name")) == name:
            return item
    return None


def _soperator_set_mapping_path(values: dict[str, Any], path: str, value: Any) -> None:
    current = values
    parts = [part for part in path.split(".") if part]
    if not parts:
        return
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = copy.deepcopy(value)


def _soperator_parse_preset_resources(preset: str) -> dict[str, int]:
    match = re.search(
        r"(?:(?P<gpu>[0-9]+)gpu-)?(?P<vcpu>[0-9]+)vcpu-(?P<memory>[0-9]+)gb",
        preset,
    )
    if not match:
        return {}
    parsed: dict[str, int] = {}
    for key in ("gpu", "vcpu", "memory"):
        value = match.group(key)
        if value:
            parsed[key] = int(value)
    return parsed


def _soperator_node_group_resource_preset(group: Mapping[str, Any]) -> str:
    preset = _non_empty_text(group.get("preset"))
    if preset:
        return preset
    labels = group.get("labels")
    if isinstance(labels, Mapping):
        return _non_empty_text(labels.get("nebius.com/resource-preset"))
    return ""


def _soperator_allocatable_gpu_count(group: Mapping[str, Any]) -> int | None:
    allocatable = group.get("allocatable")
    if not isinstance(allocatable, Mapping):
        return None
    value = allocatable.get("nvidia.com/gpu")
    if value is None:
        return None
    with suppress(TypeError, ValueError):
        gpu_count = int(str(value).strip())
        if gpu_count >= 0:
            return gpu_count
    return None


def _soperator_parse_cpu_millicores(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("m"):
        with suppress(ValueError):
            millicores = int(text[:-1])
            return millicores if millicores >= 0 else None
        return None
    with suppress(ValueError):
        cores = float(text)
        if cores >= 0:
            return int(cores * 1000)
    return None


def _soperator_parse_memory_mib(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"([0-9]+)([KMGTE]i?|[kmgte]i?|[KMGTE]B?|[kmgte]b?)?", text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = (match.group(2) or "Mi").lower()
    if unit in {"ki", "k", "kb"}:
        return max(1, amount // 1024)
    if unit in {"mi", "m", "mb"}:
        return amount
    if unit in {"gi", "g", "gb"}:
        return amount * 1024
    if unit in {"ti", "t", "tb"}:
        return amount * 1024 * 1024
    if unit in {"ei", "e", "eb"}:
        return amount * 1024 * 1024 * 1024
    return None


def _soperator_allocatable_cpu_millicores(group: Mapping[str, Any]) -> int | None:
    allocatable = group.get("allocatable")
    if not isinstance(allocatable, Mapping):
        return None
    return _soperator_parse_cpu_millicores(allocatable.get("cpu"))


def _soperator_allocatable_memory_mib(group: Mapping[str, Any]) -> int | None:
    allocatable = group.get("allocatable")
    if not isinstance(allocatable, Mapping):
        return None
    return _soperator_parse_memory_mib(allocatable.get("memory"))


def _soperator_node_group_resources(group: Mapping[str, Any]) -> dict[str, int]:
    resources = _soperator_parse_preset_resources(_soperator_node_group_resource_preset(group))
    gpu_count = _soperator_allocatable_gpu_count(group)
    if gpu_count is not None:
        resources["gpu"] = gpu_count
    cpu_millicores = _soperator_allocatable_cpu_millicores(group)
    if cpu_millicores is not None:
        resources["vcpu_millicores"] = cpu_millicores
    elif "vcpu" in resources:
        resources["vcpu_millicores"] = resources["vcpu"] * 1000
    memory_mib = _soperator_allocatable_memory_mib(group)
    if memory_mib is not None:
        resources["memory_mib"] = memory_mib
    elif "memory" in resources:
        resources["memory_mib"] = resources["memory"] * 1024
    return resources


def _soperator_resource_cpu_value(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("m"):
        with suppress(ValueError):
            return max(1, int(text[:-1]) // 1000)
        return None
    with suppress(ValueError):
        return int(float(text))
    return None


def _soperator_format_cpu_millicores(value: int) -> str:
    if value % 1000 == 0:
        return str(value // 1000)
    return f"{value}m"


def _soperator_format_memory_mib(value: int) -> str:
    if value % 1024 == 0:
        return f"{value // 1024}Gi"
    return f"{value}Mi"


def _soperator_cpu_node_config_static(cpu_millicores: int | None) -> str:
    if cpu_millicores is None or cpu_millicores <= 0:
        cpu_count = 1
    else:
        cpu_count = max(1, math.ceil(cpu_millicores / 1000))
    if cpu_count > 1 and cpu_count % 2 == 0:
        cores_per_socket = max(1, cpu_count // 2)
        threads_per_core = 2
    else:
        cores_per_socket = cpu_count
        threads_per_core = 1
    return (
        "Boards=1 SocketsPerBoard=1 "
        f"CoresPerSocket={cores_per_socket} ThreadsPerCore={threads_per_core}"
    )


def _soperator_node_config_static_cpu_count(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None

    def _field(name: str, default: int | None = None) -> int | None:
        match = re.search(rf"(?:^|\s){re.escape(name)}=([0-9]+)(?:\s|$)", text)
        if match is None:
            return default
        with suppress(ValueError):
            return int(match.group(1))
        return default

    explicit_cpus = _field("CPUs")
    if explicit_cpus is not None:
        return explicit_cpus
    cores_per_socket = _field("CoresPerSocket")
    if cores_per_socket is None:
        return None
    return (
        (_field("Boards", 1) or 1)
        * (_field("SocketsPerBoard", 1) or 1)
        * cores_per_socket
        * (_field("ThreadsPerCore", 1) or 1)
    )


def _soperator_fit_gpu_node_config_to_group(
    nodeset: dict[str, Any],
    *,
    group_cpu_millicores: int | None,
    slurmd_cpu_millicores: int | None,
    gpu_count: int | None,
) -> None:
    if group_cpu_millicores is None or group_cpu_millicores <= 0 or gpu_count is None:
        return
    node_config = nodeset.setdefault("nodeConfig", {})
    if not isinstance(node_config, dict):
        return
    static_cpu_count = _soperator_node_config_static_cpu_count(node_config.get("static"))
    group_cpu_count = max(1, math.ceil(group_cpu_millicores / 1000))
    if static_cpu_count is not None and static_cpu_count <= group_cpu_count:
        return
    effective_cpu_millicores = slurmd_cpu_millicores or group_cpu_millicores
    node_config["static"] = (
        f"{_soperator_cpu_node_config_static(effective_cpu_millicores)} Gres=gpu:{gpu_count}"
    )


def _soperator_resource_memory_gib(value: Any) -> int | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"([0-9]+)(?:Gi|G|GB|gb)?", text)
    if not match:
        return None
    return int(match.group(1))


def _soperator_fit_nodeset_resources_to_group(
    nodeset: dict[str, Any],
    *,
    group_key: str,
    inputs: Mapping[str, Any],
    install_mode: str = "",
) -> None:
    node_groups = inputs.get("node_groups")
    group = node_groups.get(group_key) if isinstance(node_groups, Mapping) else None
    if not isinstance(group, Mapping):
        return
    resources = _soperator_node_group_resources(group)
    slurmd = nodeset.get("slurmd")
    slurmd_resources = slurmd.get("resources") if isinstance(slurmd, Mapping) else None
    if not isinstance(slurmd_resources, dict):
        return

    gpu_count = resources.get("gpu")
    gpu_config = nodeset.get("gpu")
    gpu_enabled = isinstance(gpu_config, Mapping) and bool(gpu_config.get("enabled", False))
    if gpu_enabled and gpu_count is not None:
        slurmd_resources["gpu"] = gpu_count

    cpu_millicores = resources.get("vcpu_millicores")
    current_cpu = _soperator_parse_cpu_millicores(slurmd_resources.get("cpu"))
    if install_mode == _SOPERATOR_TARGET_MODE_REGISTERED:
        if current_cpu is not None and current_cpu > 500:
            slurmd_resources["cpu"] = "500m"
        current_memory = _soperator_parse_memory_mib(slurmd_resources.get("memory"))
        if current_memory is not None and current_memory > 1024:
            slurmd_resources["memory"] = "1024Mi"
        if not gpu_enabled:
            node_config = nodeset.setdefault("nodeConfig", {})
            if isinstance(node_config, dict):
                node_config["static"] = _soperator_cpu_node_config_static(cpu_millicores)
        return

    if cpu_millicores is not None and (
        not gpu_enabled or (current_cpu is not None and current_cpu > cpu_millicores)
    ):
        target_cpu_millicores = max(1000, int(cpu_millicores * 0.75))
        slurmd_resources["cpu"] = _soperator_format_cpu_millicores(target_cpu_millicores)
        current_cpu = _soperator_parse_cpu_millicores(slurmd_resources.get("cpu"))

    memory_mib = resources.get("memory_mib")
    current_memory = _soperator_parse_memory_mib(slurmd_resources.get("memory"))
    if memory_mib is not None and (
        not gpu_enabled or (current_memory is not None and current_memory > memory_mib)
    ):
        target_memory_mib = max(1024, int(memory_mib * 0.75))
        slurmd_resources["memory"] = _soperator_format_memory_mib(target_memory_mib)

    # Keep the legacy whole-node fallback for managed profile presets that do
    # not expose allocatable data.
    if "vcpu_millicores" not in resources:
        vcpu_count = resources.get("vcpu")
        legacy_current_cpu = _soperator_resource_cpu_value(slurmd_resources.get("cpu"))
        if (
            vcpu_count is not None
            and legacy_current_cpu is not None
            and legacy_current_cpu > vcpu_count
        ):
            slurmd_resources["cpu"] = str(max(1, vcpu_count // 2))
            current_cpu = _soperator_parse_cpu_millicores(slurmd_resources.get("cpu"))
    if "memory_mib" not in resources:
        memory_gib = resources.get("memory")
        legacy_current_memory = _soperator_resource_memory_gib(slurmd_resources.get("memory"))
        if (
            memory_gib is not None
            and legacy_current_memory is not None
            and legacy_current_memory > memory_gib
        ):
            slurmd_resources["memory"] = f"{max(1, memory_gib // 4)}Gi"

    if gpu_enabled:
        _soperator_fit_gpu_node_config_to_group(
            nodeset,
            group_cpu_millicores=cpu_millicores,
            slurmd_cpu_millicores=current_cpu,
            gpu_count=gpu_count,
        )
    else:
        node_config = nodeset.setdefault("nodeConfig", {})
        if isinstance(node_config, dict):
            node_config["static"] = _soperator_cpu_node_config_static(current_cpu or cpu_millicores)


def _soperator_template_nodesets_by_name(values: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    nodesets = values.get("nodesets")
    if not isinstance(nodesets, list):
        return {}
    templates: dict[str, Mapping[str, Any]] = {}
    for item in nodesets:
        if not isinstance(item, Mapping):
            continue
        name = _non_empty_text(item.get("name"))
        if name:
            templates[name] = item
    return templates


def _soperator_toleration_identity(toleration: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted((str(key), str(value)) for key, value in toleration.items() if value is not None)
    )


def _soperator_extend_nodeconfigurator_tolerations_from_nodesets(values: dict[str, Any]) -> None:
    nodesets = values.get("nodesets")
    if not isinstance(nodesets, list):
        return

    collected: list[dict[str, Any]] = []
    for nodeset in nodesets:
        if not isinstance(nodeset, Mapping):
            continue
        tolerations = nodeset.get("tolerations")
        if not isinstance(tolerations, list):
            continue
        for toleration in tolerations:
            if isinstance(toleration, Mapping) and toleration:
                collected.append(to_plain_data(toleration))
    if not collected:
        return

    for key in ("customContainer", "rebooter"):
        section = values.setdefault(key, {})
        if not isinstance(section, dict):
            section = {}
            values[key] = section
        existing = section.get("tolerations")
        existing_items = existing if isinstance(existing, list) else []
        merged = [
            to_plain_data(item) for item in existing_items if isinstance(item, Mapping) and item
        ]
        seen = {_soperator_toleration_identity(item) for item in merged}
        for item in collected:
            identity = _soperator_toleration_identity(item)
            if identity in seen:
                continue
            merged.append(item)
            seen.add(identity)
        if merged:
            section["tolerations"] = merged


def _soperator_strip_nodeset_image_overrides(nodeset: dict[str, Any]) -> None:
    for key in ("slurmd", "munge", "sssd"):
        section = nodeset.get(key)
        if isinstance(section, dict):
            section.pop("image", None)


def _soperator_guided_sssd_enabled(values: Mapping[str, Any]) -> bool | None:
    helper = values.get("sssd")
    if not isinstance(helper, Mapping) or "enabled" not in helper:
        return None
    enabled = helper.get("enabled")
    return enabled if isinstance(enabled, bool) else None


def _materialize_soperator_guided_sssd_values(values: dict[str, Any]) -> None:
    enabled = _soperator_guided_sssd_enabled(values)
    if enabled is None:
        return

    slurm_nodes = values.setdefault("slurmNodes", {})
    if isinstance(slurm_nodes, dict):
        slurm_sssd = slurm_nodes.setdefault("sssd", {})
        if not isinstance(slurm_sssd, dict):
            slurm_sssd = {}
            slurm_nodes["sssd"] = slurm_sssd
        slurm_sssd["enabled"] = enabled

    nodesets = values.get("nodesets")
    if not isinstance(nodesets, list):
        return
    for nodeset in nodesets:
        if not isinstance(nodeset, dict):
            continue
        nodeset_sssd = nodeset.setdefault("sssd", {})
        if not isinstance(nodeset_sssd, dict):
            nodeset_sssd = {}
            nodeset["sssd"] = nodeset_sssd
        nodeset_sssd["enabled"] = enabled


def _materialize_soperator_rest_compatibility(values: dict[str, Any]) -> None:
    """Keep REST functional with the target's shared Slurm configuration."""

    slurm_nodes = values.get("slurmNodes")
    if not isinstance(slurm_nodes, dict):
        return
    rest = slurm_nodes.get("rest")
    if not isinstance(rest, Mapping) or rest.get("enabled") is not True:
        return
    controller = slurm_nodes.setdefault("controller", {})
    if not isinstance(controller, dict):
        controller = {}
        slurm_nodes["controller"] = controller
    open_metrics = controller.setdefault("openMetrics", {})
    if not isinstance(open_metrics, dict):
        open_metrics = {}
        controller["openMetrics"] = open_metrics
    # Soperator 4.1.7 includes slurm.conf from slurm_rest.conf. Slurmrestd
    # rejects the controller-only MetricsType property, so native controller
    # OpenMetrics and REST cannot be enabled together in that release graph.
    open_metrics["enabled"] = False


def _soperator_merge_nodeset_with_generated(
    existing: Mapping[str, Any],
    generated: Mapping[str, Any],
    *,
    preserve_existing_node_config: bool = False,
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(existing))
    _merge_missing_mapping(merged, generated)

    for nodeset_field in ("replicas", "nodeSelector", "affinity", "nodeConfig"):
        if (
            nodeset_field == "nodeConfig"
            and preserve_existing_node_config
            and isinstance(existing.get("nodeConfig"), Mapping)
        ):
            continue
        if nodeset_field in generated:
            merged[nodeset_field] = copy.deepcopy(generated[nodeset_field])
    if "nodeSelector" in generated:
        merged.pop("affinity", None)
    if "affinity" in generated:
        merged.pop("nodeSelector", None)
    if preserve_existing_node_config:
        _soperator_strip_nodeset_image_overrides(merged)
    for nodeset_field in ("ephemeralNodes", "initialNumberEphemeralNodes"):
        if nodeset_field in generated:
            merged[nodeset_field] = copy.deepcopy(generated[nodeset_field])
        else:
            merged.pop(nodeset_field, None)

    generated_tolerations = generated.get("tolerations")
    if isinstance(generated_tolerations, list):
        merged["tolerations"] = _soperator_merge_tolerations(
            merged.get("tolerations"),
            [item for item in generated_tolerations if isinstance(item, Mapping)],
        )

    generated_slurmd = generated.get("slurmd")
    if isinstance(generated_slurmd, Mapping):
        generated_resources = generated_slurmd.get("resources")
        if isinstance(generated_resources, Mapping):
            slurmd = merged.setdefault("slurmd", {})
            if not isinstance(slurmd, dict):
                slurmd = {}
                merged["slurmd"] = slurmd
            resources = slurmd.setdefault("resources", {})
            if not isinstance(resources, dict):
                resources = {}
                slurmd["resources"] = resources
            resources.update(copy.deepcopy(dict(generated_resources)))
            if resources.get("gpu") not in (None, "", 0, "0"):
                resources.pop("nvidia.com/gpu", None)

    return merged


def _soperator_profile_chart_values(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    return _profile_mapping(_profile_mapping(profile, "chart"), "values")


def _soperator_hydrate_registered_nodesets_from_profile(
    values: dict[str, Any],
    *,
    profile: Mapping[str, Any],
    inputs: Mapping[str, Any] | None = None,
    placements: Mapping[str, list[str]] | None = None,
) -> None:
    """Combine adopted topology with target-release-safe profile defaults."""

    adopted = values.get("nodesets")
    if not isinstance(adopted, list) or not adopted:
        return
    templates = _soperator_template_nodesets_by_name(_soperator_profile_chart_values(profile))
    if not templates:
        raise ValueError(
            "Registered Soperator target has adopted worker NodeSets, but its selected "
            "profile has no target NodeSet templates."
        )

    hydrated: list[dict[str, Any]] = []
    adopted_worker_count = sum(
        1
        for item in adopted
        if isinstance(item, Mapping)
        and _non_empty_text(item.get("name")).startswith("worker")
    )
    for index, raw_nodeset in enumerate(adopted):
        if not isinstance(raw_nodeset, Mapping):
            raise ValueError(f"Registered Soperator nodesets[{index}] must be a mapping.")
        name = normalize_component_token(raw_nodeset.get("name"))
        if not name:
            raise ValueError(f"Registered Soperator nodesets[{index}] has no name.")

        candidates = [
            (template_name, template)
            for template_name, template in templates.items()
            if _soperator_nodeset_values_are_gpu(template)
            == _soperator_nodeset_values_are_gpu(raw_nodeset)
        ]
        exact = [item for item in candidates if normalize_component_token(item[0]) == name]
        prefixed = [
            item for item in candidates if name.startswith(f"{normalize_component_token(item[0])}-")
        ]
        selected = exact or prefixed or candidates
        if len(selected) != 1:
            candidate_names = ", ".join(sorted(item[0] for item in selected)) or "(none)"
            raise ValueError(
                f"Registered worker NodeSet '{name}' cannot be mapped unambiguously to the "
                f"selected target profile; candidates: {candidate_names}."
            )

        materialized = copy.deepcopy(to_plain_data(dict(selected[0][1])))
        _merge_replace_mapping(materialized, raw_nodeset)
        if "nodeSelector" in raw_nodeset:
            materialized["nodeSelector"] = copy.deepcopy(
                to_plain_data(raw_nodeset["nodeSelector"])
            )
            materialized.pop("affinity", None)
        elif "affinity" in raw_nodeset:
            materialized["affinity"] = copy.deepcopy(
                to_plain_data(raw_nodeset["affinity"])
            )
            materialized.pop("nodeSelector", None)
        materialized["name"] = name
        if inputs is not None and placements is not None and name.startswith("worker"):
            _soperator_normalize_registered_gpu_shape(
                materialized,
                source_nodeset=raw_nodeset,
                inputs=inputs,
                placements=placements,
                single_adopted_worker=adopted_worker_count == 1,
            )
        hydrated.append(materialized)
    values["nodesets"] = hydrated


def _soperator_registered_gpu_resource_count(
    value: Any,
    *,
    nodeset_name: str,
    field_name: str,
) -> int:
    text = str(value).strip()
    if isinstance(value, bool) or re.fullmatch(r"[0-9]+", text) is None:
        raise ValueError(
            f"Registered worker NodeSet '{nodeset_name}' {field_name} must be a "
            "positive integer GPU count."
        )
    count = int(text)
    if count <= 0:
        raise ValueError(
            f"Registered worker NodeSet '{nodeset_name}' {field_name} must be a "
            "positive integer GPU count."
        )
    return count


def _soperator_registered_nodeset_group_keys(
    *,
    nodeset_name: str,
    placements: Mapping[str, list[str]],
    single_adopted_worker: bool,
) -> list[str]:
    group_keys = list(placements.get(nodeset_name, []))
    if not group_keys and single_adopted_worker:
        group_keys = list(placements.get("worker", []))
    return list(dict.fromkeys(key for key in group_keys if key))


def _soperator_normalize_registered_gpu_shape(
    nodeset: dict[str, Any],
    *,
    source_nodeset: Mapping[str, Any],
    inputs: Mapping[str, Any],
    placements: Mapping[str, list[str]],
    single_adopted_worker: bool,
) -> None:
    """Translate adopted GPU capacity into the target release's canonical fields."""

    if not _soperator_nodeset_values_are_gpu(source_nodeset):
        return
    name = _non_empty_text(source_nodeset.get("name")) or "worker"
    source_resources = _mapping_path_value(source_nodeset, "slurmd.resources")
    source_resources = source_resources if isinstance(source_resources, Mapping) else {}
    canonical_present = "gpu" in source_resources
    legacy_present = "nvidia.com/gpu" in source_resources
    canonical_count = (
        _soperator_registered_gpu_resource_count(
            source_resources.get("gpu"),
            nodeset_name=name,
            field_name="slurmd.resources.gpu",
        )
        if canonical_present
        else None
    )
    legacy_count = (
        _soperator_registered_gpu_resource_count(
            source_resources.get("nvidia.com/gpu"),
            nodeset_name=name,
            field_name="slurmd.resources.nvidia.com/gpu",
        )
        if legacy_present
        else None
    )
    if (
        canonical_count is not None
        and legacy_count is not None
        and canonical_count != legacy_count
    ):
        raise ValueError(
            f"Registered worker NodeSet '{name}' has conflicting GPU counts in "
            "slurmd.resources.gpu and slurmd.resources.nvidia.com/gpu."
        )

    group_keys = _soperator_registered_nodeset_group_keys(
        nodeset_name=name,
        placements=placements,
        single_adopted_worker=single_adopted_worker,
    )
    if not group_keys:
        raise ValueError(
            f"Registered GPU worker NodeSet '{name}' has no discovered node-group placement."
        )
    node_groups = inputs.get("node_groups")
    if not isinstance(node_groups, Mapping):
        raise ValueError(
            f"Registered GPU worker NodeSet '{name}' has no discovered node-group inventory."
        )

    group_gpu_counts: list[int] = []
    group_cpu_millicores: list[int] = []
    for group_key in group_keys:
        group = node_groups.get(group_key)
        if not isinstance(group, Mapping):
            raise ValueError(
                f"Registered GPU worker NodeSet '{name}' references missing discovered "
                f"node group '{group_key}'."
            )
        resources = _soperator_node_group_resources(group)
        group_gpu_count = resources.get("gpu")
        if group_gpu_count is None or group_gpu_count <= 0:
            raise ValueError(
                f"Registered GPU worker NodeSet '{name}' node group '{group_key}' has no "
                "positive discovered GPU capacity."
            )
        group_gpu_counts.append(group_gpu_count)
        cpu_millicores = resources.get("vcpu_millicores")
        if cpu_millicores is not None and cpu_millicores > 0:
            group_cpu_millicores.append(cpu_millicores)
    if len(set(group_gpu_counts)) != 1:
        raise ValueError(
            f"Registered GPU worker NodeSet '{name}' spans node groups with different "
            "discovered GPU capacities."
        )

    discovered_gpu_count = group_gpu_counts[0]
    source_gpu_count = canonical_count if canonical_count is not None else legacy_count
    effective_gpu_count = source_gpu_count or discovered_gpu_count
    if effective_gpu_count > discovered_gpu_count:
        raise ValueError(
            f"Registered GPU worker NodeSet '{name}' requests {effective_gpu_count} GPUs "
            f"per pod, but its discovered node groups provide {discovered_gpu_count}."
        )

    slurmd = nodeset.setdefault("slurmd", {})
    if not isinstance(slurmd, dict):
        raise ValueError(f"Registered worker NodeSet '{name}' slurmd must be a mapping.")
    resources = slurmd.setdefault("resources", {})
    if not isinstance(resources, dict):
        raise ValueError(
            f"Registered worker NodeSet '{name}' slurmd.resources must be a mapping."
        )
    resources["gpu"] = effective_gpu_count
    resources.pop("nvidia.com/gpu", None)

    raw_gres_present, _raw_gres = _mapping_path_value_with_presence(
        source_nodeset,
        "nodeConfig.gresConfig",
    )
    if not raw_gres_present:
        node_config = nodeset.setdefault("nodeConfig", {})
        if not isinstance(node_config, dict):
            raise ValueError(
                f"Registered worker NodeSet '{name}' nodeConfig must be a mapping."
            )
        last_gpu_index = effective_gpu_count - 1
        node_config["gresConfig"] = [
            f"AutoDetect=off Name=gpu File=/dev/nvidia[0-{last_gpu_index}]"
            if last_gpu_index > 0
            else "AutoDetect=off Name=gpu File=/dev/nvidia[0]"
        ]

    _soperator_fit_gpu_node_config_to_group(
        nodeset,
        group_cpu_millicores=min(group_cpu_millicores) if group_cpu_millicores else None,
        slurmd_cpu_millicores=_soperator_parse_cpu_millicores(resources.get("cpu")),
        gpu_count=effective_gpu_count,
    )


def _soperator_all_profile_chart_values() -> tuple[Mapping[str, Any], ...]:
    _default_profile, profiles = _soperator_nodesets_profiles()
    return tuple(
        _soperator_profile_chart_values(profile)
        for profile in profiles.values()
        if isinstance(profile, Mapping) and _soperator_profile_chart_values(profile)
    )


def _soperator_profile_partition_values(
    profile: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    chart_profile = _profile_mapping(profile, "chart")
    partition_profiles = _profile_mapping(chart_profile, "partition_profiles")
    return tuple(
        values
        for partition_profile in partition_profiles.values()
        if isinstance(partition_profile, Mapping)
        if (values := _profile_mapping(partition_profile, "values"))
    )


def _soperator_all_profile_partition_values() -> tuple[Mapping[str, Any], ...]:
    _default_profile, profiles = _soperator_nodesets_profiles()
    return tuple(
        values
        for profile in profiles.values()
        if isinstance(profile, Mapping)
        for values in _soperator_profile_partition_values(profile)
    )


def _soperator_profile_topology_values(profile: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    chart_profile = _profile_mapping(profile, "chart")
    topology_profiles = _profile_mapping(chart_profile, "topology_profiles")
    return tuple(
        values
        for topology_profile in topology_profiles.values()
        if isinstance(topology_profile, Mapping)
        if (values := _profile_mapping(topology_profile, "values"))
    )


def _soperator_all_profile_topology_values() -> tuple[Mapping[str, Any], ...]:
    _default_profile, profiles = _soperator_nodesets_profiles()
    return tuple(
        values
        for profile in profiles.values()
        if isinstance(profile, Mapping)
        for values in _soperator_profile_topology_values(profile)
    )


def _soperator_without_nodesets(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if "nodesets" not in value:
        return value
    cleaned = dict(value)
    cleaned.pop("nodesets", None)
    return cleaned


def _soperator_profile_values_for_install_mode(
    value: Mapping[str, Any],
    *,
    install_mode: str,
    values: Mapping[str, Any],
) -> Mapping[str, Any]:
    if install_mode == _SOPERATOR_TARGET_MODE_REGISTERED and isinstance(
        values.get("nodesets"), list
    ):
        return _soperator_without_nodesets(value)
    return value


def _soperator_profile_value_sets_for_install_mode(
    value_sets: Sequence[Mapping[str, Any]],
    *,
    install_mode: str,
    values: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    if install_mode == _SOPERATOR_TARGET_MODE_REGISTERED and isinstance(
        values.get("nodesets"), list
    ):
        return tuple(_soperator_without_nodesets(value) for value in value_sets)
    return tuple(value_sets)


def _soperator_profile_install_mode_chart_values(
    profile: Mapping[str, Any],
    *,
    install_mode: str,
) -> Mapping[str, Any]:
    if install_mode != _SOPERATOR_TARGET_MODE_REGISTERED:
        return {}
    return _profile_mapping(_profile_mapping(profile, "chart"), "onboarding_values")


def _soperator_nodeset_templates(
    *,
    profile: Mapping[str, Any],
    values: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    templates = {
        name: to_plain_data(template)
        for name, template in _soperator_template_nodesets_by_name(
            _soperator_profile_chart_values(profile)
        ).items()
    }
    for name, override in _soperator_template_nodesets_by_name(values).items():
        if name in templates and isinstance(templates[name], dict):
            _merge_replace_mapping(templates[name], to_plain_data(override))
        else:
            templates[name] = to_plain_data(override)
    return templates


def _soperator_preserve_onboarded_worker_nodesets(
    *,
    values: Mapping[str, Any],
    install_mode: str,
) -> bool:
    if install_mode != _SOPERATOR_TARGET_MODE_REGISTERED:
        return False
    nodesets = values.get("nodesets")
    if not isinstance(nodesets, list):
        return False
    worker_names = {
        _non_empty_text(item.get("name"))
        for item in nodesets
        if isinstance(item, Mapping)
        if _non_empty_text(item.get("name")).startswith("worker")
    }
    if not worker_names:
        return False
    partition_config = values.get("partitionConfiguration")
    config_type = (
        _non_empty_text(partition_config.get("configType"))
        if isinstance(partition_config, Mapping)
        else ""
    )
    if config_type == "default":
        return True
    if config_type != "structured":
        return False
    partitions = (
        partition_config.get("partitions") if isinstance(partition_config, Mapping) else None
    )
    if not isinstance(partitions, Sequence) or isinstance(partitions, (str, bytes, bytearray)):
        return False
    refs = {
        normalize_component_token(ref)
        for partition in partitions
        if isinstance(partition, Mapping)
        for ref in _soperator_string_list(partition.get("nodeSetRefs"))
    }
    if refs:
        return refs <= worker_names
    return bool(partitions) and all(
        isinstance(partition, Mapping)
        and partition.get("isAll") is True
        and not _soperator_string_list(partition.get("nodeSetRefs"))
        for partition in partitions
    )


def _soperator_bind_adopted_nodesets_to_discovered_placements(
    values: dict[str, Any],
    *,
    inputs: Mapping[str, Any],
    placements: Mapping[str, list[str]],
) -> None:
    """Replace profile scheduling defaults with discovered node-group identity."""

    nodesets = values.get("nodesets")
    if not isinstance(nodesets, list):
        return
    adopted_workers = [
        item
        for item in nodesets
        if isinstance(item, dict)
        and _non_empty_text(item.get("name")).startswith("worker")
    ]
    for nodeset in adopted_workers:
        name = _non_empty_text(nodeset.get("name"))
        group_keys = list(placements.get(name, []))
        if not group_keys and len(adopted_workers) == 1:
            group_keys = list(placements.get("worker", []))
        group_keys = list(dict.fromkeys(key for key in group_keys if key))
        if not group_keys:
            raise ValueError(
                f"Registered worker NodeSet '{name}' has no discovered node-group placement."
            )
        if len(group_keys) == 1:
            expression = _soperator_node_group_selector_expression(inputs, group_keys[0])
            if (
                expression.get("operator") == "In"
                and len(expression.get("values", [])) == 1
            ):
                nodeset["nodeSelector"] = {
                    str(expression["key"]): str(expression["values"][0])
                }
                nodeset.pop("affinity", None)
                continue
        terms = _soperator_node_group_selector_terms(inputs, group_keys)
        if not terms:
            raise ValueError(
                f"Registered worker NodeSet '{name}' has no usable node-group selector."
            )
        nodeset["affinity"] = {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": terms
                }
            }
        }
        nodeset.pop("nodeSelector", None)


def _materialize_soperator_mapping_chart_values(
    *,
    values: dict[str, Any],
    profile: Mapping[str, Any],
    inputs: Mapping[str, Any],
    placements: Mapping[str, list[str]],
    install_mode: str,
    preserve_existing_worker_nodesets: bool = False,
) -> None:
    profile_placements = _soperator_profile_placements(profile)
    if not profile_placements:
        return
    use_profile_filters = not placements

    existing_filters = values.get("k8sNodeFilters")
    if isinstance(existing_filters, list):
        values["k8sNodeFilters"] = [
            item
            for item in existing_filters
            if isinstance(item, Mapping) and _non_empty_text(item.get("name"))
        ]

    cpu_keys = _soperator_node_group_keys_by_kind(inputs, kind="cpu")
    all_keys = _soperator_node_group_keys_by_kind(inputs, kind="all")
    if cpu_keys or all_keys:
        _soperator_upsert_filter(
            values,
            filter_item=_soperator_node_group_filter(
                name="no-gpu",
                group_keys=cpu_keys or all_keys,
                inputs=inputs,
            ),
        )

    template_nodesets = _soperator_nodeset_templates(profile=profile, values=values)
    worker_template_gpu_flags = _soperator_worker_template_gpu_flags(profile)
    generated_nodesets: list[dict[str, Any]] = []
    nodeset_ref_replacements: dict[str, list[str]] = {}
    storage_groups: dict[str, list[str]] = {}
    preserve_onboarded_worker_nodesets = preserve_existing_worker_nodesets

    if preserve_onboarded_worker_nodesets:
        _soperator_bind_adopted_nodesets_to_discovered_placements(
            values,
            inputs=inputs,
            placements=placements,
        )

    def _initial_ephemeral_nodes(node_group: Any) -> int:
        max_nodes = int(node_group.autoscaling_max_node_count or 0)
        initial_nodes = int(node_group.autoscaling_min_node_count or 0)
        if max_nodes > 0 and bool(getattr(node_group, "gpu", False)):
            initial_nodes = max(1, initial_nodes)
        return min(initial_nodes, max_nodes)

    def _group_keys_for_template(
        *,
        template_name: str,
        group_keys: Sequence[str],
    ) -> list[str]:
        normalized_template = normalize_component_token(template_name)
        if not normalized_template:
            return []
        normalized_groups = [str(group_key).strip() for group_key in group_keys if group_key]
        if len(normalized_groups) <= 1:
            return normalized_groups
        matched = [
            group_key
            for group_key in normalized_groups
            if normalize_component_token(group_key) == normalized_template
            or normalize_component_token(group_key).startswith(f"{normalized_template}-")
        ]
        if matched:
            return matched
        template_gpu = worker_template_gpu_flags.get(normalized_template)
        if template_gpu is None:
            return normalized_groups
        groups_by_key = {group.key: group for group in iter_mk8s_node_groups(inputs)}
        known_group_keys = [
            group_key for group_key in normalized_groups if group_key in groups_by_key
        ]
        if not known_group_keys:
            return normalized_groups
        return [
            group_key
            for group_key in known_group_keys
            if (group := groups_by_key.get(group_key)) is not None and group.gpu == template_gpu
        ]

    def _generate_nodesets_from_template(
        *,
        template_name: str,
        group_keys: Sequence[str],
    ) -> None:
        if preserve_onboarded_worker_nodesets and template_name.startswith("worker"):
            return
        template = template_nodesets.get(template_name)
        if not isinstance(template, Mapping):
            return
        replacement_names: list[str] = []
        for group_key in group_keys:
            nodeset = copy.deepcopy(to_plain_data(template))
            normalized_template_name = normalize_component_token(template_name)
            normalized_group_key = normalize_component_token(group_key)
            if len(group_keys) == 1:
                nodeset_name = template_name
            elif (
                normalized_group_key == normalized_template_name
                or normalized_group_key.startswith(f"{normalized_template_name}-")
            ):
                nodeset_name = normalized_group_key
            else:
                nodeset_name = normalize_component_token(f"{template_name}-{group_key}")
            nodeset["name"] = nodeset_name
            node_group = next(
                (
                    group
                    for group in iter_mk8s_node_groups(inputs)
                    if group.key == group_key
                    and (
                        group.node_count is not None or group.autoscaling_max_node_count is not None
                    )
                ),
                None,
            )
            if node_group is not None:
                nodeset["replicas"] = (
                    node_group.node_count
                    if node_group.node_count is not None
                    else node_group.autoscaling_max_node_count
                )
                if (
                    install_mode == _SOPERATOR_TARGET_MODE_MANAGED
                    and template_name.startswith("worker")
                    and _soperator_worker_node_group_ephemeral_enabled(inputs, group_key)
                    and node_group.autoscaling_max_node_count is not None
                ):
                    nodeset["ephemeralNodes"] = True
                    nodeset["initialNumberEphemeralNodes"] = _initial_ephemeral_nodes(node_group)
                else:
                    nodeset.pop("ephemeralNodes", None)
                    nodeset.pop("initialNumberEphemeralNodes", None)
            selector_expression = _soperator_node_group_selector_expression(inputs, group_key)
            if (
                selector_expression.get("operator") == "In"
                and len(selector_expression.get("values", [])) == 1
            ):
                nodeset["nodeSelector"] = {
                    str(selector_expression["key"]): str(selector_expression["values"][0])
                }
            else:
                nodeset["affinity"] = {
                    "nodeAffinity": {
                        "requiredDuringSchedulingIgnoredDuringExecution": {
                            "nodeSelectorTerms": [{"matchExpressions": [selector_expression]}]
                        }
                    }
                }
            nodeset_tolerations = _soperator_merge_tolerations(
                nodeset.get("tolerations"),
                _soperator_node_group_tolerations(inputs, [group_key]),
            )
            if nodeset_tolerations:
                nodeset["tolerations"] = nodeset_tolerations
            _soperator_fit_nodeset_resources_to_group(
                nodeset,
                group_key=group_key,
                inputs=inputs,
                install_mode=install_mode,
            )
            generated_nodesets.append(nodeset)
            replacement_names.append(nodeset_name)
        if replacement_names:
            nodeset_ref_replacements[template_name] = replacement_names

    for placement, raw_placement_config in profile_placements.items():
        if not isinstance(raw_placement_config, Mapping):
            continue
        placement_name = str(placement).strip()
        group_keys = placements.get(placement_name, [])
        filter_name = _non_empty_text(raw_placement_config.get("soperator_node_filter"))
        filter_item: dict[str, Any] | None = None
        if group_keys and filter_name:
            role_tolerations = _soperator_node_group_tolerations(inputs, group_keys)
            filter_item = _soperator_node_group_filter(
                name=filter_name,
                group_keys=group_keys,
                inputs=inputs,
                tolerations=role_tolerations,
            )
            _soperator_upsert_filter(
                values,
                filter_item=filter_item,
            )
        elif use_profile_filters and filter_name:
            existing_filter = _soperator_filter_by_name(values, filter_name)
            if isinstance(existing_filter, Mapping):
                filter_item = dict(existing_filter)
        if filter_item is not None and filter_name:
            raw_bindings = raw_placement_config.get("soperator_value_bindings")
            if isinstance(raw_bindings, Mapping):
                for raw_path, raw_value in raw_bindings.items():
                    path = _non_empty_text(raw_path)
                    binding_value = _non_empty_text(raw_value) or filter_name
                    preserve_registered_service_placement = (
                        install_mode == _SOPERATOR_TARGET_MODE_REGISTERED
                        and path
                        in {
                            "slurmNodes.accounting.k8sNodeFilterName",
                            "slurmNodes.exporter.k8sNodeFilterName",
                            "slurmNodes.rest.k8sNodeFilterName",
                        }
                        and _mapping_path_value_with_presence(values, path)[0]
                    )
                    if preserve_registered_service_placement:
                        continue
                    if path:
                        _soperator_set_mapping_path(values, path, binding_value)
        if filter_item is not None:
            affinity = filter_item.get("affinity")
            if isinstance(affinity, Mapping):
                for path in _soperator_string_list(
                    raw_placement_config.get("soperator_affinity_bindings")
                ):
                    _soperator_set_mapping_path(values, path, affinity)
        if not group_keys:
            continue
        for filesystem_key in _soperator_role_filesystem_keys(raw_placement_config):
            storage_groups.setdefault(filesystem_key, [])
            storage_groups[filesystem_key].extend(group_keys)

        template_names = _soperator_string_list(raw_placement_config.get("nodeset_templates"))
        if not template_names:
            template_name = _non_empty_text(raw_placement_config.get("nodeset_template"))
            template_names = [template_name] if template_name else []
        for template_name in template_names:
            if preserve_onboarded_worker_nodesets and placement_name == "worker":
                continue
            _generate_nodesets_from_template(
                template_name=template_name,
                group_keys=_group_keys_for_template(
                    template_name=template_name,
                    group_keys=group_keys,
                ),
            )

    worker_role_config = profile_placements.get("worker")
    worker_filesystem_keys = (
        _soperator_role_filesystem_keys(worker_role_config)
        if isinstance(worker_role_config, Mapping)
        else []
    )
    for template_name, group_keys in placements.items():
        if template_name in profile_placements or not template_name.startswith("worker"):
            continue
        if template_name not in template_nodesets:
            continue
        for filesystem_key in worker_filesystem_keys:
            storage_groups.setdefault(filesystem_key, [])
            storage_groups[filesystem_key].extend(group_keys)
        if preserve_onboarded_worker_nodesets:
            continue
        _generate_nodesets_from_template(template_name=template_name, group_keys=group_keys)

    for filesystem_key, group_keys in storage_groups.items():
        _soperator_set_storage_node_group_selector(
            values,
            filesystem_key=filesystem_key,
            group_keys=group_keys,
            inputs=inputs,
            tolerations=_soperator_node_group_tolerations(inputs, group_keys),
            include_profile_jail_aliases=(install_mode != _SOPERATOR_TARGET_MODE_REGISTERED),
            preserve_existing_match_expressions=(install_mode == _SOPERATOR_TARGET_MODE_REGISTERED),
        )

    if generated_nodesets:
        existing_nodesets = values.get("nodesets")
        existing_nodesets_list = existing_nodesets if isinstance(existing_nodesets, list) else []
        existing_nodesets_by_name = {
            _non_empty_text(item.get("name")): item
            for item in existing_nodesets_list
            if isinstance(item, Mapping) and _non_empty_text(item.get("name"))
        }
        all_profile_template_names: set[str] = set()
        _default_profile, all_profiles = _soperator_nodesets_profiles()
        for candidate_profile in all_profiles.values():
            if not isinstance(candidate_profile, Mapping):
                continue
            all_profile_template_names.update(
                _soperator_template_nodesets_by_name(
                    _soperator_profile_chart_values(candidate_profile)
                )
            )
        selected_template_names = set(template_nodesets)
        merged_generated_nodesets: list[dict[str, Any]] = []
        for nodeset in generated_nodesets:
            existing_nodeset = existing_nodesets_by_name.get(_non_empty_text(nodeset.get("name")))
            if isinstance(existing_nodeset, Mapping):
                merged_generated_nodesets.append(
                    _soperator_merge_nodeset_with_generated(
                        existing_nodeset,
                        nodeset,
                        preserve_existing_node_config=(
                            install_mode == _SOPERATOR_TARGET_MODE_REGISTERED
                        ),
                    )
                )
            else:
                merged_generated_nodesets.append(nodeset)
        generated_names = {_non_empty_text(item.get("name")) for item in generated_nodesets}
        replaced_template_names = set(nodeset_ref_replacements)
        mapped_group_keys = {
            group_key for group_keys in placements.values() for group_key in group_keys if group_key
        }

        def _stale_onboarding_nodeset(item: Mapping[str, Any]) -> bool:
            if install_mode != _SOPERATOR_TARGET_MODE_REGISTERED:
                return False
            name = _non_empty_text(item.get("name"))
            if not name or name in generated_names or not name.startswith("worker-"):
                return False
            for group_key in mapped_group_keys:
                if group_key and group_key in name:
                    return True
            node_selector = item.get("nodeSelector")
            if isinstance(node_selector, Mapping):
                selector_values = {
                    _non_empty_text(value)
                    for value in node_selector.values()
                    if _non_empty_text(value)
                }
                if selector_values.intersection(mapped_group_keys):
                    return True
            return False

        def _stale_profile_generated_nodeset(item: Mapping[str, Any]) -> bool:
            if install_mode == _SOPERATOR_TARGET_MODE_REGISTERED:
                return False
            name = _non_empty_text(item.get("name"))
            if not name or name in generated_names:
                return False
            if not any(
                name.startswith(f"{template_name}-")
                for template_name in selected_template_names
                if template_name.startswith("worker")
            ):
                return False
            if any(group_key and group_key in name for group_key in mapped_group_keys):
                return True
            node_selector = item.get("nodeSelector")
            if isinstance(node_selector, Mapping):
                selector_values = {
                    _non_empty_text(value)
                    for value in node_selector.values()
                    if _non_empty_text(value)
                }
                if selector_values.intersection(mapped_group_keys):
                    return True
            return False

        preserved = [
            item
            for item in existing_nodesets_list
            if isinstance(item, Mapping)
            and _non_empty_text(item.get("name"))
            and _non_empty_text(item.get("name")) not in generated_names
            and _non_empty_text(item.get("name")) not in replaced_template_names
            and not _stale_onboarding_nodeset(item)
            and not _stale_profile_generated_nodeset(item)
            and not (
                _non_empty_text(item.get("name")) in all_profile_template_names
                and _non_empty_text(item.get("name")) not in selected_template_names
            )
        ]
        values["nodesets"] = [*preserved, *merged_generated_nodesets]

    partition_config = values.get("partitionConfiguration")
    partitions = (
        partition_config.get("partitions") if isinstance(partition_config, Mapping) else None
    )
    if isinstance(partitions, list):
        for partition in partitions:
            if not isinstance(partition, dict):
                continue
            refs = _soperator_string_list(partition.get("nodeSetRefs"))
            if not refs:
                continue
            resolved_refs: list[str] = []
            for ref in refs:
                resolved_refs.extend(nodeset_ref_replacements.get(ref, [ref]))
            partition["nodeSetRefs"] = list(dict.fromkeys(resolved_refs))


def _materialize_soperator_node_group(
    *,
    target_ref: str,
    group_key: str,
    group: dict[str, Any],
    inputs: Mapping[str, Any],
    prefer_shape_defaults: bool = False,
) -> None:
    gpu = bool(group.get("gpu", False))
    _materialize_soperator_node_group_shape(
        group=group,
        inputs=inputs,
        gpu=gpu,
        prefer_shape_defaults=prefer_shape_defaults,
    )
    _materialize_soperator_node_group_labels(group, group_key=group_key)
    _materialize_soperator_node_group_filesystems(group)
    service_account = group.get("service_account")
    if service_account is None:
        identity = f"{target_ref}:{group_key}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
        target_token = normalize_component_token(target_ref)[:18] or "cluster"
        group_token = normalize_component_token(group_key)[:18] or "nodes"
        group["service_account"] = {
            "name": f"sop-{target_token}-{group_token}-{digest}",
            "description": (
                "Soperator node identity with Terraform-owned Observability writer access"
            ),
        }
    elif not isinstance(service_account, Mapping):
        raise ValueError(f"Soperator node group {group_key!r} service_account must be a mapping")


def _materialize_soperator_worker_node_groups(
    *,
    target_ref: str,
    inputs: dict[str, Any],
    node_groups: dict[str, Any],
    worker_profiles: list[Any],
) -> None:
    active_worker_control_keys: set[str] = set()
    for raw_worker in worker_profiles:
        if not isinstance(raw_worker, Mapping):
            continue
        nodeset_name = _non_empty_text(raw_worker.get("nodeset_name")) or _non_empty_text(
            raw_worker.get("name")
        )
        if not nodeset_name:
            continue
        key_prefix = _non_empty_text(raw_worker.get("node_group_prefix")) or nodeset_name
        default_total_nodes = _required_profile_positive_int(
            raw_worker.get("default_total_nodes"),
            field=f"worker_nodesets[{nodeset_name}].default_total_nodes",
        )
        default_nodes_per_group = _required_profile_positive_int(
            raw_worker.get("default_nodes_per_group"),
            field=f"worker_nodesets[{nodeset_name}].default_nodes_per_group",
        )
        total_nodes = _soperator_worker_profile_total_nodes(
            inputs=inputs,
            raw_worker=raw_worker,
            default_total_nodes=default_total_nodes,
            default_nodes_per_group=default_nodes_per_group,
        )
        desired_total_nodes = total_nodes

        max_nodes_per_group = _required_profile_positive_int(
            raw_worker.get("max_nodes_per_group"),
            field=f"worker_nodesets[{nodeset_name}].max_nodes_per_group",
        )
        nodes_per_group = _soperator_worker_profile_nodes_per_group(
            inputs=inputs,
            raw_worker=raw_worker,
            default_nodes_per_group=default_nodes_per_group,
            max_nodes_per_group=max_nodes_per_group,
        )
        shard_count = max(1, (desired_total_nodes + nodes_per_group - 1) // nodes_per_group)
        desired_counts: dict[str, int] = {}
        for index in range(shard_count):
            remaining = desired_total_nodes - (index * nodes_per_group)
            shard_size = min(nodes_per_group, remaining)
            group_key = key_prefix if shard_count == 1 else f"{key_prefix}-{index}"
            desired_counts[group_key] = shard_size
        active_worker_control_keys.update(desired_counts)
        worker_node_groups = _soperator_worker_node_groups_mutable(inputs)
        desired_key_set = set(desired_counts)
        for raw_key in list(worker_node_groups):
            group_key = str(raw_key)
            if (
                _soperator_worker_control_key_matches_prefix(group_key, key_prefix)
                and group_key not in desired_key_set
            ):
                worker_node_groups.pop(raw_key, None)
        for group_key in desired_counts:
            control = worker_node_groups.setdefault(group_key, {})
            if not isinstance(control, dict):
                raise ValueError(
                    f"{_SOPERATOR_WORKER_NODE_GROUPS_INPUT}.{group_key} must be a mapping"
                )
            autoscaling_control = control.setdefault("autoscaling", {})
            if not isinstance(autoscaling_control, dict):
                raise ValueError(
                    f"{_SOPERATOR_WORKER_NODE_GROUPS_INPUT}.{group_key}.autoscaling "
                    "must be a mapping"
                )
            autoscaling_control.setdefault("enabled", False)
            ephemeral_control = control.setdefault("ephemeral_nodes", {})
            if not isinstance(ephemeral_control, dict):
                raise ValueError(
                    f"{_SOPERATOR_WORKER_NODE_GROUPS_INPUT}.{group_key}.ephemeral_nodes "
                    "must be a mapping"
                )
            ephemeral_control.setdefault("enabled", False)

        desired_autoscaling: dict[str, dict[str, int]] = {}
        for group_key, shard_size in desired_counts.items():
            autoscaling = _soperator_worker_node_group_autoscaling(
                inputs=inputs,
                group_key=group_key,
                shard_size=shard_size,
            )
            if autoscaling:
                desired_autoscaling[group_key] = {
                    "min_node_count": autoscaling["min_node_count"],
                    "max_node_count": autoscaling["max_node_count"],
                }
            if _soperator_worker_node_group_ephemeral_enabled(inputs, group_key):
                if autoscaling is None:
                    raise ValueError(
                        f"{_SOPERATOR_WORKER_NODE_GROUPS_INPUT}.{group_key}"
                        ".ephemeral_nodes.enabled requires "
                        f"{_SOPERATOR_WORKER_NODE_GROUPS_INPUT}.{group_key}"
                        ".autoscaling.enabled=true"
                    )
                if autoscaling["max_node_count"] < 1:
                    raise ValueError(
                        f"{_SOPERATOR_WORKER_NODE_GROUPS_INPUT}.{group_key}"
                        ".ephemeral_nodes.enabled requires "
                        f"{_SOPERATOR_WORKER_NODE_GROUPS_INPUT}.{group_key}"
                        ".autoscaling.max_node_count to be at least 1"
                    )

        raw_base_group = raw_worker.get("node_group")
        base_group: dict[str, Any] = (
            copy.deepcopy(dict(raw_base_group)) if isinstance(raw_base_group, Mapping) else {}
        )
        gpu_cluster_key = _non_empty_text(base_group.get("gpu_cluster_key"))
        gpu_clusters = inputs.get("gpu_clusters")
        if gpu_cluster_key and not (
            isinstance(gpu_clusters, Mapping) and gpu_cluster_key in gpu_clusters
        ):
            base_group.pop("gpu_cluster_key", None)

        existing_group_keys = _soperator_nodeset_group_keys(
            node_groups,
            nodeset_name=nodeset_name,
            key_prefix=key_prefix,
        )
        if existing_group_keys:
            existing_key_set = set(existing_group_keys)
            desired_key_set = set(desired_counts)
            rebuild = existing_key_set != desired_key_set
            if not rebuild:
                for group_key, desired_count in desired_counts.items():
                    group = node_groups.get(group_key)
                    expected_autoscaling = desired_autoscaling.get(group_key)
                    if not isinstance(group, dict):
                        rebuild = True
                        break
                    if expected_autoscaling:
                        if (
                            group.get("autoscaling") != expected_autoscaling
                            or group.get("node_count") is not None
                        ):
                            rebuild = True
                            break
                    elif group.get("node_count") != desired_count:
                        rebuild = True
                        break
            if rebuild:
                for group_key in existing_group_keys:
                    node_groups.pop(group_key, None)
            else:
                for group_key, desired_count in desired_counts.items():
                    group = node_groups.get(group_key)
                    if isinstance(group, dict):
                        _merge_missing_mapping(group, base_group)
                        current_gpu_cluster_key = _non_empty_text(group.get("gpu_cluster_key"))
                        if current_gpu_cluster_key and not (
                            isinstance(gpu_clusters, Mapping)
                            and current_gpu_cluster_key in gpu_clusters
                        ):
                            group.pop("gpu_cluster_key", None)
                        _soperator_set_node_group_scale(
                            group,
                            node_count=desired_count,
                            autoscaling=desired_autoscaling.get(group_key),
                        )
                        _materialize_soperator_node_group(
                            target_ref=target_ref,
                            group_key=group_key,
                            group=group,
                            inputs=inputs,
                            prefer_shape_defaults=True,
                        )
                continue

        for group_key, shard_size in desired_counts.items():
            group = node_groups.setdefault(
                group_key,
                {
                    **base_group,
                    "nodeset_name": nodeset_name,
                    "workload": _non_empty_text(raw_worker.get("workload")) or "worker",
                    "gpu": bool(raw_worker.get("gpu", True)),
                    "jail": bool(raw_worker.get("jail", True)),
                },
            )
            if isinstance(group, dict):
                _soperator_set_node_group_scale(
                    group,
                    node_count=shard_size,
                    autoscaling=desired_autoscaling.get(group_key),
                )
                _materialize_soperator_node_group(
                    target_ref=target_ref,
                    group_key=group_key,
                    group=group,
                    inputs=inputs,
                    prefer_shape_defaults=True,
                )
    worker_node_groups = _soperator_worker_node_groups_mutable(inputs)
    for raw_key in list(worker_node_groups):
        group_key = str(raw_key)
        if group_key not in active_worker_control_keys:
            worker_node_groups.pop(raw_key, None)


def _materialize_soperator_mk8s_profile(
    *,
    target_ref: str,
    inputs: dict[str, Any],
    profile: Mapping[str, Any],
    values: Mapping[str, Any],
    placements: Mapping[str, list[str]],
    install_mode: str,
    replace_profile_managed_groups: bool = False,
    placements_configured: bool = True,
) -> None:
    mk8s_profile = _profile_mapping(profile, "mk8s")
    defaults = _profile_mapping(mk8s_profile, "inputs")
    _merge_missing_mapping(inputs, defaults)
    _raise_if_legacy_soperator_worker_inputs(inputs)

    node_groups = inputs.setdefault("node_groups", {})
    explicit_placements = dict(placements)
    existing_mode = install_mode == _SOPERATOR_TARGET_MODE_REGISTERED
    if not isinstance(node_groups, dict):
        return
    if not existing_mode:
        _raise_if_soperator_production_missing_service_node_groups(
            inputs=inputs,
            profile=profile,
            placements=explicit_placements if placements_configured else {},
        )
        _soperator_prune_stale_profile_node_groups(inputs=inputs, profile=profile)
        _soperator_prune_stale_profile_gpu_clusters(inputs=inputs, profile=profile)
        _soperator_prune_profile_gpu_cluster_path_for_selected_shape(
            inputs=inputs,
            profile=profile,
        )
    if existing_mode or (
        (placements_configured or not replace_profile_managed_groups)
        and explicit_placements
        and _soperator_has_external_node_groups(
            inputs=inputs,
            profile=profile,
        )
    ):
        if explicit_placements:
            _materialize_soperator_existing_node_group_mapping(
                inputs=inputs,
                profile=profile,
                mapping=explicit_placements,
            )
        return

    gpu_clusters = _profile_mapping(mk8s_profile, "gpu_clusters")
    if gpu_clusters and not _soperator_selected_gpu_defaults_are_ethernet_only(inputs):
        target_gpu_clusters = inputs.setdefault("gpu_clusters", {})
        if isinstance(target_gpu_clusters, dict):
            _merge_missing_mapping(target_gpu_clusters, gpu_clusters)

    profile_node_groups = _profile_mapping(mk8s_profile, "node_groups")
    _merge_missing_mapping(
        node_groups,
        _soperator_profile_node_group_defaults(profile_node_groups),
    )
    _soperator_apply_profile_node_group_count_inputs(
        inputs=inputs,
        node_groups=node_groups,
        profile_node_groups=profile_node_groups,
    )
    for group_key, group in node_groups.items():
        if isinstance(group, dict):
            _materialize_soperator_node_group(
                target_ref=target_ref,
                group_key=str(group_key),
                group=group,
                inputs=inputs,
                prefer_shape_defaults=True,
            )
    _materialize_soperator_worker_node_groups(
        target_ref=target_ref,
        inputs=inputs,
        node_groups=node_groups,
        worker_profiles=_profile_list(mk8s_profile, "worker_nodesets"),
    )
    if not existing_mode:
        _soperator_prune_profile_gpu_cluster_path_for_selected_shape(
            inputs=inputs,
            profile=profile,
        )


def _materialize_soperator_sfs_profile(
    *,
    inputs: dict[str, Any],
    profile: Mapping[str, Any],
    target_ref: str,
) -> dict[str, Any]:
    rendered_filesystems = _render_soperator_sfs_profile_filesystems(
        profile=profile,
        target_ref=target_ref,
    )
    filesystems = inputs.setdefault("filesystems", {})
    if isinstance(filesystems, dict):
        _merge_missing_mapping(filesystems, rendered_filesystems)
        return copy.deepcopy(filesystems)
    return {}


def _render_soperator_sfs_profile_filesystems(
    *,
    profile: Mapping[str, Any],
    target_ref: str,
) -> dict[str, Any]:
    sfs_profile = _profile_mapping(profile, "sfs")
    profile_filesystems = _profile_mapping(sfs_profile, "filesystems")
    return {
        key: _render_soperator_profile_value(value, target_ref=target_ref)
        for key, value in profile_filesystems.items()
    }


def _materialize_soperator_partition_profile(
    *,
    values: dict[str, Any],
    profile: Mapping[str, Any],
    install_mode: str = "",
) -> None:
    profile_name = _non_empty_text(values.get("partitionProfile"))
    chart_profile = _profile_mapping(profile, "chart")
    base_values = _profile_mapping(chart_profile, "values")
    partition_profiles = _profile_mapping(chart_profile, "partition_profiles")
    partition_value_sets = _soperator_profile_value_sets_for_install_mode(
        _soperator_all_profile_partition_values(),
        install_mode=install_mode,
        values=values,
    )
    if not profile_name or profile_name == "shape-default":
        _merge_soperator_profile_values(
            values,
            _soperator_profile_values_for_install_mode(
                base_values,
                install_mode=install_mode,
                values=values,
            ),
            {},
            replaceable_base_values=partition_value_sets,
        )
        return
    selected_partition_profile = partition_profiles.get(profile_name)
    if not isinstance(selected_partition_profile, Mapping):
        available = ", ".join(sorted(str(name) for name in partition_profiles)) or "(none)"
        raise ValueError(
            f"values.partitionProfile references unknown Soperator partition "
            f"profile '{profile_name}'. Available profiles for this nodesets profile: {available}"
        )
    profile_values = _profile_mapping(selected_partition_profile, "values")
    _merge_soperator_profile_values(
        values,
        _soperator_profile_values_for_install_mode(
            profile_values,
            install_mode=install_mode,
            values=values,
        ),
        _soperator_profile_values_for_install_mode(
            base_values,
            install_mode=install_mode,
            values=values,
        ),
        replaceable_base_values=partition_value_sets,
    )


def _materialize_soperator_topology_profile(
    *,
    values: dict[str, Any],
    profile: Mapping[str, Any],
    install_mode: str = "",
) -> None:
    profile_name = _non_empty_text(values.get("topologyProfile"))
    chart_profile = _profile_mapping(profile, "chart")
    base_values = _profile_mapping(chart_profile, "values")
    topology_profiles = _profile_mapping(chart_profile, "topology_profiles")
    topology_value_sets = _soperator_profile_value_sets_for_install_mode(
        _soperator_all_profile_topology_values(),
        install_mode=install_mode,
        values=values,
    )
    if not profile_name:
        profile_name = "disabled"
    topology_profile = topology_profiles.get(profile_name)
    if not isinstance(topology_profile, Mapping):
        available = ", ".join(sorted(str(name) for name in topology_profiles)) or "(none)"
        raise ValueError(
            f"values.topologyProfile references unknown Soperator topology "
            f"profile '{profile_name}'. Available profiles for this nodesets profile: {available}"
        )
    profile_values = _profile_mapping(topology_profile, "values")
    _merge_soperator_profile_values(
        values,
        _soperator_profile_values_for_install_mode(
            profile_values,
            install_mode=install_mode,
            values=values,
        ),
        _soperator_profile_values_for_install_mode(
            base_values,
            install_mode=install_mode,
            values=values,
        ),
        replaceable_base_values=topology_value_sets,
    )


def _materialize_soperator_dcgm_exporter_values(
    *,
    values: dict[str, Any],
    inputs: Mapping[str, Any],
) -> None:
    node_groups = iter_mk8s_node_groups(inputs)
    dcgm_exporter = values.setdefault("soperator-dcgm-exporter", {})
    if not any(group.gpu for group in node_groups):
        if isinstance(dcgm_exporter, dict):
            dcgm_exporter["enabled"] = False
        return
    if not any(group.gpu and group.gpu_stack_source == "nebius_image" for group in node_groups):
        return
    if isinstance(dcgm_exporter, dict):
        dcgm_exporter.setdefault("validateToolkit", False)


def _materialize_soperator_worker_ephemeral_values(
    *,
    values: dict[str, Any],
    inputs: Mapping[str, Any],
    install_mode: str,
) -> None:
    if install_mode != _SOPERATOR_TARGET_MODE_MANAGED:
        return
    slurm_config = values.get("slurmConfig")
    if not _soperator_any_worker_node_group_ephemeral_enabled(inputs):
        if isinstance(slurm_config, dict):
            slurm_config.pop("suspendTime", None)
            if not slurm_config:
                values.pop("slurmConfig", None)
        return
    suspend_time_seconds = _soperator_worker_ephemeral_suspend_time_seconds(inputs)
    if not isinstance(slurm_config, dict):
        slurm_config = {}
        values["slurmConfig"] = slurm_config
    slurm_config["suspendTime"] = suspend_time_seconds


def _materialize_soperator_component_defaults(payload: dict[str, Any]) -> bool:
    soperator_targets = _soperator_app_target_refs(payload)
    if not soperator_targets:
        return False
    changed = False
    soperator_filesystems_by_target: dict[str, dict[str, Any]] = {}
    profile_by_target = _soperator_profile_by_target(payload)
    install_mode_by_target = _soperator_target_mode_by_target(payload)

    infra_rows = _scope_rows(payload, scope="infra")
    apps_rows = _scope_rows(payload, scope="apps")
    mk8s_inputs_by_target: dict[str, dict[str, Any]] = _external_mk8s_inputs_by_target(payload)
    for row in infra_rows:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != "mk8s" or component_instance_id(row) not in soperator_targets:
            continue
        inputs = row.setdefault("inputs", {})
        if isinstance(inputs, dict):
            mk8s_inputs_by_target[component_instance_id(row)] = inputs
    enabled_sfs_rows = [
        row
        for row in infra_rows
        if isinstance(row, dict)
        and bool(row.get("enabled", False))
        and component_type_id(row) == "sfs"
    ]

    soperator_values_by_target: dict[str, dict[str, Any]] = {}
    soperator_placements_by_target: dict[str, dict[str, list[str]]] = {}
    generated_placements_by_target: dict[str, bool] = {}
    configured_placements_by_target: dict[str, bool] = {}
    for row in apps_rows:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != _SOPERATOR_APP_ID:
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if target_ref not in soperator_targets:
            continue
        before = copy.deepcopy(row)
        row.setdefault("profile", _default_soperator_profile_name())
        values = row.setdefault("values", {})
        if isinstance(values, dict):
            values.setdefault("partitionProfile", _default_soperator_partition_profile_name())
            values.setdefault("topologyProfile", _default_soperator_topology_profile_name())
            soperator_values_by_target[target_ref] = values
            placements = _soperator_row_placements(row)
            soperator_placements_by_target[target_ref] = placements
            configured_placements_by_target[target_ref] = bool(placements)
            generated_placements = _soperator_placements_match_generated_profile(
                row=row,
                inputs=mk8s_inputs_by_target.get(target_ref, {}),
            )
            generated_placements_by_target[target_ref] = generated_placements
            if not placements:
                inferred_placements = _soperator_infer_placements(
                    inputs=mk8s_inputs_by_target.get(target_ref, {}),
                    profile=profile_by_target.get(target_ref, {}),
                )
                _soperator_set_app_placements(row, inferred_placements)
                if inferred_placements:
                    soperator_placements_by_target[target_ref] = inferred_placements
                    generated_placements_by_target[target_ref] = True
        if row != before:
            changed = True

    for row in infra_rows:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        component_id = component_type_id(row)
        inputs = row.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            continue
        before = copy.deepcopy(row)
        if component_id == "mk8s" and component_instance_id(row) in soperator_targets:
            target_ref = component_instance_id(row)
            _materialize_soperator_mk8s_profile(
                target_ref=target_ref,
                inputs=inputs,
                profile=profile_by_target.get(target_ref, {}),
                values=soperator_values_by_target.get(target_ref, {}),
                placements=soperator_placements_by_target.get(target_ref, {}),
                install_mode=install_mode_by_target.get(
                    target_ref,
                    _default_soperator_target_mode(),
                ),
                replace_profile_managed_groups=generated_placements_by_target.get(
                    target_ref,
                    False,
                ),
                placements_configured=configured_placements_by_target.get(target_ref, False),
            )
        elif component_id == "sfs":
            sfs_instance_id = component_instance_id(row)
            target_ref = ""
            if sfs_instance_id in soperator_targets:
                target_ref = sfs_instance_id
            elif len(enabled_sfs_rows) == 1 and len(soperator_targets) == 1:
                target_ref = soperator_targets[0]
            if target_ref:
                soperator_filesystems_by_target[target_ref] = _materialize_soperator_sfs_profile(
                    inputs=inputs,
                    profile=profile_by_target.get(target_ref, {}),
                    target_ref=target_ref,
                )
        if row != before:
            changed = True

    for row in apps_rows:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != _SOPERATOR_APP_ID:
            continue
        target_ref = app_chart_target_ref(row) or component_instance_id(row)
        if target_ref not in soperator_targets:
            continue
        before = copy.deepcopy(row)
        values = row.setdefault("values", {})
        if isinstance(values, dict):
            _delete_mapping_path_value(values, _SOPERATOR_ACTIVECHECKS_READY_PARTITION_PATH)
            inferred_placements = _soperator_infer_placements(
                inputs=mk8s_inputs_by_target.get(target_ref, {}),
                profile=profile_by_target.get(target_ref, {}),
            )
            replace_generated_placements = generated_placements_by_target.get(
                target_ref,
                False,
            )
            current_placements = _soperator_row_placements(row)
            if not replace_generated_placements:
                replace_generated_placements = _soperator_placements_are_stale_profile_templates(
                    placements=current_placements,
                    profile=profile_by_target.get(target_ref, {}),
                    inferred_placements=inferred_placements,
                )
            if replace_generated_placements:
                generated_placements_by_target[target_ref] = True
            _soperator_set_app_placements(
                row,
                inferred_placements,
                replace=replace_generated_placements,
            )
            placements = _soperator_row_placements(row)
            soperator_placements_by_target[target_ref] = placements
            chart_profile = _profile_mapping(
                _profile_mapping(profile_by_target.get(target_ref, {}), "chart"),
                "values",
            )
            install_mode = install_mode_by_target.get(
                target_ref,
                _default_soperator_target_mode(),
            )
            preserve_adopted_worker_nodesets = (
                install_mode == _SOPERATOR_TARGET_MODE_REGISTERED
                and _soperator_preserve_onboarded_worker_nodesets(
                    values=values,
                    install_mode=install_mode,
                )
            )
            chart_profile_for_merge = dict(chart_profile)
            if (
                install_mode == _SOPERATOR_TARGET_MODE_REGISTERED
                and isinstance(values.get("nodesets"), list)
                and "nodesets" in chart_profile
            ):
                chart_profile_for_merge.pop("nodesets", None)
            if preserve_adopted_worker_nodesets and "partitionConfiguration" in chart_profile:
                chart_profile_for_merge.pop("partitionConfiguration", None)
            _merge_soperator_profile_values(
                values,
                chart_profile_for_merge,
                {},
                replaceable_base_values=_soperator_all_profile_chart_values(),
            )
            _merge_missing_mapping(
                values,
                _soperator_profile_install_mode_chart_values(
                    profile_by_target.get(target_ref, {}),
                    install_mode=install_mode,
                ),
            )
            if not preserve_adopted_worker_nodesets:
                _merge_missing_soperator_profile_partitions(values, chart_profile)
                _materialize_soperator_partition_profile(
                    values=values,
                    profile=profile_by_target.get(target_ref, {}),
                    install_mode=install_mode,
                )
            _materialize_soperator_topology_profile(
                values=values,
                profile=profile_by_target.get(target_ref, {}),
                install_mode=install_mode,
            )
            _materialize_soperator_dcgm_exporter_values(
                values=values,
                inputs=mk8s_inputs_by_target.get(target_ref, {}),
            )
            _materialize_soperator_mapping_chart_values(
                values=values,
                profile=profile_by_target.get(target_ref, {}),
                inputs=mk8s_inputs_by_target.get(target_ref, {}),
                placements=placements,
                install_mode=install_mode,
                preserve_existing_worker_nodesets=preserve_adopted_worker_nodesets,
            )
            _materialize_soperator_worker_ephemeral_values(
                values=values,
                inputs=mk8s_inputs_by_target.get(target_ref, {}),
                install_mode=install_mode,
            )
            ensure_soperator_gpu_driver_jail_values(
                values,
                context=f"Soperator target {target_ref}",
            )
            _materialize_soperator_guided_sssd_values(values)
            _materialize_soperator_rest_compatibility(values)
            _soperator_extend_nodeconfigurator_tolerations_from_nodesets(values)
            _remove_internal_activechecks_partition(values)
            current_cluster_name = str(values.get("clusterName", "") or "").strip()
            install_mode = install_mode_by_target.get(
                target_ref,
                _default_soperator_target_mode(),
            )
            preserve_adopted_live_cluster_name = install_mode == _SOPERATOR_TARGET_MODE_REGISTERED
            if not current_cluster_name or (
                current_cluster_name == "mk8s"
                and target_ref != "mk8s"
                and not preserve_adopted_live_cluster_name
            ):
                values["clusterName"] = target_ref
            slurm_nodes = values.setdefault("slurmNodes", {})
            if isinstance(slurm_nodes, dict):
                login = slurm_nodes.setdefault("login", {})
                if isinstance(login, dict):
                    login.setdefault("sshRootPublicKeys", [])
            soperator_filesystems = soperator_filesystems_by_target.get(target_ref, {})
            if soperator_filesystems:
                sfs_values = values.setdefault("sfs", {})
                if isinstance(sfs_values, dict):
                    filesystems_values = sfs_values.setdefault("filesystems", {})
                    if isinstance(filesystems_values, dict):
                        for filesystem_key, filesystem_spec in soperator_filesystems.items():
                            if not isinstance(filesystem_spec, Mapping):
                                filesystems_values[filesystem_key] = copy.deepcopy(
                                    to_plain_data(filesystem_spec)
                                )
                                continue
                            target_filesystem = filesystems_values.setdefault(filesystem_key, {})
                            if isinstance(target_filesystem, dict):
                                _merge_replace_mapping(target_filesystem, filesystem_spec)
                            else:
                                filesystems_values[filesystem_key] = copy.deepcopy(
                                    to_plain_data(filesystem_spec)
                                )
                volume_values = values.setdefault("volume", {})
                if isinstance(volume_values, dict):
                    jail_fs = soperator_filesystems.get("jail", {})
                    if isinstance(jail_fs, Mapping):
                        jail_values = volume_values.setdefault("jail", {})
                        if isinstance(jail_values, dict):
                            if "size_gib" in jail_fs:
                                jail_values["size"] = f"{jail_fs['size_gib']}Gi"
                            if str(jail_fs.get("mount_tag", "") or "").strip():
                                jail_values["filestoreDeviceName"] = jail_fs["mount_tag"]
                    controller_fs = soperator_filesystems.get("controller-spool", {})
                    if isinstance(controller_fs, Mapping):
                        controller_values = volume_values.setdefault("controllerSpool", {})
                        if isinstance(controller_values, dict):
                            if "size_gib" in controller_fs:
                                controller_values["size"] = f"{controller_fs['size_gib']}Gi"
                            if str(controller_fs.get("mount_tag", "") or "").strip():
                                controller_values["filestoreDeviceName"] = controller_fs[
                                    "mount_tag"
                                ]
                    accounting_fs = soperator_filesystems.get("accounting", {})
                    if isinstance(accounting_fs, Mapping):
                        accounting_values = volume_values.setdefault("accounting", {})
                        if isinstance(accounting_values, dict):
                            if "size_gib" in accounting_fs:
                                accounting_values["size"] = f"{accounting_fs['size_gib']}Gi"
                            if str(accounting_fs.get("mount_tag", "") or "").strip():
                                accounting_values["filestoreDeviceName"] = accounting_fs[
                                    "mount_tag"
                                ]
        if row != before:
            changed = True
    if _prune_sfs_single_filesystem_inputs_for_mapped_filesystems(payload):
        changed = True
    return changed


def _scope_rows(payload: dict[str, Any], *, scope: ComponentScope) -> list[dict[str, Any]]:
    if scope == "infra":
        infra_node = payload.setdefault("infra", {})
        if not isinstance(infra_node, dict):
            raise RuntimeError("config payload infra section must be a mapping")
        rows = infra_node.setdefault("components", [])
    else:
        apps_node = payload.setdefault("apps", {})
        if not isinstance(apps_node, dict):
            raise RuntimeError("config payload apps section must be a mapping")
        rows = apps_node.setdefault("charts", [])
    if not isinstance(rows, list):
        raise RuntimeError(f"config payload {scope} rows must be a list")
    for item in rows:
        if not isinstance(item, dict):
            continue
        component_id = component_type_id(item)
        if not component_id:
            continue
        ensure_component_instance_id(item, default_component_id=component_id)
    return rows


def _set_mapping_path_value(node: dict[str, Any], dotted_path: str, value: Any) -> None:
    current: dict[str, Any] = node
    segments = [segment.strip() for segment in dotted_path.split(".") if segment.strip()]
    if not segments:
        return
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
            current[segment] = child
        current = child
    current[segments[-1]] = value


def _delete_mapping_path_value(node: dict[str, Any], dotted_path: str) -> bool:
    current: Any = node
    segments = [segment.strip() for segment in dotted_path.split(".") if segment.strip()]
    if not segments:
        return False
    for segment in segments[:-1]:
        if not isinstance(current, dict):
            return False
        current = current.get(segment)
    if not isinstance(current, dict) or segments[-1] not in current:
        return False
    del current[segments[-1]]
    return True


def _prune_sfs_single_filesystem_inputs_for_mapped_filesystems(
    payload: dict[str, Any],
) -> bool:
    changed = False
    for row in _scope_rows(payload, scope="infra"):
        if not isinstance(row, dict) or component_type_id(row) != "sfs":
            continue
        inputs = row.get("inputs")
        if not isinstance(inputs, dict):
            continue
        filesystems = inputs.get("filesystems")
        if not (isinstance(filesystems, Mapping) and bool(filesystems)):
            continue
        for key in ("name", "size_gib", "mount_tag"):
            if key in inputs:
                del inputs[key]
                changed = True
    return changed


def _resolve_mapping_segment(node: Mapping[str, Any], segment: str) -> Any:
    candidates = (segment, segment.replace("-", "_"), segment.replace("_", "-"))
    for candidate in candidates:
        if candidate in node:
            return node[candidate]
    return None


def _mapping_path_value(node: Mapping[str, Any], dotted_path: str) -> Any:
    current: Any = node
    for raw_segment in dotted_path.split("."):
        segment = raw_segment.strip()
        if not segment or not isinstance(current, Mapping):
            return None
        current = _resolve_mapping_segment(current, segment)
        if current is None:
            return None
    return current
