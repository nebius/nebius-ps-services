"""Nebius MK8s deployment-readiness checks against live subnet state."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from nebius.api.nebius.vpc.v1 import GetSubnetRequest, SubnetServiceClient

from .component_defaults import resolve_component_defaults
from .components import component_lookup
from .runtime_config import to_plain_data
from .sdk_auth import init_nebius_sdk


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


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
            raise RuntimeError(
                "inputs.kube_network_service_cidrs cannot contain empty values."
            )
        try:
            if text.startswith("/"):
                prefix = int(text[1:])
            else:
                prefix = ipaddress.ip_network(text, strict=False).prefixlen
        except Exception as exc:
            raise RuntimeError(
                "inputs.kube_network_service_cidrs contains an invalid "
                f"value: {text!r}"
            ) from exc
        prefixes.append(prefix)

    if len(prefixes) != 1:
        raise RuntimeError(
            "inputs.kube_network_service_cidrs must contain exactly one "
            "CIDR or prefix value."
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

    infra = payload.get("infra")
    if not isinstance(infra, Mapping):
        return
    components = infra.get("components")
    if not isinstance(components, list):
        return

    entry_by_id = component_lookup("infra")

    project_id = _as_text(payload.get("client_info", {}).get("nebius", {}).get("project_id"))
    sdk = None
    try:
        for item in components:
            if not isinstance(item, Mapping) or not bool(item.get("enabled", False)):
                continue
            component_id = _as_text(item.get("id")).lower()
            mk8s_entry = entry_by_id.get(component_id)
            if mk8s_entry is None or getattr(mk8s_entry, "validation_profile", "") != "mk8s_cluster":
                continue

            resolved = resolve_component_defaults(
                payload=payload,
                component_node=dict(item),
                entry=mk8s_entry,
            )
            inputs = resolved.get("inputs")
            if not isinstance(inputs, Mapping):
                continue

            subnet_id = _as_text(inputs.get("subnet_id"))
            if not subnet_id:
                continue

            raw_service_cidrs = inputs.get("kube_network_service_cidrs", ["/16"])
            service_prefixes = _service_cidr_prefix_lengths(raw_service_cidrs)
            if not service_prefixes:
                continue

            if sdk is None:
                sdk = init_nebius_sdk(parent_id=project_id or None, context="MK8s network preflight")
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
                    f"component '{component_id}' inputs.kube_network_service_cidrs="
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


__all__ = ["validate_mk8s_network_preflight"]
