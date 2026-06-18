"""Runtime validation for config payloads."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
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
from .duration_utils import parse_go_duration_seconds
from .mk8s_gpu import mk8s_gpu_dependency_issues
from .mysterybox_eso import mysterybox_eso_dependency_issues
from .observability import observability_dependency_issues
from .runtime_component_validation import validate_soperator_qos_partition_profiles
from .runtime_config import read_path_with_catalog
from .runtime_plugin_validation import run_runtime_validation_plugins
from .soperator_onboarding import validate_soperator_onboarding_acceptance

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
_SOPERATOR_WORKER_ROLLOUT_STRATEGIES = frozenset({"safe-surge", "zero-surge"})


def _get_path(payload: Mapping[str, Any], dotted_path: str, default: Any = None) -> Any:
    resolved = read_path_with_catalog(payload, dotted_path)
    return default if resolved is None else resolved


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _positive_int_for_validation(value: Any, field_label: str) -> None:
    if value is None or value == "":
        return
    if isinstance(value, bool):
        raise ValueError(f"{field_label} must be a positive integer")
    try:
        parsed = int(_as_text(value))
    except ValueError as exc:
        raise ValueError(f"{field_label} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field_label} must be a positive integer")


def _non_negative_int_for_validation(value: Any, field_label: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_label} must be a non-negative integer")
    try:
        parsed = int(_as_text(value))
    except ValueError as exc:
        raise ValueError(f"{field_label} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{field_label} must be a non-negative integer")
    return parsed


def _drain_timeout_for_validation(value: Any, field_label: str) -> None:
    if value is None or value == "":
        return
    raw = _as_text(value).lower()
    if raw == "none":
        return
    if re.fullmatch(r"[0-9]+", raw):
        raise ValueError(
            f"{field_label} must be 'none' or an explicit Go-style duration "
            "(for example 30s, 30m, or 1h)"
        )
    try:
        parse_go_duration_seconds(raw)
    except ValueError as exc:
        raise ValueError(
            f"{field_label} must be 'none' or an explicit Go-style duration "
            "(for example 30s, 30m, or 1h)"
        ) from exc


def _validate_soperator_onboarding_rollout(onboarding: Mapping[str, Any], field_label: str) -> None:
    node_template = onboarding.get("node_template_upgrade")
    if node_template is None:
        return
    if not isinstance(node_template, Mapping):
        raise ValueError(f"{field_label}.node_template_upgrade must be a mapping")
    rollout = node_template.get("rollout")
    if rollout is None:
        return
    if not isinstance(rollout, Mapping):
        raise ValueError(f"{field_label}.node_template_upgrade.rollout must be a mapping")
    strategy = normalize_component_token(rollout.get("strategy")) or "zero-surge"
    if strategy not in _SOPERATOR_WORKER_ROLLOUT_STRATEGIES:
        raise ValueError(
            f"{field_label}.node_template_upgrade.rollout.strategy must be one of: "
            + ", ".join(sorted(_SOPERATOR_WORKER_ROLLOUT_STRATEGIES))
        )
    legacy_keys = (
        "max_global_unavailable_worker_nodes",
        "max_global_unavailable_worker_percent",
    )
    for legacy_key in legacy_keys:
        if rollout.get(legacy_key) is not None and _as_text(rollout.get(legacy_key)) != "":
            raise ValueError(
                f"{field_label}.node_template_upgrade.rollout.{legacy_key} is unsupported; "
                "use worker_wave_groups or worker_wave_percent"
            )
    groups_key = "worker_wave_groups"
    percent_key = "worker_wave_percent"
    groups_present = rollout.get(groups_key) is not None and _as_text(rollout.get(groups_key)) != ""
    percent_present = (
        rollout.get(percent_key) is not None and _as_text(rollout.get(percent_key)) != ""
    )
    if groups_present and percent_present:
        raise ValueError(
            f"{field_label}.node_template_upgrade.rollout must set only one of "
            f"{groups_key} or {percent_key}"
        )
    parallel_present = (
        rollout.get("max_parallel_worker_groups") is not None
        and _as_text(rollout.get("max_parallel_worker_groups")) != ""
    )
    if strategy == "zero-surge":
        zero_surge_wave_fields = []
        if groups_present:
            zero_surge_wave_fields.append(groups_key)
        if percent_present:
            zero_surge_wave_fields.append(percent_key)
        if parallel_present:
            zero_surge_wave_fields.append("max_parallel_worker_groups")
        if zero_surge_wave_fields:
            raise ValueError(
                f"{field_label}.node_template_upgrade.rollout.strategy zero-surge "
                "does not use worker wave budget fields "
                f"({', '.join(zero_surge_wave_fields)}); set strategy to safe-surge "
                "or remove those fields"
            )
    _positive_int_for_validation(
        rollout.get(groups_key),
        f"{field_label}.node_template_upgrade.rollout.{groups_key}",
    )
    _positive_int_for_validation(
        rollout.get(percent_key),
        f"{field_label}.node_template_upgrade.rollout.{percent_key}",
    )
    _positive_int_for_validation(
        rollout.get("max_parallel_worker_groups"),
        f"{field_label}.node_template_upgrade.rollout.max_parallel_worker_groups",
    )
    worker_strategy = rollout.get("worker_group_strategy")
    if worker_strategy is None:
        return
    if not isinstance(worker_strategy, Mapping):
        raise ValueError(
            f"{field_label}.node_template_upgrade.rollout.worker_group_strategy must be a mapping"
        )
    max_surge = _non_negative_int_for_validation(
        worker_strategy.get("max_surge_count"),
        f"{field_label}.node_template_upgrade.rollout.worker_group_strategy.max_surge_count",
    )
    max_unavailable = _non_negative_int_for_validation(
        worker_strategy.get("max_unavailable_count"),
        f"{field_label}.node_template_upgrade.rollout.worker_group_strategy.max_unavailable_count",
    )
    if max_surge == 0 and max_unavailable == 0:
        raise ValueError(
            f"{field_label}.node_template_upgrade.rollout.worker_group_strategy must keep "
            "at least one of max_surge_count or max_unavailable_count greater than zero"
        )
    if strategy == "zero-surge" and max_surge not in {None, 0}:
        raise ValueError(
            f"{field_label}.node_template_upgrade.rollout.worker_group_strategy."
            "max_surge_count must be 0 when strategy is zero-surge"
        )
    if strategy == "safe-surge" and max_surge == 0:
        raise ValueError(
            f"{field_label}.node_template_upgrade.rollout.worker_group_strategy."
            "max_surge_count must be greater than 0 when strategy is safe-surge"
        )
    _drain_timeout_for_validation(
        worker_strategy.get("drain_timeout"),
        f"{field_label}.node_template_upgrade.rollout.worker_group_strategy.drain_timeout",
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
                f"infra.components[{component_index}].inputs.subnets.{subnet_key} "
                "must be a mapping"
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
    for field in ("tenant_id", "project_id", "region_id"):
        value = _as_text(nebius.get(field))
        if not value:
            raise ValueError(f"client_info.nebius.{field} is required")

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
                    _validate_soperator_onboarding_rollout(
                        onboarding,
                        f"deploy.targets[{index}].soperator_onboarding",
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
            target_validations = raw_target.get("validations")
            if target_validations is None:
                continue
            if not isinstance(target_validations, Mapping):
                raise ValueError(f"deploy.targets[{index}].validations must be a mapping")
            unknown_target_validation_keys = sorted(
                str(key) for key in target_validations if str(key) not in {"mk8s_gpu"}
            )
            if unknown_target_validation_keys:
                raise ValueError(
                    f"deploy.targets[{index}].validations has unsupported field(s): "
                    + ", ".join(unknown_target_validation_keys)
                )
            mk8s_gpu = target_validations.get("mk8s_gpu")
            if mk8s_gpu is not None and not isinstance(mk8s_gpu, Mapping):
                raise ValueError(f"deploy.targets[{index}].validations.mk8s_gpu must be a mapping")


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
                "use deploy.targets[].validations.mk8s_gpu.*"
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
                    if not raw_groups or not all(isinstance(item, str) and item.strip() for item in raw_groups):
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
