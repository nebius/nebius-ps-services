"""Bind route-plan computed defaults to the frozen canonical VPC configuration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .component_defaults import resolve_component_defaults
from .component_sources import (
    DEFAULT_COMPONENT_SOURCES_FILE,
    SourceProfile,
    _load_bundled_component_sources,
    tf_module_source_by_id,
)
from .component_wiring import row_input_bindings
from .components import component_lookup
from .runtime_config import to_plain_data
from .runtime_introspection import resolve_module_source_path


@dataclass(frozen=True)
class SubnetActivationContract:
    address: str
    inherited_public_pools: bool


def activation_subnet_contracts(
    config: Any, manifest: Mapping[str, Any], records: Mapping[str, Any]
) -> dict[str, SubnetActivationContract]:
    """Custom modules or unresolved subnet bindings receive no computed-pool exemptions."""
    render = manifest.get("render", {})
    sources = [r for r in render.get("module_sources", []) if r.get("component_id") == "vpc"]
    if not sources:
        return {}
    profile = SourceProfile(render.get("source_profile", "portable"))
    canonical = tf_module_source_by_id(
        "vpc", sources=_load_bundled_component_sources(source_profile=profile)
    )
    entry = component_lookup("infra", source_profile=profile).get("vpc")
    if canonical is None or entry is None:
        return {}
    expected_local = (
        DEFAULT_COMPONENT_SOURCES_FILE.parent / (canonical.local_source or "")
    ).resolve()
    payload = to_plain_data(config)
    rows = {
        row["instance_id"]: row
        for row in payload["infra"]["components"]
        if row.get("id") == "vpc" and row.get("enabled", True)
    }
    contracts = {}
    for source in sources:
        actual_source = source.get("source", "")
        if actual_source != canonical.portable_source and (
            not canonical.local_source
            or resolve_module_source_path(actual_source) != expected_local
        ):
            continue
        row = rows.get(source["instance_id"])
        if row is None:
            continue
        row = resolve_component_defaults(
            payload=payload,
            component_node=row,
            entry=entry,
            preserve_existing_literal=True,
            preserve_existing_shared=False,
            include_shared=False,
        )
        bindings = (*entry.input_bindings, *row_input_bindings(row))
        if any(
            binding.target_path in {"inputs", "inputs.subnets"}
            or binding.target_path.startswith("inputs.subnets.")
            for binding in bindings
        ):
            continue
        for record in records.values():
            for target in record.get("targets", {}).values():
                if (
                    target.get("owner") != "terraform"
                    or target.get("vpc_instance") != source["instance_id"]
                ):
                    continue
                key = target["subnet_key"]
                subnet = row.get("inputs", {}).get("subnets", {}).get(key)
                if not isinstance(subnet, dict):
                    continue
                public_cidrs = subnet.get("ipv4_public_cidrs")
                use_network_pools = subnet.get("use_network_public_pools")
                # Terraform optional(type, default) replaces explicit null as well as omission.
                public_cidrs = [] if public_cidrs is None else public_cidrs
                use_network_pools = True if use_network_pools is None else use_network_pools
                inherited = (
                    use_network_pools is True
                    and isinstance(public_cidrs, list)
                    and all(isinstance(cidr, str) and not cidr.strip() for cidr in public_cidrs)
                )
                contracts[target["subnet_id"]] = SubnetActivationContract(
                    address=f"module.{source['module_name']}.nebius_vpc_v1_subnet.this[{json.dumps(key)}]",
                    inherited_public_pools=inherited,
                )
    return contracts
