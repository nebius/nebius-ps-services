"""Component-specific runtime validation adapters.

Validation rules are dispatched by resolved bundled validation profiles.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Mapping
from re import Pattern
from typing import Any

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
            _validate_mysterybox(payload, get_path, as_text, entry.config_path, env_var_pattern)


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
            "deploy.validations.mk8s_gpu.*"
        )

    gpu_enabled = bool(get_path(payload, f"{base}.gpu_enabled", False))
    gpu_node_groups = get_path(payload, f"{base}.gpu_node_groups", 0)
    gpu_nodes_count_per_group = get_path(payload, f"{base}.gpu_nodes_count_per_group", 0)
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
    project_gpu_validations = get_path(payload, "deploy.validations.mk8s_gpu", {})

    if gpu_enabled:
        if _coerce_int(gpu_node_groups) <= 0:
            raise ValueError("gpu_node_groups must be > 0 when gpu_enabled=true")
        if gpu_autoscaling is None and _coerce_int(gpu_nodes_count_per_group) <= 0:
            raise ValueError(
                "gpu_nodes_count_per_group must be > 0 when gpu_enabled=true and autoscaling is not configured"
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
        project_id = as_text(get_path(payload, "client_info.nebius.project_id"))
        if project_id:
            from .provider_options import ProviderOptionLookup

            allow_gpu_clustering = ProviderOptionLookup().compute_platform_preset_allows_gpu_clustering(
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
    if (mig_strategy or mig_parted_config) and not gpu_enabled:
        raise ValueError("mig_strategy/mig_parted_config require gpu_enabled=true")

    if isinstance(project_gpu_validations, Mapping):
        operator_readiness = project_gpu_validations.get("operator_readiness", {})
        gpu_visibility = project_gpu_validations.get("gpu_visibility", {})
        nccl = project_gpu_validations.get("nccl", {})
        health_checker = project_gpu_validations.get("health_checker", {})
        operator_enabled = (
            operator_readiness.get("enabled")
            if isinstance(operator_readiness, Mapping)
            else None
        )

        for field_label, value in (
            ("deploy.validations.mk8s_gpu.operator_readiness.enabled", operator_enabled),
            (
                "deploy.validations.mk8s_gpu.gpu_visibility.enabled",
                gpu_visibility.get("enabled") if isinstance(gpu_visibility, Mapping) else None,
            ),
            (
                "deploy.validations.mk8s_gpu.nccl.enabled",
                nccl.get("enabled") if isinstance(nccl, Mapping) else None,
            ),
            (
                "deploy.validations.mk8s_gpu.health_checker.enabled",
                health_checker.get("enabled") if isinstance(health_checker, Mapping) else None,
            ),
        ):
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{field_label} must be true or false when set")

        gpu_visibility_max_nodes = (
            gpu_visibility.get("max_nodes") if isinstance(gpu_visibility, Mapping) else None
        )
        if gpu_visibility_max_nodes is not None and _coerce_int(gpu_visibility_max_nodes, default=0) <= 0:
            raise ValueError("deploy.validations.mk8s_gpu.gpu_visibility.max_nodes must be > 0")

        nccl_max_nodes = nccl.get("max_nodes") if isinstance(nccl, Mapping) else None
        if nccl_max_nodes is not None and _coerce_int(nccl_max_nodes, default=0) <= 0:
            raise ValueError("deploy.validations.mk8s_gpu.nccl.max_nodes must be > 0")

        nccl_threshold = (
            nccl.get("average_bus_bandwidth_threshold_gbps")
            if isinstance(nccl, Mapping)
            else None
        )
        if nccl_threshold is not None:
            try:
                parsed_threshold = float(nccl_threshold)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "deploy.validations.mk8s_gpu.nccl.average_bus_bandwidth_threshold_gbps must be numeric"
                ) from exc
            if parsed_threshold <= 0:
                raise ValueError(
                    "deploy.validations.mk8s_gpu.nccl.average_bus_bandwidth_threshold_gbps must be > 0"
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
        raise ValueError(f"{base}.public_ip_allocation_id is required when public_ip_mode=allocation")
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
        if not preset.lower().startswith("8gpu-"):
            raise ValueError(f"{base}.gpu_cluster_enabled requires an 8-GPU preset")
        if bool(gpu_cluster_id) == bool(gpu_cluster_fabric):
            raise ValueError(
                f"{base} requires exactly one of gpu_cluster_id or gpu_cluster_infiniband_fabric when gpu_cluster_enabled=true"
            )
        if gpu_cluster_fabric:
            project_id = as_text(get_path(payload, "client_info.nebius.project_id"))
            if project_id:
                from .provider_options import ProviderOptionLookup

                allow_gpu_clustering = ProviderOptionLookup().compute_platform_preset_allows_gpu_clustering(
                    project_id=project_id,
                    platform_name=platform,
                    preset_name=preset,
                )
                if allow_gpu_clustering is False:
                    raise ValueError(
                        "gpu_cluster_infiniband_fabric requires a GPU preset whose live Nebius "
                        f"metadata allows GPU clustering; selected {platform}/{preset} does not "
                        "support GPU clustering"
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
    env_var_pattern: Pattern[str],
) -> None:
    mysterybox_enabled = bool(get_path(payload, f"{base}.enabled", False))
    mysterybox_secrets = get_path(payload, f"{base}.secrets", [])
    if mysterybox_enabled and (not isinstance(mysterybox_secrets, list) or not mysterybox_secrets):
        raise ValueError(f"{base}.enabled=true requires {base}.secrets")

    if isinstance(mysterybox_secrets, list):
        for secret in mysterybox_secrets:
            if not isinstance(secret, Mapping):
                continue
            scope = as_text(secret.get("scope"))
            k8s_sync_enabled = bool(get_path(secret, "k8s_sync.enabled", False))
            if k8s_sync_enabled and scope != "apps":
                raise ValueError(
                    f"{base}.secrets[].k8s_sync.enabled=true requires {base}.secrets[].scope='apps'"
                )
            entries = secret.get("entries", [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                env_name = as_text(entry.get("value_from_env"))
                if env_name and not env_var_pattern.fullmatch(env_name):
                    raise ValueError(
                        f"{base}.secrets[].entries[].value_from_env must be an environment variable name"
                    )

    external_secrets_enabled = bool(
        get_path(payload, "apps.platform.external_secrets.enabled", False)
    )
    external_secrets_mysterybox_enabled = bool(
        get_path(payload, "apps.platform.external_secrets.mysterybox.enabled", False)
    )
    if external_secrets_enabled and external_secrets_mysterybox_enabled:
        if not mysterybox_enabled:
            raise ValueError(
                f"apps.platform.external_secrets.mysterybox.enabled=true requires {base}.enabled=true"
            )
        has_k8s_sync = any(
            bool(get_path(secret, "k8s_sync.enabled", False))
            for secret in mysterybox_secrets
            if isinstance(secret, Mapping)
        )
        if not has_k8s_sync:
            raise ValueError(
                f"apps.platform.external_secrets.mysterybox.enabled=true requires at least one {base}.secrets[].k8s_sync.enabled=true entry"
            )
