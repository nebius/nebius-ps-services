"""Nebius VPC and MK8s deployment-readiness checks against live network state."""

from __future__ import annotations

import ipaddress
from collections.abc import Collection, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from nebius.api.nebius.common.v1 import GetByNameRequest
from nebius.api.nebius.compute.v1 import GpuClusterServiceClient
from nebius.api.nebius.mk8s.v1 import (
    ClusterServiceClient,
    GetNodeGroupCompatibilityMatrixRequest,
    NodeGroupServiceClient,
)
from nebius.api.nebius.vpc.v1 import (
    GetNetworkRequest,
    GetSubnetRequest,
    NetworkServiceClient,
    SubnetServiceClient,
)

from .component_defaults import read_component_path, resolve_component_defaults
from .component_instances import component_instance_id, component_instance_label, component_type_id
from .component_wiring import row_input_bindings
from .components import component_lookup
from .mk8s_node_groups import (
    cluster_name,
    cluster_service_cidrs,
    cluster_subnet_id,
    gpu_node_groups,
)
from .provider_options import _provider_request_kwargs
from .runtime_config import to_plain_data
from .sdk_auth import init_nebius_sdk


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _is_not_found_error(exc: Exception) -> bool:
    status = getattr(exc, "status", None)
    code = getattr(status, "code", None)
    code_name = _as_text(getattr(code, "name", None)).upper()
    code_text = _as_text(code).upper()
    if code_name == "NOT_FOUND" or code_text in {"NOT_FOUND", "STATUSCODE.NOT_FOUND"}:
        return True
    message = str(exc).lower()
    return "not found" in message or "not_found" in message or "statuscode.not_found" in message


@dataclass(frozen=True)
class _Mk8sResolvedComponent:
    component_label: str
    component_id: str
    project_id: str
    inputs: Mapping[str, Any]
    raw_row: Mapping[str, Any]


@dataclass(frozen=True)
class _VpcNetworkingRef:
    component_label: str
    field_label: str
    project_id: str
    network_id: str
    subnet_id: str | None = None


@dataclass(frozen=True)
class _Mk8sGpuStackCompatibilityChoice:
    platform: str
    os: str
    drivers_preset: str


@dataclass(frozen=True)
class _Mk8sGpuStackCompatibilityTarget:
    component: _Mk8sResolvedComponent
    group: Any
    version: str


def _resolved_mk8s_components(payload: Mapping[str, Any]) -> tuple[_Mk8sResolvedComponent, ...]:
    infra = payload.get("infra")
    if not isinstance(infra, Mapping):
        return ()
    components = infra.get("components")
    if not isinstance(components, list):
        return ()

    entry_by_id = component_lookup("infra")
    client_info = payload.get("client_info")
    nebius_info = client_info.get("nebius") if isinstance(client_info, Mapping) else None
    default_project_id = _as_text(
        nebius_info.get("project_id") if isinstance(nebius_info, Mapping) else ""
    )
    resolved_components: list[_Mk8sResolvedComponent] = []
    for item in components:
        if not isinstance(item, Mapping) or not bool(item.get("enabled", False)):
            continue
        component_id = component_type_id(item)
        mk8s_entry = entry_by_id.get(component_id)
        if mk8s_entry is None or getattr(mk8s_entry, "validation_profile", "") != "mk8s_cluster":
            continue

        resolved = resolve_component_defaults(
            payload=payload,
            component_node=dict(item),
            entry=mk8s_entry,
            include_shared=False,
        )
        inputs = resolved.get("inputs")
        if not isinstance(inputs, Mapping):
            continue
        instance_id = component_instance_id(item)
        cluster_inputs = inputs.get("cluster")
        project_id = (
            _as_text(cluster_inputs.get("parent_id"))
            if isinstance(cluster_inputs, Mapping)
            else ""
        ) or default_project_id
        resolved_components.append(
            _Mk8sResolvedComponent(
                component_label=component_instance_label(component_id, instance_id),
                component_id=component_id,
                project_id=project_id,
                inputs=inputs,
                raw_row=item,
            )
        )
    return tuple(resolved_components)


def _cluster_k8s_version(inputs: Mapping[str, Any]) -> str:
    cluster_inputs = inputs.get("cluster")
    if not isinstance(cluster_inputs, Mapping):
        return ""
    return _as_text(cluster_inputs.get("k8s_version"))


def _node_group_k8s_version(inputs: Mapping[str, Any], group: Any) -> str:
    raw_groups = inputs.get("node_groups")
    if isinstance(raw_groups, Mapping):
        raw_group = raw_groups.get(getattr(group, "key", ""))
        if isinstance(raw_group, Mapping):
            version = _as_text(raw_group.get("version"))
            if version:
                return version
    return _cluster_k8s_version(inputs)


def _gpu_stack_compatibility_targets(
    payload: Mapping[str, Any],
) -> tuple[_Mk8sGpuStackCompatibilityTarget, ...]:
    targets: list[_Mk8sGpuStackCompatibilityTarget] = []
    for component in _resolved_mk8s_components(payload):
        for group in gpu_node_groups(component.inputs):
            version = _node_group_k8s_version(component.inputs, group)
            if not version:
                continue
            if group.gpu_stack_source != "nebius_image":
                continue
            if not group.platform or not group.gpu_stack_preset:
                continue
            targets.append(
                _Mk8sGpuStackCompatibilityTarget(
                    component=component,
                    group=group,
                    version=version,
                )
            )
    return tuple(targets)


def has_mk8s_gpu_stack_compatibility_preflight_targets(config: Any) -> bool:
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        return False
    return bool(_gpu_stack_compatibility_targets(payload))


def _mk8s_gpu_stack_compatibility_choices(response: Any) -> tuple[_Mk8sGpuStackCompatibilityChoice, ...]:
    items = list(getattr(response, "items", []) or [])
    if not items:
        items = [
            item
            for version_item in getattr(response, "versions", []) or []
            for item in getattr(version_item, "items", []) or []
        ]

    choices: list[_Mk8sGpuStackCompatibilityChoice] = []
    for item in items:
        drivers_preset = _as_text(getattr(item, "drivers_preset", None))
        if not drivers_preset:
            continue
        os_value = _as_text(getattr(item, "os", None))
        for platform in getattr(item, "compatible_platforms", []) or []:
            platform_name = _as_text(platform)
            if platform_name:
                choices.append(
                    _Mk8sGpuStackCompatibilityChoice(
                        platform=platform_name,
                        os=os_value,
                        drivers_preset=drivers_preset,
                    )
                )
    return tuple(choices)


def _gpu_stack_choice_matches_group(
    choice: _Mk8sGpuStackCompatibilityChoice,
    group: Any,
) -> bool:
    if choice.platform != group.platform:
        return False
    if not group.os or choice.os != group.os:
        return False
    return choice.drivers_preset == group.gpu_stack_preset


def _compatible_gpu_stack_os_values_for_preset(
    choices: Collection[_Mk8sGpuStackCompatibilityChoice],
    group: Any,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            choice.os
            for choice in choices
            if choice.platform == group.platform
            and choice.drivers_preset == group.gpu_stack_preset
            and choice.os
        )
    )


def _compatible_gpu_stack_presets(
    choices: Collection[_Mk8sGpuStackCompatibilityChoice],
    group: Any,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            choice.drivers_preset
            for choice in choices
            if choice.platform == group.platform
            and choice.drivers_preset
            and (not group.os or choice.os == group.os)
        )
    )


def _compatible_gpu_stack_os_values(
    choices: Collection[_Mk8sGpuStackCompatibilityChoice],
    group: Any,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            choice.os
            for choice in choices
            if choice.platform == group.platform and choice.os
        )
    )


def _gpu_stack_compatibility_error(
    *,
    component: _Mk8sResolvedComponent,
    group: Any,
    version: str,
    choices: Collection[_Mk8sGpuStackCompatibilityChoice],
) -> RuntimeError:
    if not group.os:
        candidate_os_values = _compatible_gpu_stack_os_values_for_preset(choices, group)
        if candidate_os_values:
            candidate_text = (
                " Compatible OS values for this platform/preset: "
                f"{', '.join(candidate_os_values)}."
            )
        else:
            candidate_text = " The live compatibility matrix returned no OS values for that platform/preset."
        return RuntimeError(
            "MK8s GPU stack compatibility preflight failed: "
            f"component '{component.component_label}' node group '{group.name}' uses "
            f"gpu_stack_preset '{group.gpu_stack_preset}' with platform '{group.platform}' "
            f"for Kubernetes {version}, but omits os. Nebius GPU stack compatibility is "
            f"OS-specific.{candidate_text} "
            f"Set inputs.node_groups.{group.key}.os and rerender before deploy."
        )

    os_fragment = f" and OS '{group.os}'" if group.os else ""
    candidate_presets = _compatible_gpu_stack_presets(choices, group)
    candidate_os_values = _compatible_gpu_stack_os_values(choices, group)
    if candidate_presets:
        candidate_text = (
            " Compatible GPU stack presets for this platform"
            f"{('/OS' if group.os else '')}: {', '.join(candidate_presets)}."
        )
    elif candidate_os_values:
        candidate_text = (
            f" Compatible OS values for platform '{group.platform}': "
            f"{', '.join(candidate_os_values)}."
        )
    else:
        candidate_text = " The live compatibility matrix returned no GPU stack presets for that platform."
    return RuntimeError(
        "MK8s GPU stack compatibility preflight failed: "
        f"component '{component.component_label}' node group '{group.name}' uses "
        f"gpu_stack_preset '{group.gpu_stack_preset}' with platform '{group.platform}'"
        f"{os_fragment} for Kubernetes {version}, but the live Nebius compatibility "
        f"matrix does not list that combination.{candidate_text} "
        f"Update inputs.node_groups.{group.key}.gpu_stack_preset and rerender before deploy."
    )


def validate_mk8s_gpu_stack_compatibility_preflight(config: Any) -> None:
    """Fail fast when a Nebius-image GPU node group uses an unsupported stack tuple."""
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        return

    targets = _gpu_stack_compatibility_targets(payload)
    if not targets:
        return

    sdk_by_project: dict[str, Any] = {}
    choices_by_project_version: dict[
        tuple[str, str], tuple[_Mk8sGpuStackCompatibilityChoice, ...]
    ] = {}
    try:
        for target in targets:
            component = target.component
            group = target.group
            version = target.version
            cache_key = (component.project_id, version)
            choices = choices_by_project_version.get(cache_key)
            if choices is None:
                sdk = sdk_by_project.get(component.project_id)
                if sdk is None:
                    sdk = init_nebius_sdk(
                        parent_id=component.project_id or None,
                        context="MK8s GPU stack compatibility preflight",
                    )
                    sdk_by_project[component.project_id] = sdk
                response = (
                    NodeGroupServiceClient(sdk)
                    .get_compatibility_matrix(
                        GetNodeGroupCompatibilityMatrixRequest(
                            cluster_kubernetes_version=version,
                        ),
                        **_provider_request_kwargs(),
                    )
                    .wait()
                )
                choices = _mk8s_gpu_stack_compatibility_choices(response)
                choices_by_project_version[cache_key] = choices
            if any(_gpu_stack_choice_matches_group(choice, group) for choice in choices):
                continue
            raise _gpu_stack_compatibility_error(
                component=component,
                group=group,
                version=version,
                choices=choices,
            )
    finally:
        for sdk in sdk_by_project.values():
            with suppress(Exception):
                sdk.sync_close()


def _default_project_id(payload: Mapping[str, Any]) -> str:
    client_info = payload.get("client_info")
    nebius_info = client_info.get("nebius") if isinstance(client_info, Mapping) else None
    return _as_text(nebius_info.get("project_id") if isinstance(nebius_info, Mapping) else "")


def _row_bindings_by_target(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        binding.target_path: binding
        for binding in row_input_bindings(row, field_label="infra.components[]")
    }


def _binding_target(component_id: str, field_name: str) -> str:
    if component_id == "mk8s":
        return f"inputs.cluster.{field_name}"
    return f"inputs.{field_name}"


def _binding_ref(binding: Any) -> str:
    ref = f"{binding.source_component_id}@{binding.source_instance_id}.{binding.source_output_name}"
    if binding.key:
        ref = f"{ref}.{binding.key}"
    if binding.attribute:
        ref = f"{ref}.{binding.attribute}"
    return ref


def _enabled_vpc_rows(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, Mapping) else None
    if not isinstance(components, list):
        return {}
    rows: dict[str, Mapping[str, Any]] = {}
    for row in components:
        if not isinstance(row, Mapping) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != "vpc":
            continue
        instance_id = component_instance_id(row)
        if instance_id:
            rows[instance_id] = row
    return rows


def _enabled_resolved_infra_components(
    payload: Mapping[str, Any],
) -> tuple[tuple[str, str, str, Mapping[str, Any], Mapping[str, Any]], ...]:
    infra = payload.get("infra")
    if not isinstance(infra, Mapping):
        return ()
    components = infra.get("components")
    if not isinstance(components, list):
        return ()
    entry_by_id = component_lookup("infra")
    resolved_components: list[tuple[str, str, str, Mapping[str, Any], Mapping[str, Any]]] = []
    for item in components:
        if not isinstance(item, Mapping) or not bool(item.get("enabled", False)):
            continue
        component_id = component_type_id(item)
        entry = entry_by_id.get(component_id)
        if entry is None:
            continue
        resolved = resolve_component_defaults(
            payload=payload,
            component_node=dict(item),
            entry=entry,
            include_shared=False,
        )
        inputs = resolved.get("inputs")
        if not isinstance(inputs, Mapping):
            inputs = {}
        instance_id = component_instance_id(item)
        resolved_components.append(
            (
                component_id,
                instance_id,
                component_instance_label(component_id, instance_id),
                inputs,
                item,
            )
        )
    return tuple(resolved_components)


def _vpc_networking_refs(payload: Mapping[str, Any]) -> tuple[_VpcNetworkingRef, ...]:
    default_project_id = _default_project_id(payload)
    refs: list[_VpcNetworkingRef] = []
    for component_id, _instance_id, component_label, inputs, raw_row in (
        _enabled_resolved_infra_components(payload)
    ):
        bindings = _row_bindings_by_target(raw_row)
        if component_id == "mk8s":
            cluster_inputs = inputs.get("cluster")
            if not isinstance(cluster_inputs, Mapping):
                continue
            project_id = _as_text(cluster_inputs.get("parent_id")) or default_project_id
            network_id = _as_text(cluster_inputs.get("network_id"))
            cluster_subnet_id = _as_text(cluster_inputs.get("subnet_id"))
            network_binding = bindings.get("inputs.cluster.network_id")
            subnet_binding = bindings.get("inputs.cluster.subnet_id")
            if network_binding is None or subnet_binding is None:
                refs.append(
                    _VpcNetworkingRef(
                        component_label=component_label,
                        field_label="inputs.cluster",
                        project_id=project_id,
                        network_id=network_id,
                        subnet_id=cluster_subnet_id if subnet_binding is None else None,
                    )
                )
            node_groups = inputs.get("node_groups")
            if not isinstance(node_groups, Mapping):
                continue
            for group_key, group in node_groups.items():
                if not isinstance(group, Mapping) or not bool(group.get("enabled", True)):
                    continue
                group_label = f"inputs.node_groups.{group_key}"
                group_subnet_id = _as_text(group.get("subnet_id"))
                if group_subnet_id:
                    refs.append(
                        _VpcNetworkingRef(
                            component_label=component_label,
                            field_label=f"{group_label}.subnet_id",
                            project_id=project_id,
                            network_id=network_id,
                            subnet_id=group_subnet_id,
                        )
                    )
                network_interfaces = group.get("network_interfaces")
                if network_interfaces is None:
                    continue
                if not isinstance(network_interfaces, list) or not network_interfaces:
                    raise RuntimeError(
                        "VPC networking preflight failed: "
                        f"component '{component_label}' {group_label}.network_interfaces "
                        "must be a non-empty list when set."
                    )
                for index, interface in enumerate(network_interfaces):
                    subnet_id = (
                        _as_text(interface.get("subnet_id"))
                        if isinstance(interface, Mapping)
                        else ""
                    )
                    if not subnet_id:
                        raise RuntimeError(
                            "VPC networking preflight failed: "
                            f"component '{component_label}' "
                            f"{group_label}.network_interfaces[{index}].subnet_id is required."
                        )
                    refs.append(
                        _VpcNetworkingRef(
                            component_label=component_label,
                            field_label=f"{group_label}.network_interfaces[{index}].subnet_id",
                            project_id=project_id,
                            network_id=network_id,
                            subnet_id=subnet_id,
                        )
                    )
            continue

        if component_id in {"vm", "nfs", "wireguard-gw", "ssh-jumphost"}:
            network_binding = bindings.get(_binding_target(component_id, "network_id"))
            subnet_binding = bindings.get(_binding_target(component_id, "subnet_id"))
            if network_binding is None or subnet_binding is None:
                refs.append(
                    _VpcNetworkingRef(
                        component_label=component_label,
                        field_label="inputs",
                        project_id=_as_text(inputs.get("parent_id")) or default_project_id,
                        network_id=_as_text(inputs.get("network_id")),
                        subnet_id=_as_text(inputs.get("subnet_id")) if subnet_binding is None else None,
                    )
                )
            continue

        if component_id == "managed-postgresql":
            if bindings.get(_binding_target(component_id, "network_id")) is not None:
                continue
            refs.append(
                _VpcNetworkingRef(
                    component_label=component_label,
                    field_label="inputs",
                    project_id=_as_text(inputs.get("parent_id")) or default_project_id,
                    network_id=_as_text(inputs.get("network_id")),
                    subnet_id=None,
                )
            )
    return tuple(refs)


def _resource_parent_id(resource: Any) -> str:
    metadata = getattr(resource, "metadata", None)
    return _as_text(getattr(metadata, "parent_id", None))


def _subnet_network_id(subnet: Any) -> str:
    spec = getattr(subnet, "spec", None)
    return _as_text(getattr(spec, "network_id", None))


def _referenced_gpu_cluster_names(
    inputs: Mapping[str, Any],
    *,
    configured_cluster_name: str,
) -> tuple[str, ...]:
    gpu_clusters = inputs.get("gpu_clusters")
    if not isinstance(gpu_clusters, Mapping):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for group in gpu_node_groups(inputs):
        key = group.gpu_cluster_key
        if not key:
            continue
        gpu_cluster = gpu_clusters.get(key)
        if not isinstance(gpu_cluster, Mapping):
            continue
        name = _as_text(gpu_cluster.get("name")) or f"{configured_cluster_name}-{key}-gpu-cluster"
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return tuple(names)


def has_mk8s_resource_name_preflight_targets(config: Any) -> bool:
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        return False
    for component in _resolved_mk8s_components(payload):
        configured_cluster_name = cluster_name(component.inputs)
        if not configured_cluster_name or not component.project_id:
            continue
        return True
    return False


def _service_cidr_prefix_lengths(raw_value: Any) -> tuple[int, ...]:
    if raw_value is None:
        return ()
    if not isinstance(raw_value, (list, tuple)):
        raise RuntimeError(
            "inputs.cluster.kube_network.service_cidrs must be a list of CIDR "
            'strings or prefix-length strings such as ["/20"].'
        )

    prefixes: list[int] = []
    for item in raw_value:
        text = _as_text(item)
        if not text:
            raise RuntimeError(
                "inputs.cluster.kube_network.service_cidrs cannot contain empty values."
            )
        try:
            if text.startswith("/"):
                prefix = int(text[1:])
            else:
                prefix = ipaddress.ip_network(text, strict=False).prefixlen
        except Exception as exc:
            raise RuntimeError(
                f"inputs.cluster.kube_network.service_cidrs contains an invalid value: {text!r}"
            ) from exc
        prefixes.append(prefix)

    if len(prefixes) != 1:
        raise RuntimeError(
            "inputs.cluster.kube_network.service_cidrs must contain exactly one CIDR or "
            "prefix value."
        )
    return tuple(prefixes)


def _subnet_pool_cidrs(subnet: Any) -> tuple[str, ...]:
    spec = getattr(subnet, "spec", None)
    ipv4_private_pools = getattr(spec, "ipv4_private_pools", None)
    pools = list(getattr(ipv4_private_pools, "pools", []) or [])
    cidrs: list[str] = []
    for pool in pools:
        for cidr in list(getattr(pool, "cidrs", []) or []):
            value = _as_text(getattr(cidr, "cidr", None)) or _as_text(cidr)
            if value:
                cidrs.append(value)
    return tuple(cidrs)


def _planned_cluster_subnet_pool_cidrs(
    *,
    payload: Mapping[str, Any],
    raw_row: Mapping[str, Any],
) -> tuple[str, ...]:
    bindings = _row_bindings_by_target(raw_row)
    subnet_binding = bindings.get("inputs.cluster.subnet_id")
    if subnet_binding is None or subnet_binding.source_component_id != "vpc":
        return ()
    if subnet_binding.source_output_name != "subnets" or not subnet_binding.key:
        return ()
    vpc_row = _enabled_vpc_rows(payload).get(subnet_binding.source_instance_id or "")
    subnet = read_component_path(vpc_row or {}, f"inputs.subnets.{subnet_binding.key}")
    if not isinstance(subnet, Mapping):
        return ()
    cidrs = subnet.get("ipv4_private_cidrs")
    if not isinstance(cidrs, (list, tuple)):
        return ()
    return tuple(_as_text(cidr) for cidr in cidrs if _as_text(cidr))


def _validate_service_prefix_against_pool_cidr(
    *,
    component_label: str,
    service_cidrs: list[str],
    service_prefix: int,
    pool_cidr: str,
    subnet_label: str,
) -> None:
    try:
        pool_prefix = ipaddress.ip_network(pool_cidr, strict=False).prefixlen
    except ValueError as exc:
        raise RuntimeError(
            "VPC networking preflight failed: "
            f"component '{component_label}' subnet {subnet_label} "
            f"returned malformed pool CIDR {pool_cidr!r}."
        ) from exc

    if service_prefix <= pool_prefix:
        raise RuntimeError(
            "VPC networking preflight failed: "
            f"component '{component_label}' "
            "inputs.cluster.kube_network.service_cidrs="
            f"{service_cidrs!r} "
            f"is too large for subnet {subnet_label} pool {pool_cidr}. "
            "Nebius reserves Kubernetes service CIDRs from the same subnet pool, so this "
            "single-pool subnet can stall cluster provisioning before any node groups are created. "
            'Use a smaller service CIDR such as ["/20"] or choose a larger subnet.'
        )


def _component_project_id(
    *,
    payload_default_project_id: str,
    component_id: str,
    inputs: Mapping[str, Any],
) -> str:
    if component_id == "mk8s":
        cluster_inputs = inputs.get("cluster")
        if isinstance(cluster_inputs, Mapping):
            return _as_text(cluster_inputs.get("parent_id")) or payload_default_project_id
        return payload_default_project_id
    return _as_text(inputs.get("parent_id")) or payload_default_project_id


def _vpc_row_project_id(
    *,
    payload_default_project_id: str,
    row: Mapping[str, Any],
) -> str:
    inputs = row.get("inputs")
    if isinstance(inputs, Mapping):
        return _as_text(inputs.get("parent_id")) or payload_default_project_id
    return payload_default_project_id


def _validate_planned_vpc_bindings(payload: Mapping[str, Any]) -> None:
    default_project_id = _default_project_id(payload)
    vpc_rows = _enabled_vpc_rows(payload)
    for component_id, _instance_id, component_label, inputs, raw_row in (
        _enabled_resolved_infra_components(payload)
    ):
        bindings = _row_bindings_by_target(raw_row)
        if not bindings:
            continue
        project_id = _component_project_id(
            payload_default_project_id=default_project_id,
            component_id=component_id,
            inputs=inputs,
        )
        network_target = _binding_target(component_id, "network_id")
        subnet_target = _binding_target(component_id, "subnet_id")
        network_binding = bindings.get(network_target)
        subnet_binding = bindings.get(subnet_target)
        for target_path, binding in bindings.items():
            if binding.source_component_id != "vpc":
                continue
            if not binding.source_instance_id:
                raise RuntimeError(
                    "VPC networking preflight failed: "
                    f"component '{component_label}' binding {target_path} must set source_instance."
                )
            vpc_row = vpc_rows.get(binding.source_instance_id)
            if vpc_row is None:
                raise RuntimeError(
                    "VPC networking preflight failed: "
                    f"component '{component_label}' binding {target_path} references "
                    f"disabled or missing infra:vpc@{binding.source_instance_id}."
                )
            vpc_project_id = _vpc_row_project_id(
                payload_default_project_id=default_project_id,
                row=vpc_row,
            )
            if project_id and vpc_project_id and project_id != vpc_project_id:
                raise RuntimeError(
                    "VPC networking preflight failed: "
                    f"component '{component_label}' binding {target_path} uses "
                    f"infra:vpc@{binding.source_instance_id} in project {vpc_project_id}, "
                    f"not {project_id}."
                )
            if binding.source_output_name == "subnets":
                subnets = read_component_path(vpc_row, "inputs.subnets")
                if not binding.key or not isinstance(subnets, Mapping) or binding.key not in subnets:
                    raise RuntimeError(
                        "VPC networking preflight failed: "
                        f"component '{component_label}' binding {target_path} references "
                        f"missing VPC subnet key '{binding.key or '<empty>'}'."
                    )

        if (
            network_binding is not None
            and subnet_binding is not None
            and network_binding.source_instance_id != subnet_binding.source_instance_id
        ):
            raise RuntimeError(
                "VPC networking preflight failed: "
                f"component '{component_label}' subnet binding {_binding_ref(subnet_binding)} "
                f"does not belong to network binding {_binding_ref(network_binding)}."
            )
        if network_binding is None and subnet_binding is not None:
            selected_network_id = _as_text(read_component_path(raw_row, network_target))
            vpc_row = vpc_rows.get(subnet_binding.source_instance_id or "")
            vpc_network = read_component_path(vpc_row or {}, "inputs.network")
            existing_id = (
                _as_text(vpc_network.get("existing_id"))
                if isinstance(vpc_network, Mapping)
                else ""
            )
            if selected_network_id and existing_id != selected_network_id:
                raise RuntimeError(
                    "VPC networking preflight failed: "
                    f"component '{component_label}' subnet binding {_binding_ref(subnet_binding)} "
                    f"is created under network {existing_id or '(new)'}, "
                    f"not selected network {selected_network_id}."
                )


def _validate_vpc_hierarchy(payload: Mapping[str, Any]) -> None:
    _validate_planned_vpc_bindings(payload)
    refs = _vpc_networking_refs(payload)
    if not refs:
        return
    sdk_by_project: dict[str, Any] = {}
    network_cache: dict[tuple[str, str], Any] = {}
    subnet_cache: dict[tuple[str, str], Any] = {}
    try:
        for ref in refs:
            if not ref.project_id:
                raise RuntimeError(
                    "VPC networking preflight failed: "
                    f"component '{ref.component_label}' {ref.field_label} is missing parent_id/project_id."
                )
            if not ref.network_id:
                raise RuntimeError(
                    "VPC networking preflight failed: "
                    f"component '{ref.component_label}' {ref.field_label}.network_id is required."
                )
            sdk = sdk_by_project.get(ref.project_id)
            if sdk is None:
                sdk = init_nebius_sdk(
                    parent_id=ref.project_id,
                    context="VPC networking preflight",
                )
                sdk_by_project[ref.project_id] = sdk

            network_key = (ref.project_id, ref.network_id)
            network = network_cache.get(network_key)
            if network is None:
                network = NetworkServiceClient(sdk).get(
                    GetNetworkRequest(id=ref.network_id)
                ).wait()
                network_cache[network_key] = network
            network_parent_id = _resource_parent_id(network)
            if network_parent_id != ref.project_id:
                raise RuntimeError(
                    "VPC networking preflight failed: "
                    f"component '{ref.component_label}' {ref.field_label}.network_id "
                    f"{ref.network_id} belongs to project {network_parent_id or '(unknown)'}, "
                    f"not {ref.project_id}."
                )

            if ref.subnet_id is None:
                continue
            if not ref.subnet_id:
                raise RuntimeError(
                    "VPC networking preflight failed: "
                    f"component '{ref.component_label}' {ref.field_label}.subnet_id is required."
                )
            subnet_key = (ref.project_id, ref.subnet_id)
            subnet = subnet_cache.get(subnet_key)
            if subnet is None:
                subnet = SubnetServiceClient(sdk).get(GetSubnetRequest(id=ref.subnet_id)).wait()
                subnet_cache[subnet_key] = subnet
            subnet_parent_id = _resource_parent_id(subnet)
            if subnet_parent_id != ref.project_id:
                raise RuntimeError(
                    "VPC networking preflight failed: "
                    f"component '{ref.component_label}' {ref.field_label}.subnet_id "
                    f"{ref.subnet_id} belongs to project {subnet_parent_id or '(unknown)'}, "
                    f"not {ref.project_id}."
                )
            subnet_network_id = _subnet_network_id(subnet)
            if subnet_network_id != ref.network_id:
                raise RuntimeError(
                    "VPC networking preflight failed: "
                    f"component '{ref.component_label}' {ref.field_label}.subnet_id "
                    f"{ref.subnet_id} belongs to network {subnet_network_id or '(unknown)'}, "
                    f"not selected network {ref.network_id}."
                )
    finally:
        for sdk in sdk_by_project.values():
            with suppress(Exception):
                sdk.sync_close()


def validate_vpc_networking_preflight(config: Any) -> None:
    """Fail fast on invalid VPC hierarchy and MK8s subnet/service-CIDR combinations."""
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        return
    _validate_vpc_hierarchy(payload)
    sdk = None
    try:
        for component in _resolved_mk8s_components(payload):
            inputs = component.inputs
            subnet_id = cluster_subnet_id(inputs)

            service_cidrs = list(cluster_service_cidrs(inputs)) or ["/16"]
            service_prefixes = _service_cidr_prefix_lengths(service_cidrs)
            if not service_prefixes:
                continue
            service_prefix = service_prefixes[0]

            if not subnet_id:
                planned_pool_cidrs = _planned_cluster_subnet_pool_cidrs(
                    payload=payload,
                    raw_row=component.raw_row,
                )
                if len(planned_pool_cidrs) == 1:
                    _validate_service_prefix_against_pool_cidr(
                        component_label=component.component_label,
                        service_cidrs=service_cidrs,
                        service_prefix=service_prefix,
                        pool_cidr=planned_pool_cidrs[0],
                        subnet_label="planned VPC subnet",
                    )
                continue

            if sdk is None:
                sdk = init_nebius_sdk(
                    parent_id=component.project_id or None, context="VPC networking preflight"
                )
            subnet_client = SubnetServiceClient(sdk)
            subnet = subnet_client.get(GetSubnetRequest(id=subnet_id)).wait()
            pool_cidrs = _subnet_pool_cidrs(subnet)
            if len(pool_cidrs) != 1:
                continue

            _validate_service_prefix_against_pool_cidr(
                component_label=component.component_label,
                service_cidrs=service_cidrs,
                service_prefix=service_prefix,
                pool_cidr=pool_cidrs[0],
                subnet_label=subnet_id,
            )
    finally:
        if sdk is not None:
            with suppress(Exception):
                sdk.sync_close()


def validate_mk8s_resource_name_preflight(
    config: Any,
    *,
    managed_mk8s_cluster_names: Collection[str] = (),
    managed_gpu_cluster_names: Collection[str] = (),
) -> None:
    """Fail fast when live MK8s/GPU-cluster names would collide outside Terraform state."""
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        return

    managed_mk8s = {_as_text(item) for item in managed_mk8s_cluster_names if _as_text(item)}
    managed_gpu = {_as_text(item) for item in managed_gpu_cluster_names if _as_text(item)}
    sdk_by_project: dict[str, Any] = {}
    try:
        for component in _resolved_mk8s_components(payload):
            project_id = component.project_id
            configured_cluster_name = cluster_name(component.inputs)
            if not project_id or not configured_cluster_name:
                continue

            sdk = sdk_by_project.get(project_id)
            if sdk is None:
                sdk = init_nebius_sdk(
                    parent_id=project_id,
                    context="MK8s resource-name preflight",
                )
                sdk_by_project[project_id] = sdk
            mk8s_client = ClusterServiceClient(sdk)
            gpu_client = GpuClusterServiceClient(sdk)

            if configured_cluster_name not in managed_mk8s:
                try:
                    mk8s_client.get_by_name(
                        GetByNameRequest(parent_id=project_id, name=configured_cluster_name)
                    ).wait()
                except Exception as exc:
                    if not _is_not_found_error(exc):
                        raise
                else:
                    raise RuntimeError(
                        "MK8s resource-name preflight failed: "
                        f"component '{component.component_label}' resolves cluster name "
                        f"'{configured_cluster_name}' in project {project_id}, but a live Nebius MK8s cluster "
                        "with that name already exists and is not tracked in the current Terraform state. "
                        "If this is leftover from an earlier failed run, delete the live cluster and retry. "
                        "Otherwise import it into Terraform state or change inputs.cluster.cluster_name and rerender."
                    )

            for gpu_cluster_name in _referenced_gpu_cluster_names(
                component.inputs,
                configured_cluster_name=configured_cluster_name,
            ):
                if not gpu_cluster_name or gpu_cluster_name in managed_gpu:
                    continue
                try:
                    gpu_client.get_by_name(
                        GetByNameRequest(parent_id=project_id, name=gpu_cluster_name)
                    ).wait()
                except Exception as exc:
                    if not _is_not_found_error(exc):
                        raise
                else:
                    raise RuntimeError(
                        "MK8s resource-name preflight failed: "
                        f"component '{component.component_label}' resolves GPU cluster name "
                        f"'{gpu_cluster_name}' in project {project_id}, but a live Nebius GPU cluster "
                        "with that name already exists and is not tracked in the current Terraform state. "
                        "If this is leftover from an earlier failed run, delete the live GPU cluster and retry. "
                        "Otherwise import it into Terraform state or change inputs.gpu_clusters and rerender."
                    )
    finally:
        for sdk in sdk_by_project.values():
            with suppress(Exception):
                sdk.sync_close()


__all__ = [
    "has_mk8s_gpu_stack_compatibility_preflight_targets",
    "has_mk8s_resource_name_preflight_targets",
    "validate_mk8s_gpu_stack_compatibility_preflight",
    "validate_mk8s_resource_name_preflight",
    "validate_vpc_networking_preflight",
]
