"""Component-specific runtime validation adapters.

Validation rules are dispatched by resolved bundled validation profiles.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Mapping
from re import Pattern
from typing import Any

from .component_instances import component_instance_id, component_type_id

_LINUX_USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


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


def _has_generic_gpu_node_group(node_groups: Any) -> bool:
    if not isinstance(node_groups, Mapping):
        return False
    return any(
        isinstance(group, Mapping) and bool(group.get("gpu", False))
        for group in node_groups.values()
    )


def validate_component_runtime_rules(
    payload: Mapping[str, Any],
    *,
    get_path: Callable[[Mapping[str, Any], str, Any], Any],
    as_text: Callable[[Any], str],
    id_pattern: Pattern[str],
    env_var_pattern: Pattern[str],
) -> None:
    ssh_user_name = as_text(get_path(payload, "shared.admin_ssh.user_name"))
    _validate_linux_user_name(
        ssh_user_name,
        field_label="shared.admin_ssh.user_name",
    )

    from .components import component_entries

    for entry in component_entries("infra"):
        base = entry.config_path
        profile = getattr(entry, "validation_profile", "")

        if profile == "postgresql_cluster":
            _validate_postgresql(payload, get_path, as_text, base)
        elif profile == "mk8s_cluster":
            _validate_mk8s_gpu(payload, get_path, as_text, base)
        elif profile == "shared_filesystem":
            _validate_sfs_csi(payload, get_path, as_text, base)
        elif profile == "vm_instance":
            _validate_vm(payload, get_path, as_text, base, id_pattern)
        elif profile == "wireguard_jumphost":
            _validate_wireguard(payload, get_path, as_text, base, id_pattern)
        elif profile == "ssh_jumphost":
            _validate_ssh_jumphost(payload, get_path, as_text, base)

    for entry in component_entries("infra"):
        if getattr(entry, "validation_profile", "") == "mysterybox":
            _validate_mysterybox(
                payload,
                get_path,
                as_text,
                entry.config_path,
                id_pattern,
                component_id=entry.id,
            )

    grafana_component_ids = {
        entry.id
        for entry in component_entries("apps")
        if entry.id == "grafana" or str(entry.chart_name or "").strip().lower() == "grafana"
    }
    _validate_grafana_replicas(payload, grafana_component_ids)


def _validate_postgresql(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
) -> None:
    if bool(get_path(payload, f"{base}.enabled", False)):
        tier = as_text(get_path(payload, f"{base}.tier"))
        if tier and tier not in {"small", "medium", "large"}:
            raise ValueError(f"{base}.tier must be one of: small, medium, large")


def _validate_linux_user_name(value: str, *, field_label: str) -> None:
    if value and not _LINUX_USER_PATTERN.fullmatch(value):
        raise ValueError(
            f"{field_label} must match Linux username format (for example ubuntu, admin_user)"
        )


def _validate_mk8s_gpu(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
) -> None:
    legacy_gpu_validation_overrides = get_path(payload, f"{base}.gpu_validation_overrides")
    if legacy_gpu_validation_overrides is not None:
        raise ValueError(
            f"{base}.gpu_validation_overrides is no longer supported; use "
            "deploy.targets[].validations.mk8s_gpu.*"
        )

    gpu_enabled = bool(get_path(payload, f"{base}.gpu_enabled", False))
    gpu_node_groups = get_path(payload, f"{base}.gpu_node_groups", 0)
    gpu_nodes_count_per_group = get_path(payload, f"{base}.gpu_nodes_count_per_group", 0)
    generic_gpu_node_group = _has_generic_gpu_node_group(get_path(payload, f"{base}.node_groups"))
    gpu_platform = as_text(get_path(payload, f"{base}.gpu_nodes_platform"))
    gpu_preset = as_text(get_path(payload, f"{base}.gpu_nodes_preset"))
    gpu_autoscaling = get_path(
        payload,
        f"{base}.mk8s_gpu_node_group_overrides.autoscaling",
    )
    gpu_override_platform = as_text(
        get_path(
            payload,
            f"{base}.mk8s_gpu_node_group_overrides.template.resources.platform",
        )
    )
    gpu_override_preset = as_text(
        get_path(
            payload,
            f"{base}.mk8s_gpu_node_group_overrides.template.resources.preset",
        )
    )
    infiniband_fabric = as_text(get_path(payload, f"{base}.infiniband_fabric"))
    mig_strategy = as_text(get_path(payload, f"{base}.mig_strategy"))
    mig_parted_config = as_text(get_path(payload, f"{base}.mig_parted_config"))
    project_gpu_validations_by_label: list[tuple[str, Mapping[str, Any]]] = []
    deploy = payload.get("deploy")
    targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
    if isinstance(targets, list):
        for index, raw_target in enumerate(targets):
            if not isinstance(raw_target, Mapping):
                continue
            validations = raw_target.get("validations")
            if not isinstance(validations, Mapping):
                continue
            mk8s_gpu_validations = validations.get("mk8s_gpu")
            if isinstance(mk8s_gpu_validations, Mapping):
                project_gpu_validations_by_label.append(
                    (f"deploy.targets[{index}].validations.mk8s_gpu", mk8s_gpu_validations)
                )

    if gpu_enabled:
        if _coerce_int(gpu_node_groups) <= 0 and not generic_gpu_node_group:
            raise ValueError(
                "gpu_enabled=true requires either gpu_node_groups > 0 or at least one generic node_groups entry with gpu=true"
            )
        if (
            _coerce_int(gpu_node_groups) > 0
            and gpu_autoscaling is None
            and _coerce_int(gpu_nodes_count_per_group) <= 0
        ):
            raise ValueError(
                "gpu_nodes_count_per_group must be > 0 when built-in GPU node-group shortcut is enabled and autoscaling is not configured"
            )
        if not gpu_platform and not gpu_override_platform:
            raise ValueError(
                "gpu_nodes_platform is required when gpu_enabled=true unless mk8s_gpu_node_group_overrides.template.resources.platform is set"
            )
        if not gpu_preset and not gpu_override_preset:
            raise ValueError(
                "gpu_nodes_preset is required when gpu_enabled=true unless mk8s_gpu_node_group_overrides.template.resources.preset is set"
            )

    if infiniband_fabric and not gpu_enabled:
        raise ValueError("infiniband_fabric requires gpu_enabled=true")
    effective_gpu_platform = gpu_override_platform or gpu_platform
    effective_gpu_preset = gpu_override_preset or gpu_preset
    if infiniband_fabric and effective_gpu_platform and effective_gpu_preset:
        tenant_id = as_text(get_path(payload, "client_info.nebius.tenant_id"))
        project_id = as_text(get_path(payload, "client_info.nebius.project_id"))
        region_id = as_text(get_path(payload, "client_info.nebius.region_id"))
        if project_id:
            from .provider_options import ProviderOptionLookup

            lookup = ProviderOptionLookup()
            allow_gpu_clustering = lookup.compute_platform_preset_allows_gpu_clustering(
                project_id=project_id,
                platform_name=effective_gpu_platform,
                preset_name=effective_gpu_preset,
            )
            if allow_gpu_clustering is False:
                raise ValueError(
                    "infiniband_fabric requires a GPU preset whose live Nebius metadata allows "
                    f"GPU clustering; selected {effective_gpu_platform}/{effective_gpu_preset} "
                    "does not support GPU clustering"
                )
            live_fabrics = {
                item.fabric
                for item in lookup.compute_platform_preset_fabrics(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    region_id=region_id,
                    platform_name=effective_gpu_platform,
                    preset_name=effective_gpu_preset,
                )
            }
            if live_fabrics and infiniband_fabric not in live_fabrics:
                allowed = ", ".join(sorted(live_fabrics))
                raise ValueError(
                    "infiniband_fabric must match one of the live Capacity Dashboard fabrics for "
                    f"{effective_gpu_platform}/{effective_gpu_preset} in {region_id}: {allowed}"
                )
    if (mig_strategy or mig_parted_config) and not gpu_enabled:
        raise ValueError("mig_strategy/mig_parted_config require gpu_enabled=true")

    for validation_label, project_gpu_validations in project_gpu_validations_by_label:
        operator_readiness = project_gpu_validations.get("operator_readiness", {})
        gpu_visibility = project_gpu_validations.get("gpu_visibility", {})
        nccl = project_gpu_validations.get("nccl", {})
        health_checker = project_gpu_validations.get("health_checker", {})
        operator_enabled = (
            operator_readiness.get("enabled") if isinstance(operator_readiness, Mapping) else None
        )

        for field_label, value in (
            (f"{validation_label}.operator_readiness.enabled", operator_enabled),
            (
                f"{validation_label}.gpu_visibility.enabled",
                gpu_visibility.get("enabled") if isinstance(gpu_visibility, Mapping) else None,
            ),
            (
                f"{validation_label}.nccl.enabled",
                nccl.get("enabled") if isinstance(nccl, Mapping) else None,
            ),
            (
                f"{validation_label}.health_checker.enabled",
                health_checker.get("enabled") if isinstance(health_checker, Mapping) else None,
            ),
        ):
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{field_label} must be true or false when set")

        gpu_visibility_max_nodes = (
            gpu_visibility.get("max_nodes") if isinstance(gpu_visibility, Mapping) else None
        )
        if (
            gpu_visibility_max_nodes is not None
            and _coerce_int(gpu_visibility_max_nodes, default=0) <= 0
        ):
            raise ValueError(f"{validation_label}.gpu_visibility.max_nodes must be > 0")

        nccl_max_nodes = nccl.get("max_nodes") if isinstance(nccl, Mapping) else None
        if nccl_max_nodes is not None and _coerce_int(nccl_max_nodes, default=0) <= 0:
            raise ValueError(f"{validation_label}.nccl.max_nodes must be > 0")

        nccl_threshold = (
            nccl.get("average_bus_bandwidth_threshold_gbps") if isinstance(nccl, Mapping) else None
        )
        if nccl_threshold is not None:
            try:
                parsed_threshold = float(nccl_threshold)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{validation_label}.nccl.average_bus_bandwidth_threshold_gbps must be numeric"
                ) from exc
            if parsed_threshold <= 0:
                raise ValueError(
                    f"{validation_label}.nccl.average_bus_bandwidth_threshold_gbps must be > 0"
                )


def _validate_sfs_csi(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
) -> None:
    if bool(get_path(payload, f"{base}.csi.enabled", False)):
        mode = as_text(get_path(payload, f"{base}.csi.mode", "dynamic")) or "dynamic"
        pvcs = get_path(payload, f"{base}.csi.pvcs", [])
        if isinstance(pvcs, list):
            seen: set[tuple[str, str]] = set()
            for pvc in pvcs:
                if not isinstance(pvc, Mapping):
                    continue
                namespace = as_text(pvc.get("namespace"))
                name = as_text(pvc.get("name"))
                key = (namespace, name)
                if key in seen:
                    raise ValueError(
                        f"Duplicate PVC definition for namespace/name '{namespace}/{name}'"
                    )
                seen.add(key)

                static_pv_name = pvc.get("static_pv_name", pvc.get("static-pv-name"))
                static_sub_path = pvc.get("static_sub_path", pvc.get("static-sub-path"))
                if mode == "dynamic" and (
                    static_pv_name is not None or static_sub_path is not None
                ):
                    raise ValueError("sfs.csi.pvcs[].static_* fields require sfs.csi.mode='static'")


def _validate_vm(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
    id_pattern: Pattern[str],
) -> None:
    vm_enabled = bool(get_path(payload, f"{base}.enabled", False))
    if not vm_enabled:
        return

    name = as_text(get_path(payload, f"{base}.name"))
    if not name:
        raise ValueError(f"{base}.name is required when enabled=true")
    if not id_pattern.fullmatch(name):
        raise ValueError(f"{base}.name must use lowercase letters, digits, and hyphens")

    ssh_user_name = as_text(get_path(payload, f"{base}.ssh_user_name"))
    _validate_linux_user_name(ssh_user_name, field_label=f"{base}.ssh_user_name")
    if ssh_user_name.lower() in {"root", "admin"}:
        raise ValueError(f"{base}.ssh_user_name must not be root or admin")

    platform = as_text(get_path(payload, f"{base}.platform"))
    preset = as_text(get_path(payload, f"{base}.preset"))
    if not platform:
        raise ValueError(f"{base}.platform is required when enabled=true")
    if not preset:
        raise ValueError(f"{base}.preset is required when enabled=true")

    source_image_family = as_text(get_path(payload, f"{base}.source_image_family"))
    source_image_id = as_text(get_path(payload, f"{base}.source_image_id"))
    boot_disk_existing_id = as_text(get_path(payload, f"{base}.boot_disk_existing_id"))
    if source_image_family and source_image_id:
        raise ValueError(
            f"{base}.source_image_family and {base}.source_image_id are mutually exclusive"
        )
    if boot_disk_existing_id and (source_image_family or source_image_id):
        raise ValueError(
            f"{base}.boot_disk_existing_id cannot be combined with source_image_family or source_image_id"
        )
    if not boot_disk_existing_id and not source_image_id and not source_image_family:
        raise ValueError(
            f"{base}.source_image_family is required when creating a boot disk unless source_image_id or boot_disk_existing_id is set"
        )

    public_ip_mode = as_text(get_path(payload, f"{base}.public_ip_mode", "dynamic")) or "dynamic"
    public_ip_mode = public_ip_mode.lower()
    if public_ip_mode not in {"none", "dynamic", "static", "allocation"}:
        raise ValueError(f"{base}.public_ip_mode must be one of: none, dynamic, static, allocation")
    public_ip_allocation_id = as_text(get_path(payload, f"{base}.public_ip_allocation_id"))
    if public_ip_mode == "allocation" and not public_ip_allocation_id:
        raise ValueError(
            f"{base}.public_ip_allocation_id is required when public_ip_mode=allocation"
        )
    if public_ip_mode != "allocation" and public_ip_allocation_id:
        raise ValueError(
            f"{base}.public_ip_allocation_id can only be used when public_ip_mode=allocation"
        )

    preemptible_enabled = bool(get_path(payload, f"{base}.preemptible_enabled", False))
    recovery_policy = as_text(get_path(payload, f"{base}.recovery_policy", "RECOVER")).upper()
    if preemptible_enabled and not platform.lower().startswith("gpu-"):
        raise ValueError(f"{base}.preemptible_enabled requires a GPU platform")
    if preemptible_enabled and recovery_policy != "FAIL":
        raise ValueError(f"{base}.recovery_policy must be FAIL when preemptible_enabled=true")

    gpu_cluster_enabled = bool(get_path(payload, f"{base}.gpu_cluster_enabled", False))
    gpu_cluster_id = as_text(get_path(payload, f"{base}.gpu_cluster_id"))
    gpu_cluster_fabric = as_text(get_path(payload, f"{base}.gpu_cluster_infiniband_fabric"))
    gpu_cluster_name = as_text(get_path(payload, f"{base}.gpu_cluster_name"))
    if gpu_cluster_enabled:
        if not platform.lower().startswith("gpu-"):
            raise ValueError(f"{base}.gpu_cluster_enabled requires a GPU platform")
        project_id = as_text(get_path(payload, "client_info.nebius.project_id"))
        allow_gpu_clustering = None
        if project_id and platform and preset:
            from .provider_options import ProviderOptionLookup

            allow_gpu_clustering = (
                ProviderOptionLookup().compute_platform_preset_allows_gpu_clustering(
                    project_id=project_id,
                    platform_name=platform,
                    preset_name=preset,
                )
            )
        if allow_gpu_clustering is False:
            raise ValueError(
                f"{base}.gpu_cluster_enabled requires a GPU preset whose live Nebius metadata "
                f"allows GPU clustering; selected {platform}/{preset} does not support GPU clustering"
            )
        if allow_gpu_clustering is None and not preset.lower().startswith("8gpu-"):
            raise ValueError(f"{base}.gpu_cluster_enabled requires a GPU-cluster-compatible preset")
        if bool(gpu_cluster_id) == bool(gpu_cluster_fabric):
            raise ValueError(
                f"{base} requires exactly one of gpu_cluster_id or gpu_cluster_infiniband_fabric when gpu_cluster_enabled=true"
            )
    elif gpu_cluster_id or gpu_cluster_fabric or gpu_cluster_name:
        raise ValueError(f"{base}.gpu_cluster_* fields require gpu_cluster_enabled=true")

    container_enabled = bool(get_path(payload, f"{base}.container_enabled", False))
    container_image = as_text(get_path(payload, f"{base}.container_image"))
    container_use_gpu = bool(get_path(payload, f"{base}.container_use_gpu", False))
    if container_enabled:
        if not container_image:
            raise ValueError(f"{base}.container_image is required when container_enabled=true")
        if preemptible_enabled:
            raise ValueError(f"{base}.container_enabled requires a regular VM")
        if (
            not boot_disk_existing_id
            and not source_image_id
            and source_image_family
            and "ubuntu" not in source_image_family.lower()
        ):
            raise ValueError(
                f"{base}.source_image_family must be Ubuntu-based when container_enabled=true "
                "unless you supply source_image_id or boot_disk_existing_id"
            )
    if container_use_gpu and not container_enabled:
        raise ValueError(f"{base}.container_use_gpu requires container_enabled=true")
    if container_use_gpu and not platform.lower().startswith("gpu-"):
        raise ValueError(f"{base}.container_use_gpu requires a GPU platform")


def _validate_wireguard(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
    id_pattern: Pattern[str],
) -> None:
    wireguard_enabled = bool(get_path(payload, f"{base}.enabled", False))
    if wireguard_enabled:
        ssh_user_name = as_text(get_path(payload, f"{base}.ssh_user_name"))
        _validate_linux_user_name(ssh_user_name, field_label=f"{base}.ssh_user_name")
        wireguard_name = as_text(get_path(payload, f"{base}.name"))
        if not wireguard_name:
            raise ValueError(f"{base}.name is required when enabled=true")
        if not id_pattern.fullmatch(wireguard_name):
            raise ValueError(f"{base}.name must use lowercase letters, digits, and hyphens")
        create_public_ip = bool(get_path(payload, f"{base}.create_public_ip_allocation", True))
        if as_text(get_path(payload, f"{base}.public_ip_allocation_id")) and create_public_ip:
            raise ValueError(
                f"{base}.create_public_ip_allocation must be false "
                "when public_ip_allocation_id is set"
            )

        tunnel_cidr = as_text(get_path(payload, f"{base}.tunnel_cidr", "10.8.0.1/24"))
        try:
            interface = ipaddress.ip_interface(tunnel_cidr)
        except ValueError as exc:
            raise ValueError(
                f"{base}.tunnel_cidr must be a valid IPv4 interface CIDR (example: 10.8.0.1/24)"
            ) from exc
        if interface.version != 4:
            raise ValueError(
                f"{base}.tunnel_cidr must be an IPv4 interface CIDR (example: 10.8.0.1/24)"
            )

        listen_port = get_path(payload, f"{base}.listen_port", 51820)
        try:
            listen_port_int = int(listen_port)
        except Exception as exc:
            raise ValueError(f"{base}.listen_port must be an integer between 1 and 65535") from exc
        if listen_port_int < 1 or listen_port_int > 65535:
            raise ValueError(f"{base}.listen_port must be an integer between 1 and 65535")


def _validate_ssh_jumphost(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
) -> None:
    ssh_jump_enabled = bool(get_path(payload, f"{base}.enabled", False))
    if ssh_jump_enabled:
        ssh_user_name = as_text(get_path(payload, f"{base}.ssh_user_name"))
        _validate_linux_user_name(ssh_user_name, field_label=f"{base}.ssh_user_name")
        allowed_cidrs = get_path(payload, f"{base}.allowed_cidrs", [])
        if not isinstance(allowed_cidrs, list) or not allowed_cidrs:
            raise ValueError(
                f"{base}.allowed_cidrs must contain at least one source CIDR when enabled=true"
            )
        for cidr in allowed_cidrs:
            try:
                network = ipaddress.ip_network(str(cidr), strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"{base}.allowed_cidrs must contain valid CIDRs (for example 203.0.113.10/32)"
                ) from exc
            if network.version != 4:
                raise ValueError(f"{base}.allowed_cidrs currently supports IPv4 CIDRs only")


def _validate_mysterybox(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
    id_pattern: Pattern[str],
    component_id: str,
) -> None:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, Mapping) else None
    rows: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(components, list):
        for row in components:
            if not isinstance(row, Mapping) or component_type_id(row) != component_id:
                continue
            instance_id = component_instance_id(row)
            label = f"infra.components[{instance_id or component_id}]"
            rows.append((label, row))
    if not rows:
        infra = payload.get("infra")
        flat_row = infra.get(component_id) if isinstance(infra, Mapping) else None
        rows.append((base, flat_row if isinstance(flat_row, Mapping) else {}))

    for row_label, row in rows:
        mysterybox_enabled = bool(row.get("enabled", False)) or bool(
            get_path(payload, f"{base}.enabled", False)
        )
        inputs = row.get("inputs") if isinstance(row, Mapping) else None
        if isinstance(inputs, Mapping):
            secrets_path = f"{row_label}.inputs.secrets"
            mysterybox_secrets = inputs.get(
                "secrets",
                get_path(payload, f"{base}.inputs.secrets", []),
            )
        else:
            secrets_path = f"{row_label}.secrets"
            mysterybox_secrets = row.get("secrets", get_path(payload, f"{base}.secrets", []))
        _validate_mysterybox_secrets(
            mysterybox_secrets,
            enabled=mysterybox_enabled,
            secrets_path=secrets_path,
            enabled_label=f"{row_label}.enabled",
            as_text=as_text,
            id_pattern=id_pattern,
        )


def _validate_mysterybox_secrets(
    mysterybox_secrets: Any,
    *,
    enabled: bool,
    secrets_path: str,
    enabled_label: str,
    as_text: Callable,
    id_pattern: Pattern[str],
) -> None:
    if isinstance(mysterybox_secrets, Mapping):
        raise ValueError(f"{secrets_path} must be a list of secret objects")
    if enabled and (not isinstance(mysterybox_secrets, list) or not mysterybox_secrets):
        raise ValueError(f"{enabled_label}=true requires {secrets_path} to be a non-empty list")

    if not isinstance(mysterybox_secrets, list):
        return

    seen_names: set[str] = set()
    for secret_index, secret in enumerate(mysterybox_secrets):
        secret_label = f"{secrets_path}[{secret_index}]"
        if not isinstance(secret, Mapping):
            raise ValueError(f"{secret_label} must be a mapping")
        supported_keys = {
            "name",
            "description",
            "labels",
            "version_id",
            "kubernetes_secret_name",
            "payload",
        }
        unknown_keys = sorted(str(key) for key in secret if str(key) not in supported_keys)
        if unknown_keys:
            raise ValueError(
                f"{secret_label} has unsupported field(s): " + ", ".join(unknown_keys)
            )
        secret_name = as_text(secret.get("name"))
        if not secret_name:
            raise ValueError(f"{secret_label}.name is required")
        if secret_name in seen_names:
            raise ValueError(f"{secrets_path} names must be unique")
        seen_names.add(secret_name)
        kubernetes_secret_name = as_text(secret.get("kubernetes_secret_name"))
        if kubernetes_secret_name and not id_pattern.fullmatch(kubernetes_secret_name):
            raise ValueError(
                f"{secret_label}.kubernetes_secret_name must be a Kubernetes Secret name"
            )
        version_id = as_text(secret.get("version_id"))
        if (
            version_id
            and version_id.lower() != "n/a"
            and not re.fullmatch(r"mbsecver-[a-z0-9]+", version_id)
        ):
            raise ValueError(
                f"{secret_label}.version_id must be empty, n/a, or a MysteryBox "
                "version ID starting with mbsecver-"
            )
        payload = secret.get("payload")
        if not isinstance(payload, Mapping) or not payload:
            raise ValueError(f"{secret_label}.payload must be a non-empty mapping")
        for payload_key, payload_entry in payload.items():
            entry_label = f"{secret_label}.payload.{payload_key}"
            if not as_text(payload_key):
                raise ValueError(f"{secret_label}.payload keys must be non-empty")
            if not isinstance(payload_entry, Mapping):
                raise ValueError(f"{entry_label} must be a mapping")
            unknown_payload_keys = sorted(str(key) for key in payload_entry if str(key) != "type")
            if unknown_payload_keys:
                raise ValueError(
                    f"{entry_label} has unsupported field(s): " + ", ".join(unknown_payload_keys)
                )
            payload_type = (as_text(payload_entry.get("type")) or "text").lower()
            if payload_type not in {"text", "file"}:
                raise ValueError(f"{entry_label}.type must be one of: text, file")


def _validate_grafana_replicas(
    payload: Mapping[str, Any],
    grafana_component_ids: set[str],
) -> None:
    if not grafana_component_ids:
        return
    apps = payload.get("apps")
    charts = apps.get("charts") if isinstance(apps, Mapping) else None
    if not isinstance(charts, list):
        return

    for index, row in enumerate(charts):
        if not isinstance(row, Mapping):
            continue
        component_id = component_type_id(row)
        if component_id not in grafana_component_ids or not bool(row.get("enabled", False)):
            continue
        values = row.get("values")
        if not isinstance(values, Mapping):
            continue
        replicas = _coerce_int(values.get("replicas"), default=1)
        if replicas <= 1 or _grafana_uses_shared_database(values):
            continue
        instance_id = component_instance_id(row)
        label = component_id
        if instance_id and instance_id != component_id:
            label = f"{component_id}@{instance_id}"
        raise ValueError(
            f"apps.charts[{label or index}].values.replicas > 1 requires a shared "
            "Grafana database configured with grafana.ini.database.type set to "
            "mysql, postgres, or postgresql. The bundled default uses per-pod "
            "SQLite/emptyDir storage, so Grafana must stay at one replica."
        )


def _grafana_uses_shared_database(values: Mapping[str, Any]) -> bool:
    allowed_types = {"mysql", "postgres", "postgresql"}
    grafana_ini = values.get("grafana.ini")
    if isinstance(grafana_ini, Mapping):
        database = grafana_ini.get("database")
        if isinstance(database, Mapping):
            database_type = str(database.get("type") or "").strip().lower()
            if database_type in allowed_types:
                return True

    env = values.get("env")
    if isinstance(env, Mapping):
        database_type = str(env.get("GF_DATABASE_TYPE") or "").strip().lower()
        if database_type in allowed_types:
            return True
    return False
