"""Nebius MK8s deployment-readiness checks against live subnet state."""

from __future__ import annotations

import ipaddress
from collections.abc import Collection, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from nebius.api.nebius.common.v1 import GetByNameRequest
from nebius.api.nebius.compute.v1 import GpuClusterServiceClient
from nebius.api.nebius.mk8s.v1 import ClusterServiceClient
from nebius.api.nebius.vpc.v1 import GetSubnetRequest, SubnetServiceClient

from .component_defaults import resolve_component_defaults
from .component_instances import component_instance_id, component_instance_label, component_type_id
from .components import component_lookup
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
    return (
        "not found" in message
        or "not_found" in message
        or "statuscode.not_found" in message
    )


@dataclass(frozen=True)
class _Mk8sResolvedComponent:
    component_label: str
    component_id: str
    project_id: str
    inputs: Mapping[str, Any]


def _resolved_mk8s_components(payload: Mapping[str, Any]) -> tuple[_Mk8sResolvedComponent, ...]:
    infra = payload.get("infra")
    if not isinstance(infra, Mapping):
        return ()
    components = infra.get("components")
    if not isinstance(components, list):
        return ()

    entry_by_id = component_lookup("infra")
    default_project_id = _as_text(payload.get("client_info", {}).get("nebius", {}).get("project_id"))
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
        resolved_components.append(
            _Mk8sResolvedComponent(
                component_label=component_instance_label(component_id, instance_id),
                component_id=component_id,
                project_id=_as_text(inputs.get("parent_id")) or default_project_id,
                inputs=inputs,
            )
        )
    return tuple(resolved_components)


def has_mk8s_resource_name_preflight_targets(config: Any) -> bool:
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        return False
    for component in _resolved_mk8s_components(payload):
        cluster_name = _as_text(component.inputs.get("cluster_name"))
        if not cluster_name or not component.project_id:
            continue
        if bool(component.inputs.get("gpu_enabled", False)) and _as_text(
            component.inputs.get("infiniband_fabric")
        ):
            return True
        return True
    return False


def _service_cidr_prefix_lengths(raw_value: Any) -> tuple[int, ...]:
    if raw_value is None:
        return ()
    if not isinstance(raw_value, (list, tuple)):
        raise RuntimeError(
            "inputs.kube_network_service_cidrs must be a list of CIDR "
            'strings or prefix-length strings such as ["/20"].'
        )

    prefixes: list[int] = []
    for item in raw_value:
        text = _as_text(item)
        if not text:
            raise RuntimeError("inputs.kube_network_service_cidrs cannot contain empty values.")
        try:
            if text.startswith("/"):
                prefix = int(text[1:])
            else:
                prefix = ipaddress.ip_network(text, strict=False).prefixlen
        except Exception as exc:
            raise RuntimeError(
                f"inputs.kube_network_service_cidrs contains an invalid value: {text!r}"
            ) from exc
        prefixes.append(prefix)

    if len(prefixes) != 1:
        raise RuntimeError(
            "inputs.kube_network_service_cidrs must contain exactly one CIDR or prefix value."
        )
    return tuple(prefixes)


def _subnet_pool_cidrs(subnet: Any) -> tuple[str, ...]:
    spec = getattr(subnet, "spec", None)
    ipv4_private_pools = getattr(spec, "ipv4_private_pools", None)
    pools = list(getattr(ipv4_private_pools, "pools", []) or [])
    cidrs: list[str] = []
    for pool in pools:
        for cidr in list(getattr(pool, "cidrs", []) or []):
            value = _as_text(getattr(cidr, "cidr", None))
            if value:
                cidrs.append(value)
    return tuple(cidrs)


def validate_mk8s_network_preflight(config: Any) -> None:
    """Fail fast on known MK8s subnet/service-CIDR combinations that stall provisioning."""
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping):
        return
    sdk = None
    try:
        for component in _resolved_mk8s_components(payload):
            inputs = component.inputs
            subnet_id = _as_text(inputs.get("subnet_id"))
            if not subnet_id:
                continue

            raw_service_cidrs = inputs.get("kube_network_service_cidrs", ["/16"])
            service_prefixes = _service_cidr_prefix_lengths(raw_service_cidrs)
            if not service_prefixes:
                continue

            if sdk is None:
                sdk = init_nebius_sdk(
                    parent_id=component.project_id or None, context="MK8s network preflight"
                )
            subnet_client = SubnetServiceClient(sdk)
            subnet = subnet_client.get(GetSubnetRequest(id=subnet_id)).wait()
            pool_cidrs = _subnet_pool_cidrs(subnet)
            if len(pool_cidrs) != 1:
                continue

            pool_cidr = pool_cidrs[0]
            try:
                pool_prefix = ipaddress.ip_network(pool_cidr, strict=False).prefixlen
            except Exception:
                continue

            service_prefix = service_prefixes[0]
            if service_prefix <= pool_prefix:
                raise RuntimeError(
                    "MK8s network preflight failed: "
                    f"component '{component.component_label}' inputs.kube_network_service_cidrs="
                    f"{list(raw_service_cidrs)!r} "
                    f"is too large for subnet {subnet_id} pool {pool_cidr}. "
                    "Nebius reserves Kubernetes service CIDRs from the same subnet pool, so this "
                    "single-pool subnet can stall cluster provisioning before any node groups are created. "
                    'Use a smaller service CIDR such as ["/20"] or choose a larger subnet.'
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
            cluster_name = _as_text(component.inputs.get("cluster_name"))
            if not project_id or not cluster_name:
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

            if cluster_name not in managed_mk8s:
                try:
                    mk8s_client.get_by_name(
                        GetByNameRequest(parent_id=project_id, name=cluster_name)
                    ).wait()
                except Exception as exc:
                    if not _is_not_found_error(exc):
                        raise
                else:
                    raise RuntimeError(
                        "MK8s resource-name preflight failed: "
                        f"component '{component.component_label}' resolves cluster name "
                        f"'{cluster_name}' in project {project_id}, but a live Nebius MK8s cluster "
                        "with that name already exists and is not tracked in the current Terraform state. "
                        "If this is leftover from an earlier failed run, delete the live cluster and retry. "
                        "Otherwise import it into Terraform state or change inputs.cluster_name and rerender."
                    )

            gpu_cluster_name = (
                f"{cluster_name}-gpu-cluster"
                if bool(component.inputs.get("gpu_enabled", False))
                and _as_text(component.inputs.get("infiniband_fabric"))
                else ""
            )
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
                    "Otherwise import it into Terraform state or change inputs.cluster_name and rerender."
                )
    finally:
        for sdk in sdk_by_project.values():
            with suppress(Exception):
                sdk.sync_close()


__all__ = [
    "has_mk8s_resource_name_preflight_targets",
    "validate_mk8s_network_preflight",
    "validate_mk8s_resource_name_preflight",
]
