"""Runtime validation for config payloads."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from typing import Any

from .cluster_config import cluster_rows, validate_source_contract
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
from .filesystem_mount_tags import validate_filesystem_mount_tags
from .mk8s_gpu import mk8s_gpu_dependency_issues
from .mysterybox_eso import mysterybox_eso_dependency_issues
from .observability import observability_dependency_issues
from .regions import SUPPORTED_REGION_IDS
from .runtime_component_validation import validate_soperator_upstream_values
from .runtime_config import read_path_with_catalog
from .runtime_plugin_validation import run_runtime_validation_plugins
from .soperator_registration import (
    SOPERATOR_REGISTRATION_SCHEMA,
    SOPERATOR_REGISTRATION_STATE_SUPPORTED,
    validate_soperator_registration,
)
from .soperator_release import SOPERATOR_UPSTREAM_REPOSITORY
from .soperator_values import (
    EXPLICIT_VALUES_FIELD,
    explicit_values,
    validate_feature_values,
    validate_input_values,
)
from .soperator_worker_defaults import WORKER_DEFAULTS_FIELD, validate_worker_defaults

_ROOT_KEYS = frozenset({"version", "client_info", "infra", "apps", "compatibility"})
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
_SOPERATOR_REGISTRATION_KEYS = frozenset(
    {
        "accepted",
        "analysis_fingerprint",
        "collection_errors",
        "live_manifest_sha256",
        "owned_object_graph_sha256",
        "protected_storage_receipt_sha256",
        "provenance_method",
        "rendered_manifest_sha256",
        "schema",
        "source_archive_sha256",
        "source_capability_sha256",
        "source_commit",
        "source_contract",
        "source_manifest_sha256",
        "source_repository",
        "source_tag",
        "source_tree",
        "source_version",
        "state",
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


def _validate_soperator_registration(
    registration: Mapping[str, Any],
    field_label: str,
) -> None:
    _validate_unknown_keys(
        registration,
        allowed_keys=_SOPERATOR_REGISTRATION_KEYS,
        field_label=field_label,
    )
    schema = _as_text(registration.get("schema"))
    if schema != SOPERATOR_REGISTRATION_SCHEMA:
        raise ValueError(f"{field_label}.schema is unsupported")
    if registration.get("accepted") is not True:
        raise ValueError(f"{field_label}.accepted must be true")
    state = _as_text(registration.get("state"))
    if state != SOPERATOR_REGISTRATION_STATE_SUPPORTED:
        raise ValueError(f"{field_label}.state must be '{SOPERATOR_REGISTRATION_STATE_SUPPORTED}'")
    for key in (
        "analysis_fingerprint",
        "schema",
        "source_archive_sha256",
        "source_capability_sha256",
        "source_commit",
        "source_contract",
        "source_manifest_sha256",
        "source_repository",
        "source_tag",
        "source_tree",
        "source_version",
        "state",
    ):
        value = _as_text(registration.get(key))
        if not value:
            raise ValueError(f"{field_label}.{key} is required")
    source_version = _as_text(registration.get("source_version"))
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", source_version):
        raise ValueError(f"{field_label}.source_version must be an exact stable X.Y.Z release")
    if _as_text(registration.get("source_tag")) != source_version:
        raise ValueError(f"{field_label}.source_tag must equal source_version")
    if _as_text(registration.get("source_repository")) != SOPERATOR_UPSTREAM_REPOSITORY:
        raise ValueError(f"{field_label}.source_repository must be the official upstream")
    if _as_text(registration.get("source_contract")) not in {
        "protected-data-plane-v1",
        "upstream-flux-v1",
    }:
        raise ValueError(f"{field_label}.source_contract is not supported")
    for key in ("source_commit", "source_tree"):
        if not re.fullmatch(r"[0-9a-f]{40}", _as_text(registration.get(key))):
            raise ValueError(f"{field_label}.{key} must be an exact Git object identity")
    for key in (
        "live_manifest_sha256",
        "owned_object_graph_sha256",
        "protected_storage_receipt_sha256",
        "rendered_manifest_sha256",
        "source_archive_sha256",
        "source_capability_sha256",
        "source_manifest_sha256",
    ):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", _as_text(registration.get(key))):
            raise ValueError(f"{field_label}.{key} must be an exact SHA-256")
    if _as_text(registration.get("provenance_method")) not in {
        "official-render-equivalence",
        "official-oci-digest-and-render-equivalence",
    }:
        raise ValueError(f"{field_label}.provenance_method is not supported")
    if not re.fullmatch(
        r"[0-9a-f]{64}",
        _as_text(registration.get("analysis_fingerprint")),
    ):
        raise ValueError(f"{field_label}.analysis_fingerprint must be a SHA-256 hex digest")
    collection_errors = registration.get("collection_errors")
    if not isinstance(collection_errors, list):
        raise ValueError(f"{field_label}.collection_errors must be a list")
    if collection_errors:
        raise ValueError(f"{field_label}.collection_errors must be empty")


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


def _validate_cluster_policies(payload: Mapping[str, Any], *, materialized: bool = False) -> None:
    for index, raw_target in enumerate(cluster_rows(payload, enabled_only=False)):
        label = f"cluster[{raw_target.get('instance_id', index)}]"
        owner_active = raw_target.get("id") != "mk8s" or raw_target.get("enabled") is True
        for field in ("project_id", "region_id"):
            if field not in raw_target:
                continue
            value = raw_target[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label}.{field} must be a non-empty string")
            if raw_target.get("id") == "mk8s":
                inputs = raw_target.get("inputs", {})
                input_key = "parent_id" if field == "project_id" else "region_id"
                effective = _as_text(inputs.get(input_key)) or _as_text(
                    _mapping_path_value(payload, f"client_info.nebius.{field}")
                )
                if effective and value.strip() != effective:
                    raise ValueError(f"{label}.{field} conflicts with its infrastructure identity")
        if raw_target.get("id") != "mk8s":
            kube_context = _as_text(raw_target.get("kube_context"))
            cluster_id = _as_text(raw_target.get("cluster_id"))
            if not kube_context and not cluster_id:
                raise ValueError(
                    f"infra.external_clusters[{index}] external MK8s target requires "
                    "kube_context or cluster_id"
                )
            access = _as_text(raw_target.get("access")).lower()
            if access and access not in {"external", "internal", "public", "private"}:
                raise ValueError(
                    f"infra.external_clusters[{index}].access must be external/internal/public/private"
                )
            inventory = raw_target.get("inventory")
            if inventory is not None and not isinstance(inventory, Mapping):
                raise ValueError(f"infra.external_clusters[{index}].inventory must be a mapping")
            desired_platform = raw_target.get("desired_platform")
            if desired_platform is not None:
                if not isinstance(desired_platform, Mapping) or set(desired_platform) != {
                    "kubernetes_version",
                    "node_groups",
                }:
                    raise ValueError("desired_platform requires kubernetes_version and node_groups")
                if not re.fullmatch(r"[0-9]+\.[0-9]+", str(desired_platform["kubernetes_version"])):
                    raise ValueError("desired_platform requires an exact Kubernetes minor version")
                groups = desired_platform["node_groups"]
                if not isinstance(groups, Mapping) or any(
                    not isinstance(row, Mapping)
                    or set(row) != {"os", "gpu_stack_preset"}
                    or not isinstance(row["os"], str)
                    or not row["os"]
                    or not isinstance(row["gpu_stack_preset"], str)
                    for row in groups.values()
                ):
                    raise ValueError("desired_platform requires exact per-group templates")
        _validate_observability(
            raw_target.get("observability"),
            field_label=f"{label}.observability",
            allow_vm=False,
            allow_routing=True,
        )
        if "routing" in (raw_target.get("observability") or {}):
            from .observability_routing import _validate_saved_settings, resolve_settings

            _validate_saved_settings(raw_target["observability"]["routing"])
            if owner_active:
                resolve_settings(payload, str(raw_target.get(INSTANCE_ID_FIELD, "")))
        _validate_cluster_secrets(
            raw_target.get("secrets"),
            field_label=f"{label}.secrets",
            materialized=materialized and owner_active,
        )
        testing = raw_target.get("deployment_testing")
        if isinstance(testing, Mapping) and set(testing) - {"mk8s_gpu"}:
            raise ValueError(
                f"{label}.deployment_testing supports only mk8s_gpu; Soperator checks belong on its app"
            )
        _validate_deployment_testing(testing, field_label=f"{label}.deployment_testing")
    for row in payload.get("infra", {}).get("components", []):
        if row.get("id") == "vm":
            _validate_observability(
                row.get("observability"),
                field_label=f"vm[{row.get('instance_id')}].observability",
                allow_kubernetes=False,
            )


def _validate_soperator_policy(row: Mapping[str, Any], label: str) -> None:
    rollout = row.get("rollout")
    if rollout is not None:
        if not isinstance(rollout, Mapping) or set(rollout) != {
            "strategy",
            "max_surge_count",
            "drain_timeout",
        }:
            raise ValueError("rollout requires strategy, max_surge_count and drain_timeout")
        if rollout["strategy"] not in {"zero-surge", "safe-surge", "force-delete"}:
            raise ValueError("rollout has an unsupported strategy")
        count = rollout["max_surge_count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("rollout max_surge_count must be a nonnegative integer")
        if not isinstance(rollout["drain_timeout"], str) or not rollout["drain_timeout"]:
            raise ValueError("rollout drain_timeout must be explicit")
    from .nsight_profiling import validate_settings

    validate_settings(row.get("profiling"))
    if "registration" in row:
        if not isinstance(row["registration"], Mapping):
            raise ValueError(f"{label}.registration must be a mapping")
        _validate_soperator_registration(row["registration"], f"{label}.registration")
    _validate_deployment_testing({"soperator": row.get("deployment_testing")}, field_label=label)


def _validate_cluster_secrets(secrets: Any, *, field_label: str, materialized: bool = True) -> None:
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
        if not materialized and key not in mysterybox:
            continue
        value = _as_text(mysterybox.get(key))
        if not value:
            raise ValueError(f"{field_label}.mysterybox.{key} is required when enabled")

    refresh_interval = _as_text(mysterybox.get("refresh_interval"))
    if refresh_interval and not _REFRESH_INTERVAL_PATTERN.fullmatch(refresh_interval):
        raise ValueError(
            f"{field_label}.mysterybox.refresh_interval must use s, m, or h units "
            "(for example 30s, 15m, or 1h)"
        )

    if materialized or "credentials_secret" in mysterybox:
        _validate_mysterybox_credentials_secret(
            mysterybox.get("credentials_secret"),
            field_label=f"{field_label}.mysterybox.credentials_secret",
        )

    if not materialized and "sync_namespaces" not in mysterybox:
        return
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
    allow_routing: bool = False,
) -> None:
    if observability is None:
        return
    if not isinstance(observability, Mapping):
        raise ValueError(f"{field_label} must be a mapping")
    supported_keys = {"enabled"}
    if allow_routing:
        supported_keys.add("routing")
        if "routing" in observability and not isinstance(observability["routing"], Mapping):
            raise ValueError(f"{field_label}.routing must be a mapping")
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
    validate_source_contract(payload)
    infra = payload.get("infra")
    apps = payload.get("apps")
    if not isinstance(infra, Mapping) or not isinstance(apps, Mapping):
        return

    _validate_unknown_keys(
        infra, allowed_keys={"components", "external_clusters"}, field_label="infra"
    )
    _validate_unknown_keys(apps, allowed_keys={"charts"}, field_label="apps")

    infra_components = infra.get("components")
    apps_charts = apps.get("charts")
    if infra_components is None and apps_charts is None:
        return

    if not isinstance(infra_components, list):
        raise ValueError("infra.components must be a list in dynamic config mode")
    if not isinstance(apps_charts, list):
        raise ValueError("apps.charts must be a list in dynamic config mode")

    from .nat_gateway import validate_nat_config

    app_lookup = component_lookup("apps")
    infra_lookup = component_lookup("infra")
    seen_infra_instance_ids: set[str] = set()
    seen_infra_resource_names: dict[tuple[str, str], int] = {}
    cluster_target_refs: set[str] = set()
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
            not in (
                {"id", "instance_id", "enabled", "source", "version", "inputs", "bindings"}
                | ({"routing"} if raw_component.get("id") == "nat-gateway" else set())
                | ({"access"} if raw_component.get("id") == "wireguard-gw" else set())
                | ({"observability"} if raw_component.get("id") in {"mk8s", "vm"} else set())
                | (
                    {"secrets", "deployment_testing", "project_id", "region_id"}
                    if raw_component.get("id") == "mk8s"
                    else set()
                )
            )
        )
        if unknown_keys:
            raise ValueError(
                f"infra.components[{index}] has unsupported field(s): {', '.join(unknown_keys)}"
            )

        component_id = component_type_id(raw_component)
        if component_id == "wireguard-gw":
            from .wireguard_access import validate_access

            validate_access(raw_component)
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
            if raw_component.get("access", {}).get("lifecycle") != "retained":
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
                "use cluster settings[].deployment_testing.mk8s_gpu.*"
            )
        if component_id == "sfs" and bool(raw_component.get("enabled", False)):
            validate_filesystem_mount_tags(inputs, label=f"infra.components[{index}].inputs")
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

    validate_nat_config(payload)
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

    external_cluster_target_refs: set[str] = set()
    external = infra.get("external_clusters", [])
    if not isinstance(external, list):
        raise ValueError("infra.external_clusters must be a list")
    seen_provider_ids: set[str] = set()
    for index, row in enumerate(external):
        label = f"infra.external_clusters[{index}]"
        if not isinstance(row, Mapping):
            raise ValueError(f"{label} must be a mapping")
        allowed = {
            "instance_id",
            "cluster_id",
            "kube_context",
            "access",
            "inventory",
            "project_id",
            "region_id",
            "observability",
            "secrets",
            "deployment_testing",
            "desired_platform",
        }
        if set(row) - allowed:
            raise ValueError(
                f"{label} has unsupported fields: {', '.join(sorted(set(row) - allowed))}"
            )
        ref = row.get("instance_id")
        if not isinstance(ref, str) or not INSTANCE_ID_PATTERN.fullmatch(ref):
            raise ValueError(f"{label}.instance_id must be a canonical cluster identity")
        if ref in seen_infra_instance_ids or ref in external_cluster_target_refs:
            raise ValueError(f"Cluster identity {ref!r} is duplicated")
        for field in ("cluster_id", "kube_context", "access", "project_id", "region_id"):
            if field in row and (not isinstance(row[field], str) or not row[field].strip()):
                raise ValueError(f"{label}.{field} must be a non-empty string")
        provider_id = row.get("cluster_id")
        if provider_id and provider_id in seen_provider_ids:
            raise ValueError(f"{label}.cluster_id is duplicated")
        if provider_id:
            seen_provider_ids.add(provider_id)
        external_cluster_target_refs.add(ref)
        cluster_target_refs.add(ref)
    _validate_cluster_policies(payload)

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
                "placements",
                "repo",
                "profile",
                "version",
                "namespace",
                "release-name",
                "values",
                "dashboard_imports",
                EXPLICIT_VALUES_FIELD,
                WORKER_DEFAULTS_FIELD,
                "deployment_testing",
                "rollout",
                "profiling",
                "registration",
            }
        )
        if unknown_keys:
            raise ValueError(
                f"apps.charts[{index}] has unsupported field(s): {', '.join(unknown_keys)}"
            )

        chart_id = component_type_id(raw_chart)
        policy_fields = {"deployment_testing", "rollout", "profiling", "registration"} & set(
            raw_chart
        )
        if policy_fields:
            if chart_id != "soperator":
                raise ValueError("Application lifecycle policy is reserved for Soperator")
            _validate_soperator_policy(raw_chart, f"apps.charts[{index}]")
        if "dashboard_imports" in raw_chart:
            from .grafana_dashboards import validate_import_declarations
            from .observability import _grafana_app_id

            validate_import_declarations(raw_chart, grafana_id=_grafana_app_id())
        if WORKER_DEFAULTS_FIELD in raw_chart:
            if chart_id != "soperator":
                raise ValueError("worker-defaults is reserved for Soperator")
            validate_worker_defaults(raw_chart)
        if EXPLICIT_VALUES_FIELD in raw_chart:
            if chart_id != "soperator":
                raise ValueError("values-explicit-paths is reserved for Soperator")
            validate_input_values(explicit_values(raw_chart))
        if chart_id == "soperator" and raw_chart.get("enabled"):
            validate_feature_values(raw_chart.get("values", {}))
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
        if chart_id == "soperator" and instance_id in external_cluster_target_refs:
            validate_soperator_registration(payload, target_ref=instance_id)


def validate_runtime_payload(payload: Mapping[str, Any]) -> None:
    """Validate config payload with runtime checks."""
    if not isinstance(payload, Mapping):
        raise ValueError("config.yaml root must be a mapping")

    unknown_root = sorted(key for key in payload if key not in _ROOT_KEYS)
    if unknown_root:
        raise ValueError(f"unknown field(s) at root: {', '.join(unknown_root)}")

    validate_source_contract(payload)

    _validate_client_info(payload)
    _validate_cluster_policies(payload, materialized=True)
    for index, row in enumerate(payload.get("apps", {}).get("charts", [])):
        if isinstance(row, Mapping) and row.get("id") == "soperator":
            _validate_soperator_policy(row, f"apps.charts[{index}]")
    from .compatibility_matrix import validate_selection

    validate_selection(payload)

    infra = payload.get("infra")
    if isinstance(infra, Mapping):
        legacy_shared_paths = [key for key in ("ssh_user_name", "ssh_public_key") if key in infra]
        if legacy_shared_paths:
            raise ValueError(
                "infra.ssh_user_name and infra.ssh_public_key are no longer root infra fields. "
                "Set ssh_user_name/ssh_public_key on the selected jump-host component inputs instead "
                "(for example infra.components[id=ssh-jumphost].inputs.ssh_public_key). "
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
    from .wireguard_dependencies import access_dependencies

    access_dependencies(payload)
    mysterybox_issues = mysterybox_eso_dependency_issues(payload)
    if mysterybox_issues:
        raise ValueError(mysterybox_issues[0])

    _validate_materialized_shared_defaults(payload)
    validate_soperator_upstream_values(payload)

    run_runtime_validation_plugins(
        payload=payload,
        get_path=_get_path,
        as_text=_as_text,
        id_pattern=_ID_PATTERN,
        env_var_pattern=_ENV_VAR_PATTERN,
    )
