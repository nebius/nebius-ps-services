"""Component-specific runtime validation adapters.

Validation rules are dispatched by resolved bundled validation profiles.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Mapping
from re import Pattern
from typing import Any

from .component_instances import (
    component_instance_id,
    component_type_id,
)
from .mk8s_node_groups import (
    gpu_cluster_fabric,
    gpu_enabled,
    iter_node_groups,
    legacy_mk8s_input_keys,
)

_LINUX_USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_SOPERATOR_APP_ID = "soperator"


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


def _integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value).strip()
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


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
            _validate_mk8s_gpu(payload, get_path, as_text, base, component_id=entry.id)
        elif profile == "shared_filesystem":
            _validate_sfs_csi(payload, get_path, as_text, base)
        elif profile == "vm_instance":
            _validate_vm(payload, get_path, as_text, base, id_pattern)
        elif profile == "wireguard_gw":
            _validate_wireguard(payload, get_path, as_text, base, id_pattern)
        elif profile == "ssh_jumphost":
            _validate_ssh_jumphost(payload, get_path, as_text, base, id_pattern)

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
    validate_soperator_upstream_values(payload)


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


def validate_soperator_upstream_values(payload: Mapping[str, Any]) -> None:
    """Reject values that existed only in the removed downstream chart."""
    apps_node = payload.get("apps")
    chart_rows = apps_node.get("charts") if isinstance(apps_node, Mapping) else None
    if not isinstance(chart_rows, list):
        return

    removed_keys = ("qosConfiguration", "schedulingConfig")
    for index, row in enumerate(chart_rows):
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != _SOPERATOR_APP_ID:
            continue
        values = row.get("values")
        if not isinstance(values, Mapping):
            continue
        for key in removed_keys:
            if key in values:
                raise ValueError(
                    f"apps.charts[{index}].values.{key} is a removed downstream-only "
                    "Soperator field; use values supported by the official "
                    "nebius/soperator release"
                )


def _validate_mk8s_gpu(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
    *,
    component_id: str,
) -> None:
    rows = _mk8s_validation_inputs(payload, get_path, base, component_id)
    for inputs_label, inputs in rows:
        legacy_gpu_validation_overrides = inputs.get("gpu_validation_overrides")
        if legacy_gpu_validation_overrides is not None:
            raise ValueError(
                f"{inputs_label}.gpu_validation_overrides is no longer supported; use "
                "deploy.targets[].deployment_testing.mk8s_gpu.*"
            )
        _validate_mk8s_inputs(
            payload,
            get_path,
            as_text,
            inputs_label,
            inputs,
        )


def _mk8s_validation_inputs(
    payload: Mapping[str, Any],
    get_path: Callable,
    base: str,
    component_id: str,
) -> list[tuple[str, Mapping[str, Any]]]:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, Mapping) else None
    rows: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(components, list):
        for index, row in enumerate(components):
            if not isinstance(row, Mapping) or component_type_id(row) != component_id:
                continue
            if row.get("enabled") is False:
                continue
            inputs = row.get("inputs")
            rows.append(
                (
                    f"infra.components[{index}].inputs",
                    inputs if isinstance(inputs, Mapping) else {},
                )
            )
        return rows

    flat_row = get_path(payload, base, None)
    if not isinstance(flat_row, Mapping) or flat_row.get("enabled") is False:
        return rows
    inputs = flat_row.get("inputs")
    if isinstance(inputs, Mapping):
        return [(f"{base}.inputs", inputs)]
    return [(base, flat_row)]


def _mk8s_mig_configured(inputs: Mapping[str, Any], as_text: Callable) -> bool:
    for key in ("mig_strategy", "mig_parted_config"):
        if as_text(inputs.get(key)):
            return True

    defaults = inputs.get("node_group_defaults")
    if isinstance(defaults, Mapping):
        gpu_defaults = defaults.get("gpu")
        if isinstance(gpu_defaults, Mapping):
            for key in ("mig_strategy", "mig_parted_config"):
                if as_text(gpu_defaults.get(key)):
                    return True

    node_groups = inputs.get("node_groups")
    if isinstance(node_groups, Mapping):
        for raw_group in node_groups.values():
            if not isinstance(raw_group, Mapping):
                continue
            for key in ("mig_strategy", "mig_parted_config"):
                if as_text(raw_group.get(key)):
                    return True
    return False


def _bool_value(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _mk8s_node_group_autoscaling_enabled(group: Mapping[str, Any]) -> bool:
    autoscaling = group.get("autoscaling")
    return isinstance(autoscaling, Mapping) and _bool_value(
        autoscaling.get("enabled"),
        default=True,
    )


def _validate_mk8s_node_group_scale(base: str, key: str, group: Mapping[str, Any]) -> None:
    label = f"{base}.node_groups.{key}"
    autoscaling_enabled = _mk8s_node_group_autoscaling_enabled(group)
    node_count = group.get("node_count")
    if node_count is not None and autoscaling_enabled:
        raise ValueError(f"{label} cannot set both node_count and enabled autoscaling")
    if node_count is None and not autoscaling_enabled:
        raise ValueError(f"{label} requires node_count or enabled autoscaling")
    if node_count is not None:
        parsed = _integer_or_none(node_count)
        if parsed is None or parsed < 0:
            raise ValueError(f"{label}.node_count must be an integer >= 0")
    if not autoscaling_enabled:
        return
    autoscaling = group.get("autoscaling")
    if not isinstance(autoscaling, Mapping):
        raise ValueError(f"{label}.autoscaling must be a mapping when enabled")
    min_raw = autoscaling.get("min_node_count")
    max_raw = autoscaling.get("max_node_count")
    min_count = _integer_or_none(min_raw)
    max_count = _integer_or_none(max_raw)
    if min_count is None or max_count is None or min_count < 0 or max_count < min_count:
        raise ValueError(
            f"{label}.autoscaling requires integer min_node_count >= 0 "
            "and max_node_count >= min_node_count"
        )


def _validate_mk8s_inputs(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
    inputs: Mapping[str, Any],
) -> None:

    legacy_keys = legacy_mk8s_input_keys(inputs)
    if legacy_keys:
        raise ValueError(
            f"{base} uses removed MK8s shortcut input(s): {', '.join(legacy_keys)}. "
            "Use inputs.cluster and inputs.node_groups."
        )
    node_group_defaults = inputs.get("node_group_defaults")
    gpu_defaults = (
        node_group_defaults.get("gpu") if isinstance(node_group_defaults, Mapping) else None
    )
    if isinstance(gpu_defaults, Mapping) and "infiniband_fabric" in gpu_defaults:
        raise ValueError(
            f"{base}.node_group_defaults.gpu.infiniband_fabric is no longer supported. "
            "Use inputs.gpu_clusters.<key>.infiniband_fabric as the single source of "
            "truth and keep GPU node groups pointing at that key with gpu_cluster_key."
        )

    node_groups = iter_node_groups(inputs)
    selected_gpu_enabled = gpu_enabled(inputs)
    gpu_fabric_checks: list[tuple[str, str, str]] = []
    mig_configured = _mk8s_mig_configured(inputs, as_text)
    project_gpu_deployment_testing_by_label: list[tuple[str, Mapping[str, Any]]] = []
    deploy = payload.get("deploy")
    targets = deploy.get("targets") if isinstance(deploy, Mapping) else None
    if isinstance(targets, list):
        for index, raw_target in enumerate(targets):
            if not isinstance(raw_target, Mapping):
                continue
            if "validations" in raw_target:
                raise ValueError(
                    f"deploy.targets[{index}].validations is no longer supported; "
                    "use deploy.targets[].deployment_testing"
                )
            deployment_testing = raw_target.get("deployment_testing")
            if not isinstance(deployment_testing, Mapping):
                continue
            mk8s_gpu_deployment_testing = deployment_testing.get("mk8s_gpu")
            if isinstance(mk8s_gpu_deployment_testing, Mapping):
                project_gpu_deployment_testing_by_label.append(
                    (
                        f"deploy.targets[{index}].deployment_testing.mk8s_gpu",
                        mk8s_gpu_deployment_testing,
                    )
                )

    if not node_groups:
        raise ValueError(f"{base}.node_groups must declare at least one enabled node group")

    raw_node_groups = inputs.get("node_groups")
    if isinstance(raw_node_groups, Mapping):
        for raw_key, raw_group in raw_node_groups.items():
            if not isinstance(raw_group, Mapping) or raw_group.get("enabled") is False:
                continue
            _validate_mk8s_node_group_scale(base, str(raw_key), raw_group)

    if selected_gpu_enabled:
        for group in node_groups:
            if not group.gpu:
                continue
            if not group.platform:
                raise ValueError("GPU node_groups entries require platform")
            if not group.preset:
                raise ValueError("GPU node_groups entries require preset")
            if group.gpu_stack_source not in {"nebius_image", "operator_managed"}:
                raise ValueError(
                    "GPU node_groups entries require gpu_stack_source to be "
                    "'nebius_image' or 'operator_managed' when set"
                )
            if group.reservation_policy not in {"AUTO", "FORBID", "STRICT"}:
                raise ValueError(
                    "GPU node_groups entries require reservation.policy to be one of: "
                    "AUTO, FORBID, STRICT"
                )
            fabric = gpu_cluster_fabric(inputs, group)
            if group.gpu_cluster_key and not fabric:
                raise ValueError(
                    f"GPU node_groups entry '{group.key}' references gpu_cluster_key "
                    f"'{group.gpu_cluster_key}', but inputs.gpu_clusters."
                    f"{group.gpu_cluster_key}.infiniband_fabric is missing"
                )
            if fabric:
                gpu_fabric_checks.append((group.platform, group.preset, fabric))

    if gpu_fabric_checks and not selected_gpu_enabled:
        raise ValueError("GPU cluster fabric requires at least one GPU node_groups entry")
    if gpu_fabric_checks:
        tenant_id = as_text(get_path(payload, "client_info.nebius.tenant_id"))
        project_id = as_text(get_path(payload, "client_info.nebius.project_id"))
        region_id = as_text(get_path(payload, "client_info.nebius.region_id"))
        if not project_id:
            raise ValueError(
                "infiniband_fabric requires client_info.nebius.project_id so cxcli can confirm "
                "that the selected GPU preset supports GPU clustering"
            )

        from .provider_options import ProviderOptionLookup

        lookup = ProviderOptionLookup()
        for effective_gpu_platform, effective_gpu_preset, infiniband_fabric in sorted(
            set(gpu_fabric_checks)
        ):
            allow_gpu_clustering = lookup.compute_platform_preset_allows_gpu_clustering(
                project_id=project_id,
                platform_name=effective_gpu_platform,
                preset_name=effective_gpu_preset,
            )
            if allow_gpu_clustering is not True:
                raise ValueError(
                    "infiniband_fabric requires a GPU preset whose live Nebius metadata allows "
                    f"GPU clustering; selected {effective_gpu_platform}/{effective_gpu_preset} "
                    "does not have confirmed GPU clustering support"
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
    if mig_configured and not selected_gpu_enabled:
        raise ValueError("mig_strategy/mig_parted_config require at least one GPU node group")

    for validation_label, project_gpu_deployment_testing in project_gpu_deployment_testing_by_label:
        unknown_keys = sorted(
            str(key)
            for key in project_gpu_deployment_testing
            if str(key) not in {"operator_readiness", "gpu_visibility", "health_checker"}
        )
        if unknown_keys:
            hint = ""
            if "cuda_smoke" in unknown_keys:
                hint = "; use gpu_visibility for the bounded deploy probe"
            if "nccl" in unknown_keys:
                hint = "; use acceptance-test benchmark for NCCL"
            raise ValueError(
                f"{validation_label} has unsupported field(s): "
                + ", ".join(unknown_keys)
                + hint
            )
        operator_readiness = project_gpu_deployment_testing.get("operator_readiness", {})
        gpu_visibility = project_gpu_deployment_testing.get("gpu_visibility", {})
        health_checker = project_gpu_deployment_testing.get("health_checker", {})
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
        public_ip_allocation_name = as_text(get_path(payload, f"{base}.public_ip_allocation_name"))
        if public_ip_allocation_name and not id_pattern.fullmatch(public_ip_allocation_name):
            raise ValueError(
                f"{base}.public_ip_allocation_name must use lowercase letters, digits, and hyphens"
            )

        tunnel_cidr = as_text(get_path(payload, f"{base}.wireguard_tunnel_cidr", "10.8.0.1/22"))
        try:
            interface = ipaddress.ip_interface(tunnel_cidr)
        except ValueError as exc:
            raise ValueError(
                f"{base}.wireguard_tunnel_cidr must be a valid IPv4 interface CIDR "
                "(example: 10.8.0.1/22)"
            ) from exc
        if interface.version != 4:
            raise ValueError(
                f"{base}.wireguard_tunnel_cidr must be an IPv4 interface CIDR "
                "(example: 10.8.0.1/22)"
            )

        listen_port = get_path(payload, f"{base}.wireguard_listen_port", 51820)
        try:
            listen_port_int = int(listen_port)
        except Exception as exc:
            raise ValueError(
                f"{base}.wireguard_listen_port must be an integer between 1 and 65535"
            ) from exc
        if listen_port_int < 1 or listen_port_int > 65535:
            raise ValueError(f"{base}.wireguard_listen_port must be an integer between 1 and 65535")

        local_subnets = get_path(payload, f"{base}.local_subnets", None)
        if local_subnets is None:
            raise ValueError(f"{base}.local_subnets is required")
        if not isinstance(local_subnets, list):
            raise ValueError(f"{base}.local_subnets must be a list of CIDRs")
        for cidr in local_subnets:
            try:
                network = ipaddress.ip_network(str(cidr), strict=False)
            except ValueError as exc:
                raise ValueError(f"{base}.local_subnets must contain valid CIDRs") from exc
            if network.version != 4:
                raise ValueError(f"{base}.local_subnets currently supports IPv4 CIDRs only")

        client_default_dns = get_path(payload, f"{base}.client_default_dns", [])
        if client_default_dns is not None:
            if not isinstance(client_default_dns, list):
                raise ValueError(f"{base}.client_default_dns must be a list of IPv4 addresses")
            for dns in client_default_dns:
                try:
                    address = ipaddress.ip_address(str(dns))
                except ValueError as exc:
                    raise ValueError(
                        f"{base}.client_default_dns must contain valid IPv4 addresses"
                    ) from exc
                if address.version != 4:
                    raise ValueError(f"{base}.client_default_dns currently supports IPv4 only")

        clients = get_path(payload, f"{base}.clients", [])
        if clients is not None:
            if not isinstance(clients, list):
                raise ValueError(f"{base}.clients must be a list")
            seen_names: set[str] = set()
            seen_addresses: set[str] = set()
            for index, client in enumerate(clients):
                if not isinstance(client, Mapping):
                    raise ValueError(f"{base}.clients[{index}] must be an object")
                name = as_text(client.get("name"))
                if not name:
                    raise ValueError(f"{base}.clients[{index}].name is required")
                if not id_pattern.fullmatch(name):
                    raise ValueError(
                        f"{base}.clients[{index}].name must use lowercase letters, digits, and hyphens"
                    )
                if name in seen_names:
                    raise ValueError(f"{base}.clients contains duplicate client name '{name}'")
                seen_names.add(name)
                address = as_text(client.get("client_wg_tunnel_address"))
                if address:
                    try:
                        client_interface = ipaddress.ip_interface(address)
                    except ValueError as exc:
                        raise ValueError(
                            f"{base}.clients[{index}].client_wg_tunnel_address must be a valid IPv4 /32"
                        ) from exc
                    if client_interface.version != 4 or client_interface.network.prefixlen != 32:
                        raise ValueError(
                            f"{base}.clients[{index}].client_wg_tunnel_address must be an IPv4 /32"
                        )
                    if (
                        client_interface.ip == interface.ip
                        or client_interface.ip not in interface.network
                    ):
                        raise ValueError(
                            f"{base}.clients[{index}].client_wg_tunnel_address must be inside "
                            "wireguard_tunnel_cidr and cannot be the server address"
                        )
                    if str(client_interface.ip) in seen_addresses:
                        raise ValueError(
                            f"{base}.clients contains duplicate client_wg_tunnel_address "
                            f"'{client_interface.with_prefixlen}'"
                        )
                    seen_addresses.add(str(client_interface.ip))
                local_subnets = client.get("local_subnets", [])
                if local_subnets is not None:
                    if not isinstance(local_subnets, list):
                        raise ValueError(f"{base}.clients[{index}].local_subnets must be a list")
                    for cidr in local_subnets:
                        try:
                            network = ipaddress.ip_network(str(cidr), strict=False)
                        except ValueError as exc:
                            raise ValueError(
                                f"{base}.clients[{index}].local_subnets must contain valid CIDRs"
                            ) from exc
                        if network.version != 4:
                            raise ValueError(
                                f"{base}.clients[{index}].local_subnets currently supports IPv4 CIDRs only"
                            )
                dns_values = client.get("dns", [])
                if dns_values is not None:
                    if not isinstance(dns_values, list):
                        raise ValueError(f"{base}.clients[{index}].dns must be a list")
                    for dns in dns_values:
                        try:
                            address = ipaddress.ip_address(str(dns))
                        except ValueError as exc:
                            raise ValueError(
                                f"{base}.clients[{index}].dns must contain valid IPv4 addresses"
                            ) from exc
                        if address.version != 4:
                            raise ValueError(
                                f"{base}.clients[{index}].dns currently supports IPv4 only"
                            )


def _validate_ssh_jumphost(
    payload: Mapping[str, Any],
    get_path: Callable,
    as_text: Callable,
    base: str,
    id_pattern: Pattern[str],
) -> None:
    ssh_jump_enabled = bool(get_path(payload, f"{base}.enabled", False))
    if ssh_jump_enabled:
        ssh_user_name = as_text(get_path(payload, f"{base}.ssh_user_name"))
        _validate_linux_user_name(ssh_user_name, field_label=f"{base}.ssh_user_name")
        public_ip_allocation_name = as_text(get_path(payload, f"{base}.public_ip_allocation_name"))
        if public_ip_allocation_name and not id_pattern.fullmatch(public_ip_allocation_name):
            raise ValueError(
                f"{base}.public_ip_allocation_name must use lowercase letters, digits, and hyphens"
            )
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
            "eso_version_policy",
            "kubernetes_secret_name",
            "payload",
        }
        unknown_keys = sorted(str(key) for key in secret if str(key) not in supported_keys)
        if unknown_keys:
            raise ValueError(f"{secret_label} has unsupported field(s): " + ", ".join(unknown_keys))
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
        eso_version_policy = as_text(secret.get("eso_version_policy"))
        if eso_version_policy:
            from .mysterybox_eso import MYSTERYBOX_ESO_VERSION_POLICIES

            if eso_version_policy not in MYSTERYBOX_ESO_VERSION_POLICIES:
                allowed = ", ".join(sorted(MYSTERYBOX_ESO_VERSION_POLICIES))
                raise ValueError(f"{secret_label}.eso_version_policy must be one of: {allowed}")
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
