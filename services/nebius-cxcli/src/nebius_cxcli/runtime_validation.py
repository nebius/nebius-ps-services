"""Runtime validation for config payloads."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from .component_defaults import (
    component_path_has_material_value,
    read_component_path,
    shared_default_target_paths,
)
from .component_instances import (
    INSTANCE_ID_FIELD,
    INSTANCE_ID_PATTERN,
    component_instance_id,
    component_type_id,
    normalize_component_token,
)
from .component_wiring import row_input_bindings
from .components import (
    ComponentScope,
    component_entries,
    component_lookup,
    parse_dependency_ref,
)
from .deploy_targets import (
    EXTERNAL_MK8S_TARGET_KIND,
    EXTERNAL_TARGET_OWNERSHIP,
    deploy_target_is_external_mk8s,
)
from .mk8s_gpu import mk8s_gpu_dependency_issues
from .mysterybox_eso import mysterybox_eso_dependency_issues
from .observability import observability_dependency_issues
from .regions import SUPPORTED_REGION_IDS
from .runtime_component_validation import validate_soperator_qos_partition_profiles
from .runtime_config import read_path_with_catalog
from .runtime_plugin_validation import run_runtime_validation_plugins
from .soperator_onboarding import (
    ONBOARDING_ACTION_IDS,
    ONBOARDING_COMPUTE_MODES,
    ONBOARDING_STORAGE_MODES,
    SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA,
    validate_soperator_onboarding_acceptance,
)
from .soperator_upgrade_campaign import soperator_upgrade_campaign_fingerprint

_ROOT_KEYS = frozenset({"version", "client_info", "deploy", "infra", "apps"})
_ID_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_SECTION_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_CLIENT_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_REFRESH_INTERVAL_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)(?:s|m|h)(?:(?:0|[1-9][0-9]*)(?:s|m|h))*$"
)
_FOLDED_SOPERATOR_CHILD_APP_IDS = frozenset(
    {
        "soperator-activechecks",
        "soperator-backup-config",
        "soperator-checks",
        "soperator-dcgm-exporter",
        "soperator-notifier",
    }
)
_FOLDED_SOPERATOR_DEPENDENCY_APP_IDS = frozenset({"k8up"})
_SOPERATOR_ONBOARDING_KEYS = frozenset(
    {
        "accepted",
        "actions",
        "analysis_fingerprint",
        "collection_errors",
        "compute_mode",
        "migration_profile_id",
        "node_template_upgrade",
        "source_version",
        "state",
        "storage_mode",
        "support_message",
        "support_override_used",
        "support_rule_id",
        "support_status",
        "target_version",
        "upgrade_path",
    }
)
_SOPERATOR_ONBOARDING_NODE_TEMPLATE_KEYS = frozenset(
    {
        "target_k8s_version",
        "target_os",
        "target_gpu_stack_preset",
        "slurm_scheduling_pause",
    }
)
_SOPERATOR_UPGRADE_CAMPAIGN_KEYS = frozenset(
    {
        "campaign_id",
        "capabilities_source",
        "catalog_fingerprint",
        "created_at",
        "fingerprint",
        "final_targets",
        "identity",
        "jail_rootfs",
        "locked",
        "managed_operators",
        "mk8s",
        "recommended_order",
        "recommended_order_policy",
        "compute_migration",
        "schema",
        "segments",
        "source_provenance",
        "source_k8s_version",
        "soperator_app",
        "soperator_chart",
        "support_rule_id",
        "support_status",
        "target_k8s_version",
    }
)
_SOPERATOR_UPGRADE_CAMPAIGN_IDENTITY_KEYS = frozenset(
    {
        "cluster_id",
        "cluster_name",
        "jail_filesystem_id",
        "kubernetes_uid",
        "project_id",
        "slurmcluster_uid",
        "soperator_uid",
        "target_ref",
    }
)
_SOPERATOR_UPGRADE_CAMPAIGN_SOURCE_PROVENANCE_KEYS = frozenset(
    {
        "component_catalog",
        "discovery",
        "provider_capabilities",
        "source_contract",
        "support_policy",
    }
)
_SOPERATOR_UPGRADE_CAMPAIGN_FINAL_TARGET_KEYS = frozenset(
    {
        "jail_artifact_digest",
        "jail_artifact_identity_warning",
        "jail_cuda_version",
        "jail_rootfs_image",
        "jail_rootfs_version",
        "kubernetes",
        "soperator_app",
        "soperator_chart",
    }
)
_SOPERATOR_UPGRADE_CAMPAIGN_MANAGED_OPERATOR_ROLES = frozenset({"gpu", "network"})
_SOPERATOR_UPGRADE_CAMPAIGN_MANAGED_OPERATOR_KEYS = frozenset(
    {
        "chart",
        "chart_version",
        "component_id",
        "namespace",
        "release_name",
        "repository",
    }
)
_SOPERATOR_UPGRADE_CAMPAIGN_COMPUTE_MIGRATION_KEYS = frozenset(
    {
        "busy_worker_policy",
        "login_session_policy",
        "mode",
        "slurm_scheduling_pause",
        "source_node_groups",
        "target_node_groups",
    }
)
_SOPERATOR_UPGRADE_CAMPAIGN_SEGMENT_KEYS = frozenset(
    {
        "actions",
        "current_k8s_version",
        "depends_on",
        "id",
        "index",
        "jail_rootfs",
        "k8s_upgrade_required",
        "kind",
        "mk8s",
        "soperator_app",
        "soperator_chart",
        "soperator_upgrade_required",
        "target_k8s_version",
        "title",
    }
)
_SOPERATOR_UPGRADE_CAMPAIGN_MK8S_KEYS = frozenset({"control_plane", "node_groups"})
_SOPERATOR_UPGRADE_CAMPAIGN_CONTROL_PLANE_KEYS = frozenset({"source_version", "target_version"})
_SOPERATOR_UPGRADE_CAMPAIGN_NODE_GROUP_KEYS = frozenset(
    {
        "compatibility_source",
        "gpu_software_mode",
        "id",
        "name",
        "platform",
        "preset",
        "role",
        "source",
        "target",
    }
)
_SOPERATOR_UPGRADE_CAMPAIGN_NODE_TEMPLATE_KEYS = frozenset(
    {"drivers_preset", "kubernetes_version", "os"}
)
_SOPERATOR_UPGRADE_CAMPAIGN_GPU_SOFTWARE_MODES = frozenset(
    {"none", "operator-managed", "provider-managed"}
)
_SOPERATOR_UPGRADE_CAMPAIGN_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SOPERATOR_UPGRADE_CAMPAIGN_ID_PATTERN = re.compile(r"^campaign-[0-9a-f]{16}$")
_SOPERATOR_LOCKED_VERSION_RECORD_KEYS = frozenset(
    {"current_version", "target_version", "upgrade_required"}
)
_SOPERATOR_LOCKED_JAIL_ROOTFS_KEYS = frozenset(
    {
        "current_image",
        "current_evidence_reason",
        "current_evidence_status",
        "current_jail_filesystem_id",
        "current_job_name",
        "current_job_uid",
        "current_pvc_name",
        "current_pvc_uid",
        "current_slot",
        "current_source",
        "current_version",
        "live_desired_image",
        "live_desired_source",
        "live_desired_version",
        "reason",
        "refresh_required",
        "slurmcluster_name",
        "target_image",
        "target_cuda_version",
        "target_digest",
        "target_identity_warning",
        "target_source",
        "target_version",
    }
)


def _get_path(payload: Mapping[str, Any], dotted_path: str, default: Any = None) -> Any:
    resolved = read_path_with_catalog(payload, dotted_path)
    return default if resolved is None else resolved


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _validate_unknown_keys(
    value: Mapping[str, Any],
    *,
    allowed_keys: frozenset[str],
    field_label: str,
) -> None:
    unknown_keys = sorted(str(key) for key in value if str(key) not in allowed_keys)
    if unknown_keys:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown_keys))


def _optional_string_for_validation(value: Any, field_label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_label} must be a string")
    return value.strip()


def _required_string_for_validation(value: Any, field_label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_label} must be a non-empty string")
    return value.strip()


def _validate_k8s_minor_version_for_validation(value: Any, field_label: str) -> None:
    text = _optional_string_for_validation(value, field_label)
    if not text:
        return
    raw = text.lstrip("v")
    if not re.fullmatch(r"[0-9]+\.[0-9]+", raw):
        raise ValueError(f"{field_label} must be a Kubernetes major.minor version")


def _required_k8s_minor_for_validation(value: Any, field_label: str) -> tuple[int, int]:
    text = _required_string_for_validation(value, field_label)
    raw = text.lstrip("v")
    if not re.fullmatch(r"[0-9]+\.[0-9]+", raw):
        raise ValueError(f"{field_label} must be a Kubernetes major.minor version")
    major, minor = raw.split(".", maxsplit=1)
    return int(major), int(minor)


def _validate_soperator_onboarding_actions(value: Any, field_label: str) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise ValueError(f"{field_label} must be a list")
    for index, raw_action in enumerate(value):
        if not isinstance(raw_action, str) or not raw_action.strip():
            raise ValueError(f"{field_label}[{index}] must be a non-empty string")
        action = raw_action.strip()
        if action not in ONBOARDING_ACTION_IDS:
            raise ValueError(
                f"{field_label}[{index}] has unsupported value '{action}'. "
                "Rerun `nebius-cxcli ext-soperator onboard` so the accepted config "
                "uses the current Soperator onboarding action contract."
            )


def _validate_soperator_onboarding_node_template(
    node_template: Any,
    field_label: str,
) -> None:
    if node_template is None:
        return
    if not isinstance(node_template, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        node_template,
        allowed_keys=_SOPERATOR_ONBOARDING_NODE_TEMPLATE_KEYS,
        field_label=field_label,
    )
    _validate_k8s_minor_version_for_validation(
        node_template.get("target_k8s_version"),
        f"{field_label}.target_k8s_version",
    )
    _optional_string_for_validation(node_template.get("target_os"), f"{field_label}.target_os")
    _optional_string_for_validation(
        node_template.get("target_gpu_stack_preset"),
        f"{field_label}.target_gpu_stack_preset",
    )
    if "slurm_scheduling_pause" in node_template and not isinstance(
        node_template.get("slurm_scheduling_pause"),
        bool,
    ):
        raise ValueError(f"{field_label}.slurm_scheduling_pause must be true or false")


def _validate_locked_version_record(record: Any, field_label: str) -> tuple[str, str, bool]:
    if not isinstance(record, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        record,
        allowed_keys=_SOPERATOR_LOCKED_VERSION_RECORD_KEYS,
        field_label=field_label,
    )
    current_version = _required_string_for_validation(
        record.get("current_version"),
        f"{field_label}.current_version",
    )
    target_version = _required_string_for_validation(
        record.get("target_version"),
        f"{field_label}.target_version",
    )
    upgrade_required = record.get("upgrade_required")
    if not isinstance(upgrade_required, bool):
        raise ValueError(f"{field_label}.upgrade_required must be true or false")
    expected_upgrade_required = current_version != target_version
    if upgrade_required is not expected_upgrade_required:
        raise ValueError(
            f"{field_label}.upgrade_required must be "
            f"{'true' if expected_upgrade_required else 'false'} because current_version "
            f"{'differs from' if expected_upgrade_required else 'matches'} target_version"
        )
    return current_version, target_version, upgrade_required


def _validate_locked_jail_rootfs_record(record: Any, field_label: str) -> None:
    if not isinstance(record, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        record,
        allowed_keys=_SOPERATOR_LOCKED_JAIL_ROOTFS_KEYS,
        field_label=field_label,
    )
    for key in (
        "current_image",
        "current_evidence_reason",
        "current_evidence_status",
        "current_jail_filesystem_id",
        "current_job_name",
        "current_job_uid",
        "current_pvc_name",
        "current_pvc_uid",
        "current_slot",
        "current_source",
        "current_version",
        "live_desired_image",
        "live_desired_source",
        "live_desired_version",
        "reason",
        "slurmcluster_name",
        "target_image",
        "target_cuda_version",
        "target_digest",
        "target_identity_warning",
        "target_source",
        "target_version",
    ):
        if key in record:
            _optional_string_for_validation(record.get(key), f"{field_label}.{key}")
    if not isinstance(record.get("refresh_required"), bool):
        raise ValueError(f"{field_label}.refresh_required must be true or false")


def _soperator_campaign_jail_source_identity(
    record: Mapping[str, Any],
    field_label: str,
) -> tuple[str, str]:
    return (
        _required_string_for_validation(
            record.get("current_image"),
            f"{field_label}.current_image",
        ),
        _required_string_for_validation(
            record.get("current_version"),
            f"{field_label}.current_version",
        ),
    )


def _soperator_campaign_jail_target_identity(
    record: Mapping[str, Any],
    field_label: str,
) -> tuple[str, str, str, str, str, str]:
    return (
        _required_string_for_validation(
            record.get("target_image"),
            f"{field_label}.target_image",
        ),
        _required_string_for_validation(
            record.get("target_version"),
            f"{field_label}.target_version",
        ),
        _required_string_for_validation(
            record.get("target_cuda_version"),
            f"{field_label}.target_cuda_version",
        ),
        _required_string_for_validation(
            record.get("target_source"),
            f"{field_label}.target_source",
        ),
        _as_text(record.get("target_digest")),
        _as_text(record.get("target_identity_warning")),
    )


def _validate_soperator_campaign_identity(
    identity: Any,
    field_label: str,
    *,
    expected_project_id: str,
    expected_cluster_id: str,
    expected_target_ref: str,
) -> None:
    if not isinstance(identity, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        identity,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_IDENTITY_KEYS,
        field_label=field_label,
    )
    immutable_keys = (
        "project_id",
        "cluster_id",
        "target_ref",
        "kubernetes_uid",
        "soperator_uid",
        "slurmcluster_uid",
        "jail_filesystem_id",
    )
    for key in immutable_keys:
        _required_string_for_validation(identity.get(key), f"{field_label}.{key}")
    if "cluster_name" not in identity:
        raise ValueError(f"{field_label}.cluster_name is required")
    _optional_string_for_validation(identity.get("cluster_name"), f"{field_label}.cluster_name")

    expected_values = {
        "project_id": expected_project_id,
        "cluster_id": expected_cluster_id,
        "target_ref": expected_target_ref,
    }
    for key, expected in expected_values.items():
        if not expected:
            raise ValueError(
                f"{field_label}.{key} cannot be verified because the owning config field is empty"
            )
        actual = _as_text(identity.get(key))
        if actual != expected:
            raise ValueError(f"{field_label}.{key} must match the owning config value")


def _validate_soperator_campaign_source_provenance(value: Any, field_label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        value,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_SOURCE_PROVENANCE_KEYS,
        field_label=field_label,
    )
    expected_values = {
        "discovery": "live-kubernetes-and-nebius-sdk",
        "provider_capabilities": "nebius-sdk-list-versions-and-compatibility-matrix",
        "component_catalog": "component_sources.yaml",
        "support_policy": "committed-soperator-upgrade-support-policy",
        "source_contract": "campaign-source-waypoints",
    }
    for key, expected in expected_values.items():
        actual = _required_string_for_validation(value.get(key), f"{field_label}.{key}")
        if actual != expected:
            raise ValueError(f"{field_label}.{key} must be '{expected}'")


def _validate_soperator_campaign_final_targets(
    value: Any,
    field_label: str,
    *,
    target_k8s: tuple[int, int],
    soperator_app: Mapping[str, Any],
    soperator_chart: Mapping[str, Any],
    jail_rootfs: Mapping[str, Any],
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        value,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_FINAL_TARGET_KEYS,
        field_label=field_label,
    )
    for key in _SOPERATOR_UPGRADE_CAMPAIGN_FINAL_TARGET_KEYS:
        if key not in value:
            raise ValueError(f"{field_label}.{key} is required")
    final_k8s = _required_k8s_minor_for_validation(
        value.get("kubernetes"),
        f"{field_label}.kubernetes",
    )
    if final_k8s != target_k8s:
        raise ValueError(f"{field_label}.kubernetes must match target_k8s_version")
    cross_checks = {
        "soperator_app": _as_text(soperator_app.get("target_version")),
        "soperator_chart": _as_text(soperator_chart.get("target_version")),
        "jail_rootfs_image": _as_text(jail_rootfs.get("target_image")),
        "jail_rootfs_version": _as_text(jail_rootfs.get("target_version")),
        "jail_cuda_version": _as_text(jail_rootfs.get("target_cuda_version")),
    }
    for key, expected in cross_checks.items():
        actual = _required_string_for_validation(value.get(key), f"{field_label}.{key}")
        if not expected or actual != expected:
            raise ValueError(f"{field_label}.{key} must match the campaign target record")
    artifact_digest = _optional_string_for_validation(
        value.get("jail_artifact_digest"),
        f"{field_label}.jail_artifact_digest",
    )
    identity_warning = _optional_string_for_validation(
        value.get("jail_artifact_identity_warning"),
        f"{field_label}.jail_artifact_identity_warning",
    )
    if artifact_digest != _as_text(jail_rootfs.get("target_digest")):
        raise ValueError(
            f"{field_label}.jail_artifact_digest must match the campaign target record"
        )
    if identity_warning != _as_text(jail_rootfs.get("target_identity_warning")):
        raise ValueError(
            f"{field_label}.jail_artifact_identity_warning must match the campaign target record"
        )
    if artifact_digest and not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_digest):
        raise ValueError(f"{field_label}.jail_artifact_digest must be a sha256 digest when set")
    if not artifact_digest and not identity_warning:
        raise ValueError(
            f"{field_label} requires jail_artifact_digest or jail_artifact_identity_warning"
        )


def _validate_soperator_campaign_managed_operators(
    value: Any,
    field_label: str,
    *,
    required: bool,
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        value,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_MANAGED_OPERATOR_ROLES,
        field_label=field_label,
    )
    if not required:
        if value:
            raise ValueError(
                f"{field_label} must be empty when every campaign node group is CPU-only"
            )
        return
    expected_component_ids = {
        "gpu": "nvidia-gpu-operator",
        "network": "nvidia-network-operator",
    }
    for role, expected_component_id in expected_component_ids.items():
        operator = value.get(role)
        operator_label = f"{field_label}.{role}"
        if not isinstance(operator, Mapping):
            raise ValueError(f"{operator_label} must be a mapping")
        _validate_unknown_keys(
            operator,
            allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_MANAGED_OPERATOR_KEYS,
            field_label=operator_label,
        )
        for key in _SOPERATOR_UPGRADE_CAMPAIGN_MANAGED_OPERATOR_KEYS:
            _required_string_for_validation(operator.get(key), f"{operator_label}.{key}")
        if _as_text(operator.get("component_id")) != expected_component_id:
            raise ValueError(f"{operator_label}.component_id must be '{expected_component_id}'")


def _required_campaign_int(
    value: Any,
    field_label: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_label} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        range_label = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{field_label} must be {range_label}")
    return value


def _validate_soperator_campaign_compute_migration(value: Any, field_label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        value,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_COMPUTE_MIGRATION_KEYS,
        field_label=field_label,
    )
    required_values = {
        "mode": "blue-green-replacement",
        "source_node_groups": "immutable-until-retirement",
        "target_node_groups": "replacement",
        "busy_worker_policy": "retain-until-job-and-epilog-finish",
        "login_session_policy": "voluntary-handoff",
    }
    for key, expected in required_values.items():
        actual = _required_string_for_validation(value.get(key), f"{field_label}.{key}")
        if actual != expected:
            raise ValueError(f"{field_label}.{key} must be '{expected}'")
    if not isinstance(value.get("slurm_scheduling_pause"), bool):
        raise ValueError(f"{field_label}.slurm_scheduling_pause must be true or false")

def _validate_soperator_campaign_node_template(
    value: Any,
    field_label: str,
) -> tuple[tuple[int, int], str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        value,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_NODE_TEMPLATE_KEYS,
        field_label=field_label,
    )
    for key in _SOPERATOR_UPGRADE_CAMPAIGN_NODE_TEMPLATE_KEYS:
        if key not in value:
            raise ValueError(f"{field_label}.{key} is required")
    kubernetes_version = _required_k8s_minor_for_validation(
        value.get("kubernetes_version"),
        f"{field_label}.kubernetes_version",
    )
    os_image = _required_string_for_validation(value.get("os"), f"{field_label}.os")
    drivers_preset = _optional_string_for_validation(
        value.get("drivers_preset"),
        f"{field_label}.drivers_preset",
    )
    return kubernetes_version, os_image, drivers_preset


def _validate_soperator_campaign_node_group(
    node_group: Any,
    field_label: str,
    *,
    control_plane_source: tuple[int, int],
    control_plane_target: tuple[int, int],
) -> tuple[str, str, tuple[tuple[int, int], str, str], tuple[tuple[int, int], str, str]]:
    if not isinstance(node_group, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        node_group,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_NODE_GROUP_KEYS,
        field_label=field_label,
    )
    for key in _SOPERATOR_UPGRADE_CAMPAIGN_NODE_GROUP_KEYS:
        if key not in node_group:
            raise ValueError(f"{field_label}.{key} is required")
    node_group_id = _required_string_for_validation(node_group.get("id"), f"{field_label}.id")
    node_group_name = _required_string_for_validation(
        node_group.get("name"),
        f"{field_label}.name",
    )
    for key in ("role", "platform"):
        _required_string_for_validation(node_group.get(key), f"{field_label}.{key}")
    _required_string_for_validation(node_group.get("preset"), f"{field_label}.preset")
    gpu_software_mode = _required_string_for_validation(
        node_group.get("gpu_software_mode"),
        f"{field_label}.gpu_software_mode",
    )
    if gpu_software_mode not in _SOPERATOR_UPGRADE_CAMPAIGN_GPU_SOFTWARE_MODES:
        raise ValueError(
            f"{field_label}.gpu_software_mode must be one of: "
            + ", ".join(sorted(_SOPERATOR_UPGRADE_CAMPAIGN_GPU_SOFTWARE_MODES))
        )
    compatibility_source = _required_string_for_validation(
        node_group.get("compatibility_source"),
        f"{field_label}.compatibility_source",
    )
    if compatibility_source != "nebius-sdk-get-compatibility-matrix":
        raise ValueError(
            f"{field_label}.compatibility_source must be 'nebius-sdk-get-compatibility-matrix'"
        )
    source = _validate_soperator_campaign_node_template(
        node_group.get("source"),
        f"{field_label}.source",
    )
    target = _validate_soperator_campaign_node_template(
        node_group.get("target"),
        f"{field_label}.target",
    )
    if source[0] != control_plane_source:
        raise ValueError(
            f"{field_label}.source.kubernetes_version must match the segment control-plane "
            "source_version"
        )
    if target[0] != control_plane_target:
        raise ValueError(
            f"{field_label}.target.kubernetes_version must match the segment control-plane "
            "target_version"
        )
    source_drivers = source[2]
    target_drivers = target[2]
    if gpu_software_mode == "provider-managed":
        if not source_drivers or not target_drivers:
            raise ValueError(
                f"{field_label} provider-managed GPU tuples require non-empty drivers_preset"
            )
    elif source_drivers or target_drivers:
        raise ValueError(
            f"{field_label} {gpu_software_mode} tuples must use an empty drivers_preset"
        )
    return node_group_id, node_group_name, source, target


def _validate_soperator_campaign_mk8s_segment(
    mk8s: Any,
    field_label: str,
    *,
    k8s_upgrade_required: bool,
) -> tuple[
    tuple[int, int],
    tuple[int, int],
    dict[str, tuple[tuple[int, int], str, str]],
    dict[str, tuple[tuple[int, int], str, str]],
]:
    if not isinstance(mk8s, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        mk8s,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_MK8S_KEYS,
        field_label=field_label,
    )
    control_plane = mk8s.get("control_plane")
    if not isinstance(control_plane, Mapping):
        raise ValueError(f"{field_label}.control_plane must be a mapping")
    _validate_unknown_keys(
        control_plane,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_CONTROL_PLANE_KEYS,
        field_label=f"{field_label}.control_plane",
    )
    for key in _SOPERATOR_UPGRADE_CAMPAIGN_CONTROL_PLANE_KEYS:
        if key not in control_plane:
            raise ValueError(f"{field_label}.control_plane.{key} is required")
    source = _required_k8s_minor_for_validation(
        control_plane.get("source_version"),
        f"{field_label}.control_plane.source_version",
    )
    target = _required_k8s_minor_for_validation(
        control_plane.get("target_version"),
        f"{field_label}.control_plane.target_version",
    )
    advances_one_minor = target[0] == source[0] and target[1] == source[1] + 1
    stays_on_minor = target == source
    if k8s_upgrade_required and not advances_one_minor:
        raise ValueError(f"{field_label}.control_plane must advance exactly one Kubernetes minor")
    if not k8s_upgrade_required and not stays_on_minor:
        raise ValueError(
            f"{field_label}.control_plane must stay on the current Kubernetes minor when "
            "k8s_upgrade_required is false"
        )

    node_groups = mk8s.get("node_groups")
    if not isinstance(node_groups, list):
        raise ValueError(f"{field_label}.node_groups must be a list")
    if k8s_upgrade_required and not node_groups:
        raise ValueError(f"{field_label}.node_groups must not be empty for a Kubernetes hop")
    source_by_id: dict[str, tuple[tuple[int, int], str, str]] = {}
    target_by_id: dict[str, tuple[tuple[int, int], str, str]] = {}
    seen_names: set[str] = set()
    for index, node_group in enumerate(node_groups):
        node_group_id, node_group_name, group_source, group_target = (
            _validate_soperator_campaign_node_group(
                node_group,
                f"{field_label}.node_groups[{index}]",
                control_plane_source=source,
                control_plane_target=target,
            )
        )
        if node_group_id in source_by_id:
            raise ValueError(
                f"{field_label}.node_groups[{index}].id '{node_group_id}' is duplicated"
            )
        if node_group_name in seen_names:
            raise ValueError(
                f"{field_label}.node_groups[{index}].name '{node_group_name}' is duplicated"
            )
        source_by_id[node_group_id] = group_source
        target_by_id[node_group_id] = group_target
        seen_names.add(node_group_name)
    return source, target, source_by_id, target_by_id


def _validate_soperator_campaign_mk8s_inventory(
    mk8s: Any,
    field_label: str,
) -> tuple[
    tuple[int, int],
    tuple[int, int],
    dict[str, tuple[tuple[int, int], str, str]],
    dict[str, tuple[tuple[int, int], str, str]],
]:
    if not isinstance(mk8s, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        mk8s,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_MK8S_KEYS,
        field_label=field_label,
    )
    control_plane = mk8s.get("control_plane")
    if not isinstance(control_plane, Mapping):
        raise ValueError(f"{field_label}.control_plane must be a mapping")
    _validate_unknown_keys(
        control_plane,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_CONTROL_PLANE_KEYS,
        field_label=f"{field_label}.control_plane",
    )
    source = _required_k8s_minor_for_validation(
        control_plane.get("source_version"),
        f"{field_label}.control_plane.source_version",
    )
    target = _required_k8s_minor_for_validation(
        control_plane.get("target_version"),
        f"{field_label}.control_plane.target_version",
    )
    if source[0] != target[0] or source[1] > target[1]:
        raise ValueError(f"{field_label}.control_plane must not downgrade or cross a major")
    node_groups = mk8s.get("node_groups")
    if not isinstance(node_groups, list):
        raise ValueError(f"{field_label}.node_groups must be a list")
    source_by_id: dict[str, tuple[tuple[int, int], str, str]] = {}
    target_by_id: dict[str, tuple[tuple[int, int], str, str]] = {}
    seen_names: set[str] = set()
    for index, node_group in enumerate(node_groups):
        node_group_id, node_group_name, group_source, group_target = (
            _validate_soperator_campaign_node_group(
                node_group,
                f"{field_label}.node_groups[{index}]",
                control_plane_source=source,
                control_plane_target=target,
            )
        )
        if node_group_id in source_by_id:
            raise ValueError(
                f"{field_label}.node_groups[{index}].id '{node_group_id}' is duplicated"
            )
        if node_group_name in seen_names:
            raise ValueError(
                f"{field_label}.node_groups[{index}].name '{node_group_name}' is duplicated"
            )
        source_by_id[node_group_id] = group_source
        target_by_id[node_group_id] = group_target
        seen_names.add(node_group_name)
    return source, target, source_by_id, target_by_id


def _validate_soperator_campaign_segment(
    segment: Any,
    field_label: str,
    *,
    expected_index: int,
    expected_dependency_id: str,
) -> tuple[
    str,
    tuple[int, int],
    tuple[int, int],
    dict[str, tuple[tuple[int, int], str, str]],
    dict[str, tuple[tuple[int, int], str, str]],
]:
    if not isinstance(segment, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    _validate_unknown_keys(
        segment,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_SEGMENT_KEYS,
        field_label=field_label,
    )
    for key in ("id", "kind", "title"):
        _required_string_for_validation(segment.get(key), f"{field_label}.{key}")
    for key in (
        "current_k8s_version",
        "target_k8s_version",
    ):
        if key not in segment:
            raise ValueError(f"{field_label}.{key} is required")
        _optional_string_for_validation(segment.get(key), f"{field_label}.{key}")
    _validate_locked_version_record(
        segment.get("soperator_app"),
        f"{field_label}.soperator_app",
    )
    _validate_locked_version_record(
        segment.get("soperator_chart"),
        f"{field_label}.soperator_chart",
    )
    _validate_locked_jail_rootfs_record(
        segment.get("jail_rootfs"),
        f"{field_label}.jail_rootfs",
    )
    for key in ("current_k8s_version", "target_k8s_version"):
        _validate_k8s_minor_version_for_validation(segment.get(key), f"{field_label}.{key}")
    for key in ("k8s_upgrade_required", "soperator_upgrade_required"):
        if not isinstance(segment.get(key), bool):
            raise ValueError(f"{field_label}.{key} must be true or false")
    index = segment.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index != expected_index:
        raise ValueError(f"{field_label}.index must be {expected_index}")
    depends_on = segment.get("depends_on")
    expected_dependencies = [expected_dependency_id] if expected_dependency_id else []
    if depends_on != expected_dependencies:
        raise ValueError(f"{field_label}.depends_on must be {expected_dependencies}")
    if "actions" not in segment:
        raise ValueError(f"{field_label}.actions is required")
    _validate_soperator_onboarding_actions(segment.get("actions"), f"{field_label}.actions")
    current_k8s = _required_k8s_minor_for_validation(
        segment.get("current_k8s_version"),
        f"{field_label}.current_k8s_version",
    )
    target_k8s = _required_k8s_minor_for_validation(
        segment.get("target_k8s_version"),
        f"{field_label}.target_k8s_version",
    )
    mk8s_source, mk8s_target, group_sources, group_targets = (
        _validate_soperator_campaign_mk8s_segment(
            segment.get("mk8s"),
            f"{field_label}.mk8s",
            k8s_upgrade_required=segment.get("k8s_upgrade_required") is True,
        )
    )
    if current_k8s != mk8s_source:
        raise ValueError(
            f"{field_label}.current_k8s_version must match mk8s.control_plane.source_version"
        )
    if target_k8s != mk8s_target:
        raise ValueError(
            f"{field_label}.target_k8s_version must match mk8s.control_plane.target_version"
        )
    return (
        _as_text(segment.get("id")),
        mk8s_source,
        mk8s_target,
        group_sources,
        group_targets,
    )


def _validate_soperator_upgrade_campaign(
    upgrade_path: Any,
    field_label: str,
    *,
    expected_project_id: str,
    expected_cluster_id: str,
    expected_target_ref: str,
) -> None:
    if upgrade_path is None:
        raise ValueError(
            f"{field_label} is required and must use '{SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA}'"
        )
    if not isinstance(upgrade_path, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    schema = upgrade_path.get("schema")
    if schema != SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA:
        raise ValueError(f"{field_label}.schema must be '{SOPERATOR_LOCKED_UPGRADE_PATH_SCHEMA}'")
    _validate_unknown_keys(
        upgrade_path,
        allowed_keys=_SOPERATOR_UPGRADE_CAMPAIGN_KEYS,
        field_label=field_label,
    )
    if upgrade_path.get("locked") is not True:
        raise ValueError(f"{field_label}.locked must be true")
    campaign_id = _required_string_for_validation(
        upgrade_path.get("campaign_id"),
        f"{field_label}.campaign_id",
    )
    if not _SOPERATOR_UPGRADE_CAMPAIGN_ID_PATTERN.fullmatch(campaign_id):
        raise ValueError(
            f"{field_label}.campaign_id must be 'campaign-' plus 16 lowercase hex digits"
        )
    fingerprint = _required_string_for_validation(
        upgrade_path.get("fingerprint"),
        f"{field_label}.fingerprint",
    )
    if not _SOPERATOR_UPGRADE_CAMPAIGN_FINGERPRINT_PATTERN.fullmatch(fingerprint):
        raise ValueError(f"{field_label}.fingerprint must be 64 lowercase hex digits")
    catalog_fingerprint = _required_string_for_validation(
        upgrade_path.get("catalog_fingerprint"),
        f"{field_label}.catalog_fingerprint",
    )
    if not _SOPERATOR_UPGRADE_CAMPAIGN_FINGERPRINT_PATTERN.fullmatch(catalog_fingerprint):
        raise ValueError(f"{field_label}.catalog_fingerprint must be 64 lowercase hex digits")
    capabilities_source = _required_string_for_validation(
        upgrade_path.get("capabilities_source"),
        f"{field_label}.capabilities_source",
    )
    if capabilities_source != "nebius-sdk":
        raise ValueError(f"{field_label}.capabilities_source must be 'nebius-sdk'")
    created_at = _required_string_for_validation(
        upgrade_path.get("created_at"),
        f"{field_label}.created_at",
    )
    try:
        parsed_created_at = datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise ValueError(f"{field_label}.created_at must be an ISO-8601 timestamp") from exc
    if parsed_created_at.tzinfo is None:
        raise ValueError(f"{field_label}.created_at must include a timezone")
    _validate_soperator_campaign_identity(
        upgrade_path.get("identity"),
        f"{field_label}.identity",
        expected_project_id=expected_project_id,
        expected_cluster_id=expected_cluster_id,
        expected_target_ref=expected_target_ref,
    )
    _validate_soperator_campaign_source_provenance(
        upgrade_path.get("source_provenance"),
        f"{field_label}.source_provenance",
    )
    for key in (
        "source_k8s_version",
        "target_k8s_version",
        "support_status",
        "support_rule_id",
    ):
        if key not in upgrade_path:
            raise ValueError(f"{field_label}.{key} is required")
        _optional_string_for_validation(upgrade_path.get(key), f"{field_label}.{key}")
    support_status = _as_text(upgrade_path.get("support_status"))
    if support_status not in {"supported", "supported_with_warning"}:
        raise ValueError(
            f"{field_label}.support_status must be supported or supported_with_warning"
        )
    soperator_app = upgrade_path.get("soperator_app")
    soperator_chart = upgrade_path.get("soperator_chart")
    jail_rootfs = upgrade_path.get("jail_rootfs")
    campaign_app_source, campaign_app_target, campaign_app_upgrade_required = (
        _validate_locked_version_record(
            soperator_app,
            f"{field_label}.soperator_app",
        )
    )
    campaign_chart_source, campaign_chart_target, campaign_chart_upgrade_required = (
        _validate_locked_version_record(
            soperator_chart,
            f"{field_label}.soperator_chart",
        )
    )
    _validate_locked_jail_rootfs_record(
        jail_rootfs,
        f"{field_label}.jail_rootfs",
    )
    source_k8s = _required_k8s_minor_for_validation(
        upgrade_path.get("source_k8s_version"),
        f"{field_label}.source_k8s_version",
    )
    target_k8s = _required_k8s_minor_for_validation(
        upgrade_path.get("target_k8s_version"),
        f"{field_label}.target_k8s_version",
    )
    if source_k8s[0] != target_k8s[0] or source_k8s[1] > target_k8s[1]:
        raise ValueError(
            f"{field_label} must not downgrade Kubernetes or cross a Kubernetes major version"
        )
    mk8s_source, mk8s_target, campaign_group_sources, campaign_group_targets = (
        _validate_soperator_campaign_mk8s_inventory(
            upgrade_path.get("mk8s"),
            f"{field_label}.mk8s",
        )
    )
    if mk8s_source != source_k8s or mk8s_target != target_k8s:
        raise ValueError(
            f"{field_label}.mk8s control-plane source/target must match campaign Kubernetes endpoints"
        )
    if not isinstance(soperator_app, Mapping):  # narrowed by validation above
        raise ValueError(f"{field_label}.soperator_app must be a mapping")
    if not isinstance(soperator_chart, Mapping):  # narrowed by validation above
        raise ValueError(f"{field_label}.soperator_chart must be a mapping")
    if not isinstance(jail_rootfs, Mapping):  # narrowed by validation above
        raise ValueError(f"{field_label}.jail_rootfs must be a mapping")
    campaign_jail_source = _soperator_campaign_jail_source_identity(
        jail_rootfs,
        f"{field_label}.jail_rootfs",
    )
    campaign_jail_target = _soperator_campaign_jail_target_identity(
        jail_rootfs,
        f"{field_label}.jail_rootfs",
    )
    campaign_jail_refresh_required = jail_rootfs.get("refresh_required") is True
    _validate_soperator_campaign_final_targets(
        upgrade_path.get("final_targets"),
        f"{field_label}.final_targets",
        target_k8s=target_k8s,
        soperator_app=soperator_app,
        soperator_chart=soperator_chart,
        jail_rootfs=jail_rootfs,
    )
    _validate_soperator_campaign_managed_operators(
        upgrade_path.get("managed_operators"),
        f"{field_label}.managed_operators",
        required=any(
            _as_text(node_group.get("gpu_software_mode")) != "none"
            for node_group in (
                upgrade_path.get("mk8s", {}).get("node_groups", [])
                if isinstance(upgrade_path.get("mk8s"), Mapping)
                else []
            )
            if isinstance(node_group, Mapping)
        ),
    )
    recommended_order = upgrade_path.get("recommended_order")
    if not isinstance(recommended_order, list):
        raise ValueError(f"{field_label}.recommended_order must be a list")
    for index, item in enumerate(recommended_order):
        if not isinstance(item, str):
            raise ValueError(f"{field_label}.recommended_order[{index}] must be a string")
    recommended_order_policy = upgrade_path.get("recommended_order_policy")
    if not isinstance(recommended_order_policy, Mapping):
        raise ValueError(f"{field_label}.recommended_order_policy must be a mapping")
    _validate_soperator_campaign_compute_migration(
        upgrade_path.get("compute_migration"),
        f"{field_label}.compute_migration",
    )
    segments = upgrade_path.get("segments")
    if not isinstance(segments, list):
        raise ValueError(f"{field_label}.segments must be a list")
    segment_ids: set[str] = set()
    current_control_plane = source_k8s
    current_soperator_app = campaign_app_source
    current_soperator_chart = campaign_chart_source
    current_jail_source = campaign_jail_source
    soperator_app_transition_count = 0
    soperator_chart_transition_count = 0
    jail_refresh_segment_count = 0
    expected_node_group_ids: set[str] | None = None
    previous_node_group_targets: dict[str, tuple[tuple[int, int], str, str]] = {}
    previous_segment_id = ""
    for index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            raise ValueError(f"{field_label}.segments[{index}] must be a mapping")
        (
            segment_id,
            segment_source,
            segment_target,
            group_sources,
            group_targets,
        ) = _validate_soperator_campaign_segment(
            segment,
            f"{field_label}.segments[{index}]",
            expected_index=index + 1,
            expected_dependency_id=previous_segment_id,
        )
        if segment_id in segment_ids:
            raise ValueError(f"{field_label}.segments[{index}].id '{segment_id}' is duplicated")
        segment_ids.add(segment_id)
        previous_segment_id = segment_id
        if segment_source != current_control_plane:
            raise ValueError(
                f"{field_label}.segments[{index}].mk8s.control_plane.source_version must "
                "match the previous segment target"
            )
        current_control_plane = segment_target
        if group_targets:
            group_ids = set(group_targets)
            if expected_node_group_ids is None:
                expected_node_group_ids = group_ids
            elif group_ids != expected_node_group_ids:
                raise ValueError(
                    f"{field_label}.segments[{index}].mk8s.node_groups must contain the same "
                    "immutable node-group ids as the previous Kubernetes hop"
                )
            for node_group_id, source_tuple in group_sources.items():
                previous_target = previous_node_group_targets.get(node_group_id)
                if previous_target is not None and source_tuple != previous_target:
                    raise ValueError(
                        f"{field_label}.segments[{index}].mk8s node group '{node_group_id}' "
                        "source tuple must match its previous segment target tuple"
                    )
            previous_node_group_targets = group_targets

        segment_app_source, segment_app_target, segment_app_upgrade_required = (
            _validate_locked_version_record(
                segment.get("soperator_app"),
                f"{field_label}.segments[{index}].soperator_app",
            )
        )
        if segment_app_source != current_soperator_app:
            raise ValueError(
                f"{field_label}.segments[{index}].soperator_app.current_version must match "
                "the previous segment target or campaign source"
            )
        if segment_app_target not in {current_soperator_app, campaign_app_target}:
            raise ValueError(
                f"{field_label}.segments[{index}].soperator_app.target_version must stay "
                "unchanged or advance directly to the campaign target"
            )
        if segment_app_upgrade_required:
            soperator_app_transition_count += 1
        current_soperator_app = segment_app_target

        segment_chart_source, segment_chart_target, segment_chart_upgrade_required = (
            _validate_locked_version_record(
                segment.get("soperator_chart"),
                f"{field_label}.segments[{index}].soperator_chart",
            )
        )
        if segment_chart_source != current_soperator_chart:
            raise ValueError(
                f"{field_label}.segments[{index}].soperator_chart.current_version must match "
                "the previous segment target or campaign source"
            )
        if segment_chart_target not in {current_soperator_chart, campaign_chart_target}:
            raise ValueError(
                f"{field_label}.segments[{index}].soperator_chart.target_version must stay "
                "unchanged or advance directly to the campaign target"
            )
        if segment_chart_upgrade_required:
            soperator_chart_transition_count += 1
        current_soperator_chart = segment_chart_target

        expected_soperator_upgrade = bool(
            segment_app_upgrade_required or segment_chart_upgrade_required
        )
        if segment.get("soperator_upgrade_required") is not expected_soperator_upgrade:
            raise ValueError(
                f"{field_label}.segments[{index}].soperator_upgrade_required must be "
                f"{'true' if expected_soperator_upgrade else 'false'} for its app/chart "
                "version transitions"
            )

        segment_jail = segment.get("jail_rootfs")
        if not isinstance(segment_jail, Mapping):
            raise ValueError(f"{field_label}.segments[{index}].jail_rootfs must be a mapping")
        segment_jail_source = _soperator_campaign_jail_source_identity(
            segment_jail,
            f"{field_label}.segments[{index}].jail_rootfs",
        )
        if segment_jail_source != current_jail_source:
            raise ValueError(
                f"{field_label}.segments[{index}].jail_rootfs current image/version must "
                "match the previous segment target or campaign source"
            )
        segment_jail_target = _soperator_campaign_jail_target_identity(
            segment_jail,
            f"{field_label}.segments[{index}].jail_rootfs",
        )
        if segment_jail_target != campaign_jail_target:
            raise ValueError(
                f"{field_label}.segments[{index}].jail_rootfs target image/CUDA identity "
                "must match the campaign final target"
            )
        if segment_jail.get("refresh_required") is True:
            jail_refresh_segment_count += 1
            current_jail_source = campaign_jail_target[:2]
    if current_control_plane != target_k8s:
        raise ValueError(
            f"{field_label}.segments must form a contiguous control-plane path from "
            "source_k8s_version to target_k8s_version"
        )
    if current_soperator_app != campaign_app_target:
        raise ValueError(
            f"{field_label}.segments must advance Soperator app from the campaign source "
            "to its final target"
        )
    if current_soperator_chart != campaign_chart_target:
        raise ValueError(
            f"{field_label}.segments must advance Soperator chart from the campaign source "
            "to its final target"
        )
    if soperator_app_transition_count != int(campaign_app_upgrade_required):
        raise ValueError(
            f"{field_label}.segments must contain exactly one Soperator app transition when "
            "the campaign app upgrade is required, and none otherwise"
        )
    if soperator_chart_transition_count != int(campaign_chart_upgrade_required):
        raise ValueError(
            f"{field_label}.segments must contain exactly one Soperator chart transition when "
            "the campaign chart upgrade is required, and none otherwise"
        )
    if jail_refresh_segment_count != int(campaign_jail_refresh_required):
        raise ValueError(
            f"{field_label}.segments must contain exactly one Jail rootfs refresh when the "
            "campaign refresh is required, and none otherwise"
        )
    if current_jail_source != campaign_jail_target[:2]:
        raise ValueError(
            f"{field_label}.segments must advance the Jail rootfs image/version from the "
            "campaign source to its final target"
        )
    if expected_node_group_ids is not None and expected_node_group_ids != set(
        campaign_group_targets
    ):
        raise ValueError(
            f"{field_label}.mk8s.node_groups must contain the same immutable ids as every Kubernetes hop"
        )
    if previous_node_group_targets and previous_node_group_targets != campaign_group_targets:
        raise ValueError(
            f"{field_label}.mk8s.node_groups final targets must match the final Kubernetes hop"
        )
    if expected_node_group_ids is not None:
        first_segment = next(
            (
                segment
                for segment in segments
                if isinstance(segment, Mapping)
                and isinstance(segment.get("mk8s"), Mapping)
                and segment["mk8s"].get("node_groups")
            ),
            None,
        )
        if isinstance(first_segment, Mapping):
            _first_source, _first_target, first_group_sources, _first_group_targets = (
                _validate_soperator_campaign_mk8s_segment(
                    first_segment.get("mk8s"),
                    f"{field_label}.segments[first].mk8s",
                    k8s_upgrade_required=first_segment.get("k8s_upgrade_required") is True,
                )
            )
            if first_group_sources != campaign_group_sources:
                raise ValueError(
                    f"{field_label}.mk8s.node_groups sources must match the first Kubernetes hop"
                )

    expected_fingerprint = soperator_upgrade_campaign_fingerprint(upgrade_path)
    if fingerprint != expected_fingerprint:
        raise ValueError(f"{field_label}.fingerprint does not match the immutable campaign payload")
    expected_campaign_id = f"campaign-{fingerprint[:16]}"
    if campaign_id != expected_campaign_id:
        raise ValueError(
            f"{field_label}.campaign_id must be '{expected_campaign_id}' for the campaign fingerprint"
        )


def _validate_soperator_onboarding(
    onboarding: Mapping[str, Any],
    field_label: str,
    *,
    campaign_project_id: str = "",
    campaign_cluster_id: str = "",
    campaign_target_ref: str = "",
) -> None:
    _validate_unknown_keys(
        onboarding,
        allowed_keys=_SOPERATOR_ONBOARDING_KEYS,
        field_label=field_label,
    )
    accepted = onboarding.get("accepted")
    if accepted is not None and not isinstance(accepted, bool):
        raise ValueError(f"{field_label}.accepted must be true or false")
    support_override_used = onboarding.get("support_override_used")
    if support_override_used is not None and not isinstance(support_override_used, bool):
        raise ValueError(f"{field_label}.support_override_used must be true or false")
    for key in (
        "state",
        "storage_mode",
        "compute_mode",
        "target_version",
        "source_version",
        "migration_profile_id",
        "analysis_fingerprint",
        "support_status",
        "support_rule_id",
        "support_message",
    ):
        _optional_string_for_validation(onboarding.get(key), f"{field_label}.{key}")
    storage_mode = _as_text(onboarding.get("storage_mode"))
    if storage_mode and storage_mode not in ONBOARDING_STORAGE_MODES:
        raise ValueError(
            f"{field_label}.storage_mode must be one of: "
            + ", ".join(sorted(ONBOARDING_STORAGE_MODES))
        )
    compute_mode = _as_text(onboarding.get("compute_mode"))
    if compute_mode and compute_mode not in ONBOARDING_COMPUTE_MODES:
        raise ValueError(
            f"{field_label}.compute_mode must be one of: "
            + ", ".join(sorted(ONBOARDING_COMPUTE_MODES))
        )
    collection_errors = onboarding.get("collection_errors")
    if collection_errors is not None and not isinstance(collection_errors, list):
        raise ValueError(f"{field_label}.collection_errors must be a list")
    if "actions" not in onboarding:
        raise ValueError(f"{field_label}.actions is required")
    _validate_soperator_onboarding_actions(onboarding.get("actions"), f"{field_label}.actions")
    _validate_soperator_onboarding_node_template(
        onboarding.get("node_template_upgrade"),
        f"{field_label}.node_template_upgrade",
    )
    _validate_soperator_upgrade_campaign(
        onboarding.get("upgrade_path"),
        f"{field_label}.upgrade_path",
        expected_project_id=campaign_project_id,
        expected_cluster_id=campaign_cluster_id,
        expected_target_ref=campaign_target_ref,
    )


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


def _private_ipv4_network(value: Any) -> ipaddress.IPv4Network | None:
    try:
        network = ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError:
        return None
    if network.version != 4:
        return None
    return network


def _planned_vpc_network_private_cidr_entries(
    *,
    component_index: int,
    inputs: Mapping[str, Any],
) -> list[tuple[str, str, ipaddress.IPv4Network]]:
    network_value = inputs.get("network")
    if not isinstance(network_value, Mapping):
        return []
    raw_cidrs = network_value.get("ipv4_private_cidrs")
    if not isinstance(raw_cidrs, list):
        return []
    entries: list[tuple[str, str, ipaddress.IPv4Network]] = []
    for cidr_index, raw_cidr in enumerate(raw_cidrs):
        cidr = _as_text(raw_cidr)
        if not cidr:
            continue
        field_label = (
            f"infra.components[{component_index}].inputs.network.ipv4_private_cidrs[{cidr_index}]"
        )
        network = _private_ipv4_network(cidr)
        if network is None:
            raise ValueError(f"{field_label} must be an IPv4 CIDR")
        entries.append((field_label, cidr, network))
    return entries


def _planned_vpc_private_cidr_entries(
    *,
    component_index: int,
    inputs: Mapping[str, Any],
) -> list[tuple[str, str, ipaddress.IPv4Network]]:
    subnets = inputs.get("subnets")
    if not isinstance(subnets, Mapping):
        return []
    entries: list[tuple[str, str, ipaddress.IPv4Network]] = []
    for subnet_key, raw_subnet in subnets.items():
        if not isinstance(raw_subnet, Mapping):
            raise ValueError(
                f"infra.components[{component_index}].inputs.subnets.{subnet_key} must be a mapping"
            )
        if raw_subnet.get("use_network_private_pools") is True:
            raise ValueError(
                f"infra.components[{component_index}].inputs.subnets.{subnet_key}."
                "use_network_private_pools must be false; VPC subnets created by "
                "cxcli require explicit private CIDRs"
            )
        raw_cidrs = raw_subnet.get("ipv4_private_cidrs")
        if not isinstance(raw_cidrs, list):
            raise ValueError(
                f"infra.components[{component_index}].inputs.subnets.{subnet_key}."
                "ipv4_private_cidrs is required for VPC subnets"
            )
        subnet_has_cidr = False
        for cidr_index, raw_cidr in enumerate(raw_cidrs):
            cidr = _as_text(raw_cidr)
            if not cidr:
                continue
            subnet_has_cidr = True
            field_label = (
                f"infra.components[{component_index}].inputs.subnets.{subnet_key}."
                f"ipv4_private_cidrs[{cidr_index}]"
            )
            network = _private_ipv4_network(cidr)
            if network is None:
                raise ValueError(f"{field_label} must be an IPv4 CIDR")
            entries.append((field_label, cidr, network))
        if not subnet_has_cidr:
            raise ValueError(
                f"infra.components[{component_index}].inputs.subnets.{subnet_key}."
                "ipv4_private_cidrs must contain at least one IPv4 CIDR"
            )
    return entries


def _validate_vpc_private_cidr_entries_do_not_overlap(
    entries: list[tuple[str, str, ipaddress.IPv4Network]],
) -> None:
    seen_networks: list[tuple[str, str, ipaddress.IPv4Network]] = []
    for field_label, cidr, network in entries:
        for seen_label, seen_cidr, seen_network in seen_networks:
            if network.overlaps(seen_network):
                raise ValueError(
                    f"{field_label} overlaps {seen_label} CIDR {seen_cidr}; "
                    "Nebius requires subnet CIDR blocks in the same VPC network "
                    "to be non-overlapping"
                )
        seen_networks.append((field_label, cidr, network))


def _validate_planned_vpc_private_cidr_overlaps(
    *,
    component_index: int,
    inputs: Mapping[str, Any],
) -> list[tuple[str, str, ipaddress.IPv4Network]]:
    entries = _planned_vpc_private_cidr_entries(
        component_index=component_index,
        inputs=inputs,
    )
    _validate_vpc_private_cidr_entries_do_not_overlap(entries)
    return entries


def _planned_vpc_network_pool_ids(inputs: Mapping[str, Any], field_name: str) -> list[str]:
    network_value = inputs.get("network")
    if not isinstance(network_value, Mapping):
        return []
    raw_pool_ids = network_value.get(field_name)
    if not isinstance(raw_pool_ids, list):
        return []
    return [_as_text(pool_id) for pool_id in raw_pool_ids if _as_text(pool_id)]


def _planned_vpc_network_private_pool_ids(inputs: Mapping[str, Any]) -> list[str]:
    return _planned_vpc_network_pool_ids(inputs, "ipv4_private_pool_ids")


def _planned_vpc_network_private_source_pool_id(inputs: Mapping[str, Any]) -> str:
    network_value = inputs.get("network")
    if not isinstance(network_value, Mapping):
        return ""
    return _as_text(network_value.get("ipv4_private_source_pool_id"))


def _validate_planned_vpc_private_cidr_contract(
    *,
    component_index: int,
    inputs: Mapping[str, Any],
) -> list[tuple[str, str, ipaddress.IPv4Network]]:
    network_entries = _planned_vpc_network_private_cidr_entries(
        component_index=component_index,
        inputs=inputs,
    )
    _validate_vpc_private_cidr_entries_do_not_overlap(network_entries)
    network_value = inputs.get("network")
    existing_network_id = (
        _as_text(network_value.get("existing_id")) if isinstance(network_value, Mapping) else ""
    )
    source_pool_id = _planned_vpc_network_private_source_pool_id(inputs)
    private_pool_ids = _planned_vpc_network_private_pool_ids(inputs)
    if existing_network_id:
        if (
            network_entries
            or private_pool_ids
            or source_pool_id
            or _planned_vpc_network_pool_ids(inputs, "ipv4_public_pool_ids")
        ):
            raise ValueError(
                f"infra.components[{component_index}].inputs.network private CIDRs, "
                "source pool, or pool IDs cannot be set when network.existing_id is set; "
                "existing networks already own their pools"
            )
        subnet_entries = _validate_planned_vpc_private_cidr_overlaps(
            component_index=component_index,
            inputs=inputs,
        )
        return subnet_entries

    if source_pool_id and not network_entries:
        raise ValueError(
            f"infra.components[{component_index}].inputs.network.ipv4_private_source_pool_id "
            "applies only when network.ipv4_private_cidrs creates managed private pools"
        )

    if not network_entries and not private_pool_ids:
        raise ValueError(
            f"infra.components[{component_index}].inputs.network.ipv4_private_cidrs "
            "is required when creating a new VPC network unless "
            "network.ipv4_private_pool_ids is set"
        )

    subnet_entries = _validate_planned_vpc_private_cidr_overlaps(
        component_index=component_index,
        inputs=inputs,
    )
    if network_entries and not private_pool_ids:
        for subnet_label, subnet_cidr, subnet_network in subnet_entries:
            if any(subnet_network.subnet_of(network) for _label, _cidr, network in network_entries):
                continue
            network_ranges = ", ".join(cidr for _label, cidr, _network in network_entries)
            raise ValueError(
                f"{subnet_label} CIDR {subnet_cidr} must fit inside the VPC network "
                f"private CIDR range ({network_ranges})"
            )
    return subnet_entries


def _is_scalar_resource_name_value(value: Any) -> bool:
    return value is not None and not isinstance(value, (bool, Mapping, list, tuple, set))


def _is_complex_type_hint(type_hint: Any) -> bool:
    normalized = _as_text(type_hint).lower()
    return normalized.startswith(("list(", "set(", "map(", "object(", "tuple("))


def _entry_scalar_resource_name_input(entry: Any) -> str:
    if entry is None or entry.status is None:
        return ""
    name_input = _as_text(entry.status.name_input) or "name"
    wizard_fields = getattr(entry, "wizard_fields", {}) or {}
    for candidate in (name_input, f"inputs.{name_input}"):
        field = wizard_fields.get(candidate)
        if not isinstance(field, Mapping):
            continue
        if bool(field.get("prompt_complex")) or _is_complex_type_hint(field.get("type_hint")):
            return ""
    return name_input


def _validate_client_info(payload: Mapping[str, Any]) -> None:
    client_info = payload.get("client_info")
    if not isinstance(client_info, Mapping):
        raise ValueError("client_info must be a mapping")

    supported_client_info_keys = {"client_name", "nebius", "notifications"}
    unknown_client_info = sorted(
        str(key) for key in client_info if str(key) not in supported_client_info_keys
    )
    if unknown_client_info:
        raise ValueError("client_info has unsupported field(s): " + ", ".join(unknown_client_info))

    client_name = _as_text(client_info.get("client_name"))
    if not client_name:
        raise ValueError("client_info.client_name is required")
    if not _CLIENT_NAME_PATTERN.fullmatch(client_name):
        raise ValueError("client_info.client_name must use lowercase letters, digits, and hyphens")

    nebius = client_info.get("nebius")
    if not isinstance(nebius, Mapping):
        raise ValueError("client_info.nebius must be a mapping")
    supported_nebius_keys = {"tenant_id", "project_id", "region_id"}
    unknown_nebius = sorted(str(key) for key in nebius if str(key) not in supported_nebius_keys)
    if unknown_nebius:
        raise ValueError(
            "client_info.nebius has unsupported field(s): " + ", ".join(unknown_nebius)
        )
    for field in ("project_id", "region_id"):
        value = _as_text(nebius.get(field))
        if not value:
            raise ValueError(f"client_info.nebius.{field} is required")
    region_id = _as_text(nebius.get("region_id"))
    if region_id not in SUPPORTED_REGION_IDS:
        available = ", ".join(SUPPORTED_REGION_IDS)
        raise ValueError("client_info.nebius.region_id must be one of: " + available)

    notifications = client_info.get("notifications")
    if not isinstance(notifications, Mapping):
        raise ValueError("client_info.notifications must be a mapping")
    supported_notification_keys = {"email_enabled", "email"}
    unknown_notification_keys = sorted(
        str(key) for key in notifications if str(key) not in supported_notification_keys
    )
    if unknown_notification_keys:
        raise ValueError(
            "client_info.notifications has unsupported field(s): "
            + ", ".join(unknown_notification_keys)
        )
    email_enabled = notifications.get("email_enabled")
    if not isinstance(email_enabled, bool):
        raise ValueError("client_info.notifications.email_enabled must be true or false")
    email = notifications.get("email")
    if email is not None and not isinstance(email, str):
        raise ValueError("client_info.notifications.email must be a string or null")


def _validate_deploy(payload: Mapping[str, Any]) -> None:
    deploy = payload.get("deploy")
    if deploy is None:
        return
    if not isinstance(deploy, Mapping):
        raise ValueError("deploy must be a mapping")

    supported_deploy_keys = {"observability", "targets"}
    unknown_deploy_keys = sorted(
        str(key) for key in deploy if str(key) not in supported_deploy_keys
    )
    if unknown_deploy_keys:
        raise ValueError("deploy has unsupported field(s): " + ", ".join(unknown_deploy_keys))

    _validate_observability(
        deploy.get("observability"),
        field_label="deploy.observability",
        allow_kubernetes=False,
    )

    targets = deploy.get("targets")
    if targets is not None:
        if not isinstance(targets, list):
            raise ValueError("deploy.targets must be a list")
        seen_target_refs: set[str] = set()
        for index, raw_target in enumerate(targets):
            if not isinstance(raw_target, Mapping):
                raise ValueError(f"deploy.targets[{index}] must be a mapping")
            kind = _as_text(raw_target.get("kind")).lower()
            base_target_keys = {
                INSTANCE_ID_FIELD,
                "deployment_testing",
                "observability",
                "project_id",
                "region_id",
                "secrets",
                "validations",
            }
            external_target_keys = {
                "access",
                "cluster_id",
                "inventory",
                "kind",
                "kube_context",
                "ownership",
                "soperator_onboarding",
            }
            supported_target_keys = (
                base_target_keys | external_target_keys
                if kind == EXTERNAL_MK8S_TARGET_KIND
                else base_target_keys | {"kind"}
            )
            unknown_target_keys = sorted(
                str(key) for key in raw_target if str(key) not in supported_target_keys
            )
            if unknown_target_keys:
                raise ValueError(
                    f"deploy.targets[{index}] has unsupported field(s): "
                    + ", ".join(unknown_target_keys)
                )
            target_ref = _as_text(raw_target.get(INSTANCE_ID_FIELD)).lower()
            if not target_ref:
                raise ValueError(f"deploy.targets[{index}].{INSTANCE_ID_FIELD} is required")
            if not INSTANCE_ID_PATTERN.fullmatch(target_ref):
                raise ValueError(
                    f"deploy.targets[{index}].{INSTANCE_ID_FIELD} must use lowercase letters, digits, and hyphens"
                )
            if target_ref in seen_target_refs:
                raise ValueError(
                    f"deploy.targets[{index}].{INSTANCE_ID_FIELD} '{target_ref}' is duplicated"
                )
            seen_target_refs.add(target_ref)
            if kind and kind != EXTERNAL_MK8S_TARGET_KIND:
                raise ValueError(
                    f"deploy.targets[{index}].kind must be '{EXTERNAL_MK8S_TARGET_KIND}' when set"
                )
            ownership = _as_text(raw_target.get("ownership")).lower()
            if ownership and ownership != EXTERNAL_TARGET_OWNERSHIP:
                raise ValueError(
                    f"deploy.targets[{index}].ownership must be '{EXTERNAL_TARGET_OWNERSHIP}' when set"
                )
            if deploy_target_is_external_mk8s(raw_target):
                kube_context = _as_text(raw_target.get("kube_context"))
                cluster_id = _as_text(raw_target.get("cluster_id"))
                if not kube_context and not cluster_id:
                    raise ValueError(
                        f"deploy.targets[{index}] external MK8s target requires "
                        "kube_context or cluster_id"
                    )
                access = _as_text(raw_target.get("access")).lower()
                if access and access not in {"external", "internal", "public", "private"}:
                    raise ValueError(
                        f"deploy.targets[{index}].access must be external/internal/public/private"
                    )
                inventory = raw_target.get("inventory")
                if inventory is not None and not isinstance(inventory, Mapping):
                    raise ValueError(f"deploy.targets[{index}].inventory must be a mapping")
                onboarding = raw_target.get("soperator_onboarding")
                if onboarding is not None and not isinstance(onboarding, Mapping):
                    raise ValueError(
                        f"deploy.targets[{index}].soperator_onboarding must be a mapping"
                    )
                if isinstance(onboarding, Mapping):
                    campaign_project_id = _as_text(raw_target.get("project_id")) or _as_text(
                        _mapping_path_value(payload, "client_info.nebius.project_id")
                    )
                    _validate_soperator_onboarding(
                        onboarding,
                        f"deploy.targets[{index}].soperator_onboarding",
                        campaign_project_id=campaign_project_id,
                        campaign_cluster_id=cluster_id,
                        campaign_target_ref=target_ref,
                    )
            _validate_observability(
                raw_target.get("observability"),
                field_label=f"deploy.targets[{index}].observability",
                allow_vm=False,
            )
            _validate_deploy_target_secrets(
                raw_target.get("secrets"),
                field_label=f"deploy.targets[{index}].secrets",
            )
            if "validations" in raw_target:
                raise ValueError(
                    f"deploy.targets[{index}].validations is no longer supported; "
                    "use deploy.targets[].deployment_testing for deploy-time checks and "
                    "acceptance-test commands for heavy validation/benchmarking"
                )
            _validate_deployment_testing(
                raw_target.get("deployment_testing"),
                field_label=f"deploy.targets[{index}].deployment_testing",
            )


def _validate_deploy_target_secrets(secrets: Any, *, field_label: str) -> None:
    if secrets is None:
        return
    if not isinstance(secrets, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    unknown_keys = sorted(str(key) for key in secrets if str(key) not in {"mysterybox"})
    if unknown_keys:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown_keys))
    mysterybox = secrets.get("mysterybox")
    if mysterybox is None:
        return
    if not isinstance(mysterybox, Mapping):
        raise ValueError(f"{field_label}.mysterybox must be a mapping")
    supported_keys = {
        "enabled",
        "store_name",
        "api_domain",
        "credentials_secret",
        "allow_all_namespaces",
        "sync_namespaces",
        "refresh_interval",
    }
    unknown_mysterybox_keys = sorted(
        str(key) for key in mysterybox if str(key) not in supported_keys
    )
    if unknown_mysterybox_keys:
        raise ValueError(
            f"{field_label}.mysterybox has unsupported field(s): "
            + ", ".join(unknown_mysterybox_keys)
        )
    enabled = mysterybox.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError(f"{field_label}.mysterybox.enabled must be true or false")
    if enabled is not True:
        return

    allow_all_namespaces = mysterybox.get("allow_all_namespaces")
    if allow_all_namespaces is not None and not isinstance(allow_all_namespaces, bool):
        raise ValueError(f"{field_label}.mysterybox.allow_all_namespaces must be true or false")

    for key in ("store_name", "api_domain"):
        value = _as_text(mysterybox.get(key))
        if not value:
            raise ValueError(f"{field_label}.mysterybox.{key} is required when enabled")

    refresh_interval = _as_text(mysterybox.get("refresh_interval"))
    if refresh_interval and not _REFRESH_INTERVAL_PATTERN.fullmatch(refresh_interval):
        raise ValueError(
            f"{field_label}.mysterybox.refresh_interval must use s, m, or h units "
            "(for example 30s, 15m, or 1h)"
        )

    _validate_mysterybox_credentials_secret(
        mysterybox.get("credentials_secret"),
        field_label=f"{field_label}.mysterybox.credentials_secret",
    )

    sync_namespaces = mysterybox.get("sync_namespaces")
    if not isinstance(sync_namespaces, list) or not sync_namespaces:
        raise ValueError(
            f"{field_label}.mysterybox.sync_namespaces must be a non-empty list of strings"
        )
    for index, namespace in enumerate(sync_namespaces):
        if not isinstance(namespace, str) or not _ID_PATTERN.fullmatch(namespace):
            raise ValueError(
                f"{field_label}.mysterybox.sync_namespaces[{index}] must be a Kubernetes namespace name"
            )


def _validate_deployment_testing(raw: Any, *, field_label: str) -> None:
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    unknown_keys = sorted(str(key) for key in raw if str(key) not in {"mk8s_gpu", "soperator"})
    if unknown_keys:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown_keys))

    mk8s_gpu = raw.get("mk8s_gpu")
    if mk8s_gpu is not None:
        if not isinstance(mk8s_gpu, Mapping):
            raise ValueError(f"{field_label}.mk8s_gpu must be a mapping")
        supported_mk8s_gpu = {"operator_readiness", "gpu_visibility", "health_checker"}
        unknown_mk8s_gpu = sorted(
            str(key) for key in mk8s_gpu if str(key) not in supported_mk8s_gpu
        )
        if unknown_mk8s_gpu:
            hint = ""
            if "cuda_smoke" in unknown_mk8s_gpu:
                hint = "; use gpu_visibility for the bounded deploy probe"
            if "nccl" in unknown_mk8s_gpu:
                hint = "; use acceptance-test benchmark for NCCL"
            raise ValueError(
                f"{field_label}.mk8s_gpu has unsupported field(s): "
                + ", ".join(unknown_mk8s_gpu)
                + hint
            )
        for section_name in ("operator_readiness", "gpu_visibility", "health_checker"):
            section = mk8s_gpu.get(section_name)
            if section is None:
                continue
            if not isinstance(section, Mapping):
                raise ValueError(f"{field_label}.mk8s_gpu.{section_name} must be a mapping")
            supported_section_keys = {"enabled"}
            if section_name == "gpu_visibility":
                supported_section_keys.add("max_nodes")
            unknown_section = sorted(
                str(key) for key in section if str(key) not in supported_section_keys
            )
            if unknown_section:
                raise ValueError(
                    f"{field_label}.mk8s_gpu.{section_name} has unsupported field(s): "
                    + ", ".join(unknown_section)
                )
            enabled = section.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                raise ValueError(
                    f"{field_label}.mk8s_gpu.{section_name}.enabled must be true or false when set"
                )
            max_nodes = section.get("max_nodes")
            if max_nodes is not None and _coerce_int(max_nodes, default=0) <= 0:
                raise ValueError(f"{field_label}.mk8s_gpu.{section_name}.max_nodes must be > 0")

    soperator = raw.get("soperator")
    if soperator is not None:
        if not isinstance(soperator, Mapping):
            raise ValueError(f"{field_label}.soperator must be a mapping")
        unknown_soperator = sorted(str(key) for key in soperator if str(key) not in {"smoke"})
        if unknown_soperator:
            raise ValueError(
                f"{field_label}.soperator has unsupported field(s): " + ", ".join(unknown_soperator)
            )
        smoke = soperator.get("smoke")
        if smoke is not None:
            if not isinstance(smoke, Mapping):
                raise ValueError(f"{field_label}.soperator.smoke must be a mapping")
            unknown_smoke = sorted(str(key) for key in smoke if str(key) not in {"enabled"})
            if unknown_smoke:
                raise ValueError(
                    f"{field_label}.soperator.smoke has unsupported field(s): "
                    + ", ".join(unknown_smoke)
                )
            enabled = smoke.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                raise ValueError(
                    f"{field_label}.soperator.smoke.enabled must be true or false when set"
                )


def _validate_mysterybox_credentials_secret(value: Any, *, field_label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"name", "namespace", "key"}
    unknown_keys = sorted(str(key) for key in value if str(key) not in supported_keys)
    if unknown_keys:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown_keys))
    for key in ("name", "namespace", "key"):
        current = _as_text(value.get(key))
        if not current:
            raise ValueError(f"{field_label}.{key} is required")
        if key != "key" and not _ID_PATTERN.fullmatch(current):
            raise ValueError(f"{field_label}.{key} must be a Kubernetes name")


def _validate_observability(
    observability: Any,
    *,
    field_label: str,
    allow_vm: bool = True,
    allow_kubernetes: bool = True,
) -> None:
    if observability is None:
        return
    if not isinstance(observability, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"enabled"}
    if allow_kubernetes:
        supported_keys.add("kubernetes")
    if allow_vm:
        supported_keys.add("vm")
    unknown_keys = sorted(str(key) for key in observability if str(key) not in supported_keys)
    if unknown_keys:
        raise ValueError(f"{field_label} has unsupported field(s): " + ", ".join(unknown_keys))
    enabled = observability.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError(f"{field_label}.enabled must be true or false")

    kubernetes = observability.get("kubernetes")
    if kubernetes is not None:
        if not isinstance(kubernetes, Mapping):
            raise ValueError(f"{field_label}.kubernetes must be a mapping")
        supported_kubernetes_keys = {"logs", "metrics", "traces"}
        unknown_kubernetes_keys = sorted(
            str(key) for key in kubernetes if str(key) not in supported_kubernetes_keys
        )
        if unknown_kubernetes_keys:
            raise ValueError(
                f"{field_label}.kubernetes has unsupported field(s): "
                + ", ".join(unknown_kubernetes_keys)
            )

        logs = kubernetes.get("logs")
        if logs is not None:
            if not isinstance(logs, Mapping):
                raise ValueError(f"{field_label}.kubernetes.logs must be a mapping")
            supported_log_keys = {"enabled", "collect_agent_logs", "excluded_namespaces"}
            unknown_log_keys = sorted(
                str(key) for key in logs if str(key) not in supported_log_keys
            )
            if unknown_log_keys:
                raise ValueError(
                    f"{field_label}.kubernetes.logs has unsupported field(s): "
                    + ", ".join(unknown_log_keys)
                )
            for field in ("enabled", "collect_agent_logs"):
                value = logs.get(field)
                if value is not None and not isinstance(value, bool):
                    raise ValueError(f"{field_label}.kubernetes.logs.{field} must be true or false")
            excluded_namespaces = logs.get("excluded_namespaces")
            if excluded_namespaces is not None and (
                not isinstance(excluded_namespaces, list)
                or any(not isinstance(item, str) for item in excluded_namespaces)
            ):
                raise ValueError(
                    f"{field_label}.kubernetes.logs.excluded_namespaces must be a list of strings"
                )

        metrics = kubernetes.get("metrics")
        if metrics is not None:
            if not isinstance(metrics, Mapping):
                raise ValueError(f"{field_label}.kubernetes.metrics must be a mapping")
            supported_metric_keys = {
                "enabled",
                "collect_agent_metrics",
                "collect_k8s_cluster_metrics",
                "excluded_namespaces",
            }
            unknown_metric_keys = sorted(
                str(key) for key in metrics if str(key) not in supported_metric_keys
            )
            if unknown_metric_keys:
                raise ValueError(
                    f"{field_label}.kubernetes.metrics has unsupported field(s): "
                    + ", ".join(unknown_metric_keys)
                )
            for field in ("enabled", "collect_agent_metrics", "collect_k8s_cluster_metrics"):
                value = metrics.get(field)
                if value is not None and not isinstance(value, bool):
                    raise ValueError(
                        f"{field_label}.kubernetes.metrics.{field} must be true or false"
                    )
            excluded_namespaces = metrics.get("excluded_namespaces")
            if excluded_namespaces is not None and (
                not isinstance(excluded_namespaces, list)
                or any(not isinstance(item, str) for item in excluded_namespaces)
            ):
                raise ValueError(
                    f"{field_label}.kubernetes.metrics.excluded_namespaces must be a list of strings"
                )

        traces = kubernetes.get("traces")
        if traces is not None:
            if not isinstance(traces, Mapping):
                raise ValueError(f"{field_label}.kubernetes.traces must be a mapping")
            supported_trace_keys = {"enabled"}
            unknown_trace_keys = sorted(
                str(key) for key in traces if str(key) not in supported_trace_keys
            )
            if unknown_trace_keys:
                raise ValueError(
                    f"{field_label}.kubernetes.traces has unsupported field(s): "
                    + ", ".join(unknown_trace_keys)
                )
            value = traces.get("enabled")
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{field_label}.kubernetes.traces.enabled must be true or false")

    vm = observability.get("vm")
    if vm is None:
        return
    if not isinstance(vm, Mapping):
        raise ValueError(f"{field_label}.vm must be a mapping")
    supported_vm_keys = {"logs"}
    unknown_vm_keys = sorted(str(key) for key in vm if str(key) not in supported_vm_keys)
    if unknown_vm_keys:
        raise ValueError(
            f"{field_label}.vm has unsupported field(s): " + ", ".join(unknown_vm_keys)
        )

    logs = vm.get("logs")
    if logs is None:
        return
    if not isinstance(logs, Mapping):
        raise ValueError(f"{field_label}.vm.logs must be a mapping")
    supported_vm_log_keys = {"enabled", "systemd_units"}
    unknown_vm_log_keys = sorted(str(key) for key in logs if str(key) not in supported_vm_log_keys)
    if unknown_vm_log_keys:
        raise ValueError(
            f"{field_label}.vm.logs has unsupported field(s): " + ", ".join(unknown_vm_log_keys)
        )
    enabled = logs.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError(f"{field_label}.vm.logs.enabled must be true or false")
    systemd_units = logs.get("systemd_units")
    if systemd_units is not None and (
        not isinstance(systemd_units, list)
        or any(not isinstance(item, str) for item in systemd_units)
    ):
        raise ValueError(f"{field_label}.vm.logs.systemd_units must be a list of strings")


def _enabled_component_ids(payload: Mapping[str, Any], *, scope: ComponentScope) -> set[str]:
    selected: set[str] = set()
    if scope == "infra":
        infra = payload.get("infra")
        if not isinstance(infra, Mapping):
            return selected
        components = infra.get("components")
        if not isinstance(components, list):
            return selected
        for item in components:
            if not isinstance(item, Mapping):
                continue
            if not bool(item.get("enabled", False)):
                continue
            component_id = _as_text(item.get("id")).lower()
            if component_id:
                selected.add(component_id)
        return selected

    apps = payload.get("apps")
    if not isinstance(apps, Mapping):
        return selected
    charts = apps.get("charts")
    if not isinstance(charts, list):
        return selected
    for item in charts:
        if not isinstance(item, Mapping):
            continue
        if not bool(item.get("enabled", False)):
            continue
        chart_id = _as_text(item.get("id")).lower()
        if chart_id:
            selected.add(chart_id)
    return selected


def _expected_app_group(config_path: str) -> str | None:
    parts = config_path.split(".")
    if len(parts) < 3:
        return None
    if parts[0] != "apps":
        return None
    return parts[1]


def _component_config_path_label(
    *,
    scope: ComponentScope,
    component_id: str,
    instance_id: str,
    target_path: str,
) -> str:
    collection = "components" if scope == "infra" else "charts"
    selector = f"id={component_id}"
    if instance_id and instance_id != component_id:
        selector = f"{selector},instance_id={instance_id}"
    return f"{scope}.{collection}[{selector}].{target_path}"


def _validate_materialized_shared_defaults(payload: Mapping[str, Any]) -> None:
    scopes: tuple[tuple[ComponentScope, str, str], ...] = (
        ("infra", "infra", "components"),
        ("apps", "apps", "charts"),
    )
    for scope, section_name, collection_name in scopes:
        section = payload.get(section_name)
        if not isinstance(section, Mapping):
            continue
        rows = section.get(collection_name)
        if not isinstance(rows, list):
            continue
        entry_by_id = {entry.id: entry for entry in component_entries(scope)}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if not bool(row.get("enabled", False)):
                continue
            component_id = component_type_id(row)
            if not component_id:
                continue
            entry = entry_by_id.get(component_id)
            if entry is None:
                continue
            instance_id = component_instance_id(row)
            if not instance_id:
                continue
            for target_path in sorted(shared_default_target_paths(entry)):
                value = read_component_path(row, target_path)
                if component_path_has_material_value(value):
                    continue
                raise ValueError(
                    f"{_component_config_path_label(scope=scope, component_id=component_id, instance_id=instance_id, target_path=target_path)} "
                    "is required; shared-derived defaults must be materialized into config.yaml during create/component add"
                )


def validate_dynamic_payload_structure(payload: Mapping[str, Any]) -> None:
    """Validate dynamic model sections (`infra.components[]`, `apps.charts[]`)."""
    infra = payload.get("infra")
    apps = payload.get("apps")
    if not isinstance(infra, Mapping) or not isinstance(apps, Mapping):
        return

    infra_components = infra.get("components")
    apps_charts = apps.get("charts")
    if infra_components is None and apps_charts is None:
        return

    if not isinstance(infra_components, list):
        raise ValueError("infra.components must be a list in dynamic config mode")
    if not isinstance(apps_charts, list):
        raise ValueError("apps.charts must be a list in dynamic config mode")

    app_lookup = component_lookup("apps")
    infra_lookup = component_lookup("infra")
    seen_infra_instance_ids: set[str] = set()
    seen_infra_resource_names: dict[tuple[str, str], int] = {}
    cluster_target_refs: set[str] = set()
    enabled_vm_instance_ids: set[str] = set()
    enabled_infra_rows_by_selector: dict[tuple[str, str], Mapping[str, Any]] = {}
    enabled_infra_instances_by_id: dict[str, list[str]] = {}
    row_bindings_to_validate: list[tuple[int, str, str, Any]] = []
    default_project_id = _as_text(_mapping_path_value(payload, "client_info.nebius.project_id"))
    existing_network_private_cidrs: dict[
        tuple[str, str], list[tuple[str, str, ipaddress.IPv4Network]]
    ] = {}
    for index, raw_component in enumerate(infra_components):
        if not isinstance(raw_component, Mapping):
            raise ValueError(f"infra.components[{index}] must be a mapping")
        unknown_keys = sorted(
            str(key)
            for key in raw_component
            if str(key)
            not in {"id", "instance_id", "enabled", "source", "version", "inputs", "bindings"}
        )
        if unknown_keys:
            raise ValueError(
                f"infra.components[{index}] has unsupported field(s): {', '.join(unknown_keys)}"
            )

        component_id = component_type_id(raw_component)
        if not component_id:
            raise ValueError(f"infra.components[{index}].id is required")
        if not _ID_PATTERN.fullmatch(component_id):
            raise ValueError(
                f"infra.components[{index}].id must use lowercase letters, digits, and hyphens"
            )
        raw_instance_id = _as_text(raw_component.get("instance_id")).lower()
        if not raw_instance_id:
            raise ValueError(f"infra.components[{index}].instance_id is required")
        if not INSTANCE_ID_PATTERN.fullmatch(raw_instance_id):
            raise ValueError(
                f"infra.components[{index}].instance_id must use lowercase letters, digits, and hyphens"
            )
        instance_id = raw_instance_id
        if instance_id in seen_infra_instance_ids:
            raise ValueError(f"infra.components[{index}].instance_id '{instance_id}' is duplicated")
        seen_infra_instance_ids.add(instance_id)

        if not isinstance(raw_component.get("enabled"), bool):
            raise ValueError(f"infra.components[{index}].enabled must be true or false")
        if bool(raw_component.get("enabled", False)):
            enabled_infra_rows_by_selector[(component_id, instance_id)] = raw_component
            enabled_infra_instances_by_id.setdefault(component_id, []).append(instance_id)
        source_value = raw_component.get("source")
        if source_value is not None and not isinstance(source_value, str):
            raise ValueError(f"infra.components[{index}].source must be a string when set")
        version_value = raw_component.get("version")
        if version_value is not None and not isinstance(version_value, str):
            raise ValueError(f"infra.components[{index}].version must be a string when set")
        inputs = raw_component.get("inputs")
        if not isinstance(inputs, Mapping):
            raise ValueError(f"infra.components[{index}].inputs must be a mapping")
        row_bindings = row_input_bindings(
            raw_component,
            field_label=f"infra.components[{index}]",
        )
        for binding in row_bindings:
            row_bindings_to_validate.append((index, component_id, instance_id, binding))
            existing_value = read_component_path(raw_component, binding.target_path)
            if component_path_has_material_value(existing_value):
                raise ValueError(
                    f"infra.components[{index}].bindings.{binding.target_path} conflicts with "
                    f"literal {binding.target_path}"
                )
        if "module" in inputs:
            raise ValueError(
                f"infra.components[{index}].inputs.module is not supported; "
                "set module source at infra.components[].source and module vars directly under infra.components[].inputs"
            )
        if component_id == "mk8s" and "gpu_validation_overrides" in inputs:
            raise ValueError(
                "infra.components[].inputs.gpu_validation_overrides is no longer supported; "
                "use deploy.targets[].deployment_testing.mk8s_gpu.*"
            )
        if component_id == "vpc" and bool(raw_component.get("enabled", False)):
            cidr_entries = _validate_planned_vpc_private_cidr_contract(
                component_index=index,
                inputs=inputs,
            )
            network = inputs.get("network")
            existing_network_id = (
                _as_text(network.get("existing_id")) if isinstance(network, Mapping) else ""
            )
            if existing_network_id:
                project_id = _as_text(inputs.get("parent_id")) or default_project_id
                existing_network_private_cidrs.setdefault(
                    (project_id, existing_network_id), []
                ).extend(cidr_entries)
        entry = infra_lookup.get(component_id)
        if entry is not None and bool(raw_component.get("enabled", False)):
            name_input = _entry_scalar_resource_name_input(entry)
            if name_input:
                raw_resource_name = _mapping_path_value(inputs, name_input)
                if _is_scalar_resource_name_value(raw_resource_name):
                    normalized_name = normalize_component_token(raw_resource_name)
                    if not normalized_name or not INSTANCE_ID_PATTERN.fullmatch(normalized_name):
                        raise ValueError(
                            f"infra.components[{index}].inputs.{name_input} must normalize to a valid "
                            "instance_id using lowercase letters, digits, and hyphens"
                        )
                    name_key = (component_id, normalized_name)
                    existing_index = seen_infra_resource_names.get(name_key)
                    if existing_index is not None:
                        raise ValueError(
                            f"infra.components[{index}].inputs.{name_input} '{normalized_name}' "
                            f"duplicates infra.components[{existing_index}].inputs.{name_input}"
                        )
                    seen_infra_resource_names[name_key] = index
                    if instance_id != normalized_name:
                        raise ValueError(
                            f"infra.components[{index}].instance_id '{instance_id}' must match "
                            f"normalized inputs.{name_input} '{normalized_name}'"
                        )
        if (
            entry is not None
            and entry.handoff is not None
            and bool(raw_component.get("enabled", False))
        ):
            cluster_target_refs.add(instance_id)
        if component_id == "vm" and bool(raw_component.get("enabled", False)):
            enabled_vm_instance_ids.add(instance_id)

    for cidr_entries in existing_network_private_cidrs.values():
        _validate_vpc_private_cidr_entries_do_not_overlap(cidr_entries)

    for index, _component_id, _instance_id, binding in row_bindings_to_validate:
        source_entry = infra_lookup.get(binding.source_component_id)
        if source_entry is None:
            raise ValueError(
                f"infra.components[{index}].bindings.{binding.target_path} references "
                f"unknown infra component '{binding.source_component_id}'"
            )
        if binding.source_output_name not in {output.name for output in source_entry.outputs}:
            raise ValueError(
                f"infra.components[{index}].bindings.{binding.target_path} references "
                f"undeclared output '{binding.source_component_id}.{binding.source_output_name}'"
            )
        if binding.source_instance_id:
            source_row = enabled_infra_rows_by_selector.get(
                (binding.source_component_id, binding.source_instance_id)
            )
            if source_row is None:
                raise ValueError(
                    f"infra.components[{index}].bindings.{binding.target_path} references "
                    f"disabled or missing infra:{binding.source_component_id}@{binding.source_instance_id}"
                )
        else:
            source_instances = enabled_infra_instances_by_id.get(binding.source_component_id, [])
            if len(source_instances) != 1:
                raise ValueError(
                    f"infra.components[{index}].bindings.{binding.target_path} must set "
                    "source_instance when the source component is absent or not unique"
                )
            source_row = enabled_infra_rows_by_selector[
                (binding.source_component_id, source_instances[0])
            ]
        if binding.source_component_id == "vpc" and binding.source_output_name == "subnets":
            subnets = read_component_path(source_row, "inputs.subnets")
            if not binding.key or not isinstance(subnets, Mapping) or binding.key not in subnets:
                raise ValueError(
                    f"infra.components[{index}].bindings.{binding.target_path} references "
                    f"missing VPC subnet key '{binding.key or '<empty>'}'"
                )
            if binding.attribute != "id":
                raise ValueError(
                    f"infra.components[{index}].bindings.{binding.target_path} VPC subnet "
                    "bindings must use attribute 'id'"
                )

    deploy = payload.get("deploy")
    deploy_targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
    if isinstance(deploy_targets, list):
        for index, raw_target in enumerate(deploy_targets):
            if not isinstance(raw_target, Mapping):
                continue
            target_ref = _as_text(raw_target.get(INSTANCE_ID_FIELD)).lower()
            if deploy_target_is_external_mk8s(raw_target) and target_ref:
                cluster_target_refs.add(target_ref)
            if target_ref and cluster_target_refs and target_ref not in cluster_target_refs:
                available = ", ".join(sorted(cluster_target_refs)) or "(none)"
                raise ValueError(
                    f"deploy.targets[{index}].{INSTANCE_ID_FIELD} must reference one of the enabled cluster targets: {available}"
                )
        if deploy_targets and not cluster_target_refs:
            raise ValueError(
                "deploy.targets requires at least one enabled cluster target or external MK8s target"
            )
    root_observability = deploy.get("observability") if isinstance(deploy, Mapping) else None
    if root_observability is not None and not enabled_vm_instance_ids:
        raise ValueError(
            "deploy.observability is only supported for enabled infra:vm components; "
            "use deploy.targets[].observability for MK8s targets"
        )

    has_enabled_app_charts = any(
        isinstance(raw_chart, Mapping) and bool(raw_chart.get("enabled", False))
        for raw_chart in apps_charts
    )
    if has_enabled_app_charts and not cluster_target_refs:
        raise ValueError(
            "apps.charts requires at least one enabled MK8s target because cxcli apps "
            "are Helm charts installed into Kubernetes. Add an enabled infra:mk8s "
            "component in the same config or remove/disable apps.charts."
        )

    seen_app_instance_keys: set[tuple[str, str]] = set()
    for index, raw_chart in enumerate(apps_charts):
        if not isinstance(raw_chart, Mapping):
            raise ValueError(f"apps.charts[{index}] must be a mapping")
        unknown_keys = sorted(
            str(key)
            for key in raw_chart
            if str(key)
            not in {
                "id",
                "instance_id",
                "group",
                "enabled",
                "install_mode",
                "placements",
                "repo",
                "profile",
                "version",
                "namespace",
                "release-name",
                "values",
            }
        )
        if unknown_keys:
            raise ValueError(
                f"apps.charts[{index}] has unsupported field(s): {', '.join(unknown_keys)}"
            )

        chart_id = component_type_id(raw_chart)
        if not chart_id:
            raise ValueError(f"apps.charts[{index}].id is required")
        if not _ID_PATTERN.fullmatch(chart_id):
            raise ValueError(
                f"apps.charts[{index}].id must use lowercase letters, digits, and hyphens"
            )
        raw_instance_id = _as_text(raw_chart.get("instance_id")).lower()
        if not raw_instance_id:
            raise ValueError(f"apps.charts[{index}].instance_id is required")
        if not INSTANCE_ID_PATTERN.fullmatch(raw_instance_id):
            raise ValueError(
                f"apps.charts[{index}].instance_id must use lowercase letters, digits, and hyphens"
            )
        instance_id = raw_instance_id
        instance_key = (chart_id, instance_id)
        if instance_key in seen_app_instance_keys:
            raise ValueError(
                f"apps.charts[{index}] duplicates chart '{chart_id}' instance_id '{instance_id}'"
            )
        seen_app_instance_keys.add(instance_key)

        entry = app_lookup.get(chart_id)

        group = _as_text(raw_chart.get("group")).lower()
        if group and not _SECTION_PATTERN.fullmatch(group):
            raise ValueError(
                f"apps.charts[{index}].group must use lowercase letters, digits, and hyphens"
            )
        expected_group = _expected_app_group(entry.config_path) if entry else None
        if group and expected_group and group != expected_group:
            raise ValueError(
                f"apps.charts[{index}].group must be '{expected_group}' for chart '{chart_id}'"
            )

        if not isinstance(raw_chart.get("enabled"), bool):
            raise ValueError(f"apps.charts[{index}].enabled must be true or false")
        if chart_id in _FOLDED_SOPERATOR_CHILD_APP_IDS:
            raise ValueError(
                f"apps.charts[{index}].id '{chart_id}' is no longer a standalone app. "
                f"Enable values.{chart_id}.enabled under the "
                "apps:soperator row instead."
            )
        if chart_id in _FOLDED_SOPERATOR_DEPENDENCY_APP_IDS:
            raise ValueError(
                f"apps.charts[{index}].id '{chart_id}' is no longer a standalone app. "
                "Enable values.soperator-backup-config.enabled under the apps:soperator "
                "row instead; k8up is installed as that child chart dependency."
            )
        install_mode = _as_text(raw_chart.get("install_mode"))
        if install_mode and chart_id != "soperator":
            raise ValueError(
                f"apps.charts[{index}].install_mode is only supported for chart 'soperator'"
            )
        placements = raw_chart.get("placements")
        if placements is not None:
            if chart_id != "soperator":
                raise ValueError(
                    f"apps.charts[{index}].placements is only supported for chart 'soperator'"
                )
            if not isinstance(placements, Mapping):
                raise ValueError(f"apps.charts[{index}].placements must be a mapping")
            for raw_placement, raw_groups in placements.items():
                placement = _as_text(raw_placement)
                if not placement:
                    raise ValueError(
                        f"apps.charts[{index}].placements entries must have non-empty names"
                    )
                if not _ID_PATTERN.fullmatch(placement):
                    raise ValueError(
                        f"apps.charts[{index}].placements.{placement} must use lowercase letters, digits, and hyphens"
                    )
                if isinstance(raw_groups, str):
                    if not raw_groups.strip():
                        raise ValueError(
                            f"apps.charts[{index}].placements.{placement} must not be empty"
                        )
                elif isinstance(raw_groups, list):
                    if not raw_groups or not all(
                        isinstance(item, str) and item.strip() for item in raw_groups
                    ):
                        raise ValueError(
                            f"apps.charts[{index}].placements.{placement} must be a non-empty string or list of non-empty strings"
                        )
                else:
                    raise ValueError(
                        f"apps.charts[{index}].placements.{placement} must be a non-empty string or list of non-empty strings"
                    )
        if chart_id == "soperator" and install_mode == "onboard-existing-cluster":
            validate_soperator_onboarding_acceptance(payload, target_ref=instance_id)
        for key in ("repo", "profile", "version", "namespace"):
            value = raw_chart.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"apps.charts[{index}].{key} must be a string when set")
        release_name = raw_chart.get("release-name")
        if release_name is not None and not isinstance(release_name, str):
            raise ValueError(f"apps.charts[{index}].release-name must be a string when set")
        if not isinstance(raw_chart.get("values"), Mapping):
            raise ValueError(f"apps.charts[{index}].values must be a mapping")
        values = raw_chart.get("values")
        if chart_id == "soperator" and isinstance(values, Mapping) and "nodeGroupMapping" in values:
            raise ValueError(
                f"apps.charts[{index}].values.nodeGroupMapping is no longer supported; "
                "use apps.charts[].placements instead"
            )
        if (
            bool(raw_chart.get("enabled", False))
            and cluster_target_refs
            and instance_id not in cluster_target_refs
        ):
            available = ", ".join(sorted(cluster_target_refs))
            raise ValueError(
                f"apps.charts[{index}].instance_id must reference one of the enabled cluster targets: {available}"
            )


def validate_runtime_payload(payload: Mapping[str, Any]) -> None:
    """Validate config payload with runtime checks."""
    if not isinstance(payload, Mapping):
        raise ValueError("config.yaml root must be a mapping")

    unknown_root = sorted(key for key in payload if key not in _ROOT_KEYS)
    if unknown_root:
        raise ValueError(f"unknown field(s) at root: {', '.join(unknown_root)}")

    if _as_text(payload.get("version")) not in {"", "v1"}:
        raise ValueError("version must be 'v1'")

    _validate_client_info(payload)
    _validate_deploy(payload)

    infra = payload.get("infra")
    if isinstance(infra, Mapping):
        legacy_shared_paths = [key for key in ("ssh_user_name", "ssh_public_key") if key in infra]
        if legacy_shared_paths:
            raise ValueError(
                "infra.ssh_user_name and infra.ssh_public_key are no longer root infra fields. "
                "Set ssh_user_name/ssh_public_key on the selected jump-host component inputs instead "
                "(for example infra.components[id=wireguard-gw].inputs.ssh_public_key). "
                "component_sources.yaml shared.admin_ssh.user_name remains available as a "
                "catalog-level seed that create/component add materialize into jump-host "
                "component inputs."
            )

    selected_by_scope: dict[ComponentScope, set[str]] = {
        "infra": _enabled_component_ids(payload, scope="infra"),
        "apps": _enabled_component_ids(payload, scope="apps"),
    }
    for scope in ("infra", "apps"):
        typed_scope: ComponentScope = scope
        lookup = {entry.id: entry for entry in component_entries(typed_scope)}
        for entry_id in sorted(selected_by_scope[typed_scope]):
            entry = lookup.get(entry_id)
            if entry is None:
                continue
            # Apps dependencies are resolved from Helm Chart.yaml at runtime.
            dependency_refs = entry.depends_on if typed_scope == "infra" else ()
            for raw_ref in dependency_refs:
                dep_scope, dep_id = parse_dependency_ref(raw_ref, default_scope=typed_scope)
                if dep_id not in selected_by_scope[dep_scope]:
                    raise ValueError(
                        f"component dependency '{typed_scope}:{entry_id}' requires "
                        f"'{dep_scope}:{dep_id}' to be enabled"
                    )
    gpu_issues = mk8s_gpu_dependency_issues(payload)
    if gpu_issues:
        raise ValueError(gpu_issues[0])
    observability_issues = observability_dependency_issues(payload)
    if observability_issues:
        raise ValueError(observability_issues[0])
    mysterybox_issues = mysterybox_eso_dependency_issues(payload)
    if mysterybox_issues:
        raise ValueError(mysterybox_issues[0])

    _validate_materialized_shared_defaults(payload)
    validate_soperator_qos_partition_profiles(payload, _as_text)

    run_runtime_validation_plugins(
        payload=payload,
        get_path=_get_path,
        as_text=_as_text,
        id_pattern=_ID_PATTERN,
        env_var_pattern=_ENV_VAR_PATTERN,
    )
