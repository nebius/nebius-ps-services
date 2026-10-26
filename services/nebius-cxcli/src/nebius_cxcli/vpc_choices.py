"""Identity-aware presentation of live resources and configured VPC bindings."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .component_instances import (
    INSTANCE_ID_PATTERN,
    component_instance_id,
    component_type_id,
    normalize_component_token,
)
from .components import ComponentEntry
from .provider_options import OptionChoice, ProviderOptionLookup
from .vpc_identity import VpcIdentitySnapshot


@dataclass(frozen=True)
class VpcChoiceResult:
    choices: tuple[OptionChoice, ...]
    diagnostics: tuple[str, ...] = ()


def _cidrs(choice: OptionChoice) -> set[str]:
    values = choice.metadata.get("private_cidrs", ())
    if not isinstance(values, Sequence) or isinstance(values, str):
        return set()
    result: set[str] = set()
    for value in values:
        try:
            result.add(str(ipaddress.ip_network(value, strict=False)))
        except (TypeError, ValueError):
            continue
    return result


def reconcile_vpc_choices(
    *,
    live: Sequence[OptionChoice],
    configured: Sequence[OptionChoice],
    snapshot: VpcIdentitySnapshot,
    project_id: str,
    network_id: str = "",
    discovery_complete: bool = True,
) -> VpcChoiceResult:
    choices = list(live)
    diagnostics: list[str] = []
    claimed: dict[str, int] = {}
    identities = []
    for candidate in configured:
        instance = str(candidate.metadata["instance_id"])
        key = candidate.metadata.get("subnet_key")
        identity = (
            snapshot.subnets.get((instance, str(key)))
            if key is not None
            else snapshot.networks.get(instance)
        )
        identities.append(identity)
        if identity is not None:
            claimed[identity.id] = claimed.get(identity.id, 0) + 1
    for candidate, identity in zip(configured, identities, strict=True):
        instance = str(candidate.metadata["instance_id"])
        key = candidate.metadata.get("subnet_key")
        origin = f"infra:vpc@{instance}" + (f".subnets.{key}" if key is not None else "")
        reason = ""
        if not discovery_complete:
            reason = "live discovery is incomplete"
        elif snapshot.status not in {"fresh", "available"}:
            reason = snapshot.diagnostic or "recorded resource identity is unresolved"
        elif identity is not None:
            matches = [choice for choice in live if choice.value == identity.id]
            if claimed[identity.id] != 1 or len(matches) != 1:
                reason = "recorded resource is missing or has ambiguous ownership"
            elif identity.parent_id != project_id or (
                key is not None and network_id and identity.network_id != network_id
            ):
                reason = "recorded resource belongs to a different project or network"
            else:
                observed = matches[0]
                if observed.metadata.get("parent_id") != project_id or (
                    key is not None and observed.metadata.get("network_id") != identity.network_id
                ):
                    reason = "live resource scope does not match recorded identity"
                else:
                    choices = [choice for choice in choices if choice.value != identity.id]
                    choices.append(
                        OptionChoice(
                            value=candidate.value,
                            label=f"{observed.label}  (existing; managed by {origin})",
                            recommended=observed.recommended,
                            metadata={
                                **candidate.metadata,
                                **observed.metadata,
                                "planned": False,
                                "lifecycle": "existing",
                                "binding_ref": candidate.value,
                                "live_id": identity.id,
                            },
                        )
                    )
                    continue
        else:
            # These are collision indicators only, never ownership evidence.
            if any(
                (
                    candidate.metadata.get("name")
                    and candidate.metadata["name"] == choice.metadata.get("name")
                )
                or bool(_cidrs(candidate) & _cidrs(choice))
                for choice in live
            ):
                reason = "a live resource has a matching name or CIDR; ownership is unverified"
            else:
                choices.append(candidate)
                continue
        diagnostics.append(
            f"{origin}: {reason}. Resolve recorded identity before selecting its binding."
        )
    return VpcChoiceResult(tuple(choices), tuple(dict.fromkeys(diagnostics)))


PLANNED_BINDING_TOKEN_PREFIX = "planned:"


def _text(value: object | None) -> str:
    return "" if value is None else str(value).strip()


@dataclass(frozen=True)
class PlannedBindingRef:
    source_instance: str
    source_output: str
    key: str | None = None
    attribute: str | None = None


def parse_planned_vpc_ref(value: str, *, option_name: str, field_name: str) -> PlannedBindingRef:
    token = value.strip()
    if token.startswith(PLANNED_BINDING_TOKEN_PREFIX):
        token = token[len(PLANNED_BINDING_TOKEN_PREFIX) :]
    component_selector, separator, output_selector = token.partition(".")
    component_raw, instance_separator, instance_raw = component_selector.partition("@")
    component_id = normalize_component_token(component_raw)
    instance_id = normalize_component_token(instance_raw) if instance_separator else ""
    if component_id != "vpc" or not instance_separator or not instance_id:
        raise RuntimeError(
            f"Invalid {option_name} value '{value}'. Expected 'vpc@<vpc-instance>.<output>'."
        )
    if not INSTANCE_ID_PATTERN.fullmatch(instance_id):
        raise RuntimeError(
            f"Invalid {option_name} value '{value}'. VPC instance id must use lowercase letters, digits, and hyphens."
        )
    output_parts = [part.strip() for part in output_selector.split(".") if part.strip()]
    if not separator or not output_parts:
        raise RuntimeError(
            f"Invalid {option_name} value '{value}'. Expected 'vpc@<vpc-instance>.<output>'."
        )
    output_name = output_parts[0].replace("-", "_").lower()
    if field_name == "network_id":
        if output_name != "network_id" or len(output_parts) != 1:
            raise RuntimeError(
                f"Invalid {option_name} value '{value}'. Network refs must use "
                "'vpc@<vpc-instance>.network_id'."
            )
        return PlannedBindingRef(source_instance=instance_id, source_output="network_id")
    if output_name != "subnets" or len(output_parts) != 3:
        raise RuntimeError(
            f"Invalid {option_name} value '{value}'. Subnet refs must use "
            "'vpc@<vpc-instance>.subnets.<subnet-key>.id'."
        )
    subnet_key = output_parts[1]
    attribute = output_parts[2].replace("-", "_")
    if not subnet_key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", attribute):
        raise RuntimeError(
            f"Invalid {option_name} value '{value}'. Subnet key and attribute must be non-empty."
        )
    if attribute != "id":
        raise RuntimeError(
            f"Invalid {option_name} value '{value}'. Subnet refs for subnet_id must end in '.id'."
        )
    return PlannedBindingRef(
        source_instance=instance_id,
        source_output="subnets",
        key=subnet_key,
        attribute=attribute,
    )


def planned_vpc_rows(payload: dict[str, Any]) -> list[tuple[dict[str, Any], str, dict[str, Any]]]:
    infra = payload.get("infra")
    components = infra.get("components") if isinstance(infra, Mapping) else None
    if not isinstance(components, list):
        return []
    rows: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    for row in components:
        if not isinstance(row, dict) or not bool(row.get("enabled", False)):
            continue
        if component_type_id(row) != "vpc":
            continue
        instance_id = component_instance_id(row)
        if not instance_id:
            continue
        inputs = row.get("inputs")
        if not isinstance(inputs, dict):
            inputs = {}
        rows.append((row, instance_id, inputs))
    return rows


def planned_network_choice(instance_id: str, inputs: Mapping[str, Any]) -> OptionChoice:
    network = inputs.get("network")
    name = ""
    if isinstance(network, Mapping):
        name = _text(network.get("name"))
    label_name = name or instance_id
    return OptionChoice(
        value=f"{PLANNED_BINDING_TOKEN_PREFIX}vpc@{instance_id}.network_id",
        label=f"planned: {label_name}  (infra:vpc@{instance_id})",
        metadata={
            "planned": True,
            "component": "vpc",
            "instance_id": instance_id,
            "name": label_name,
            "lifecycle": "planned",
        },
    )


def planned_subnet_choices_for_vpc(
    *,
    instance_id: str,
    inputs: Mapping[str, Any],
) -> list[OptionChoice]:
    subnets = inputs.get("subnets")
    if not isinstance(subnets, Mapping):
        return []
    choices: list[OptionChoice] = []
    for subnet_key, subnet_value in subnets.items():
        key = str(subnet_key).strip()
        if not key:
            continue
        name = ""
        cidrs: list[str] = []
        if isinstance(subnet_value, Mapping):
            name = _text(subnet_value.get("name"))
            raw_cidrs = subnet_value.get("ipv4_private_cidrs")
            if isinstance(raw_cidrs, list):
                cidrs = [str(cidr).strip() for cidr in raw_cidrs if str(cidr).strip()]
        suffix = f" ({', '.join(cidrs)})" if cidrs else ""
        label_name = name or key
        choices.append(
            OptionChoice(
                value=(f"{PLANNED_BINDING_TOKEN_PREFIX}vpc@{instance_id}.subnets.{key}.id"),
                label=f"planned: {label_name}  (infra:vpc@{instance_id}.subnets.{key}){suffix}",
                metadata={
                    "planned": True,
                    "component": "vpc",
                    "instance_id": instance_id,
                    "subnet_key": key,
                    "private_cidrs": tuple(cidrs),
                    "name": label_name,
                    "lifecycle": "planned",
                },
            )
        )
    return choices


def combined_vpc_choices(
    *,
    read_field: Callable[[dict[str, Any], str], Any],
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
    provider: str,
    args: Mapping[str, Any],
    live_choices: list[OptionChoice],
    snapshot: VpcIdentitySnapshot | None = None,
    provider_lookup: ProviderOptionLookup | None = None,
    discovery_complete: bool = True,
) -> list[OptionChoice]:
    snapshot = snapshot or VpcIdentitySnapshot()
    planned: list[OptionChoice] = []
    selected_live_network = ""
    if provider == "project_networks":
        include_planned_networks = not (
            entry.scope == "infra"
            and entry.id == "vpc"
            and full_path_label.endswith(".inputs.network.existing_id")
        )
        if include_planned_networks:
            for _row, instance_id, inputs in planned_vpc_rows(payload):
                network = inputs.get("network")
                existing_id = ""
                if isinstance(network, Mapping):
                    existing_id = _text(network.get("existing_id"))
                if existing_id:
                    continue
                planned.append(planned_network_choice(instance_id, inputs))
    elif provider == "project_subnets":
        network_id_path = _text(args.get("network_id_path"))
        selected_network = (
            _text(read_field(payload, network_id_path))
            if network_id_path
            else _text(args.get("network_id"))
        )
        if selected_network:
            selected_live_network = selected_network
            selected_planned_ref: PlannedBindingRef | None = None
            if selected_network.startswith(PLANNED_BINDING_TOKEN_PREFIX):
                try:
                    selected_planned_ref = parse_planned_vpc_ref(
                        selected_network,
                        option_name="planned network choice",
                        field_name="network_id",
                    )
                except RuntimeError:
                    selected_planned_ref = None
                if selected_planned_ref is not None:
                    identity = snapshot.networks.get(selected_planned_ref.source_instance)
                    selected_live_network = identity.id if identity is not None else ""
            for _row, instance_id, inputs in planned_vpc_rows(payload):
                network = inputs.get("network")
                existing_id = ""
                if isinstance(network, Mapping):
                    existing_id = _text(network.get("existing_id"))
                if selected_planned_ref is not None:
                    if instance_id != selected_planned_ref.source_instance:
                        continue
                elif (
                    existing_id
                    or (
                        snapshot.networks[instance_id].id
                        if instance_id in snapshot.networks
                        else ""
                    )
                ) != selected_network:
                    continue
                planned.extend(
                    planned_subnet_choices_for_vpc(instance_id=instance_id, inputs=inputs)
                )
    result = reconcile_vpc_choices(
        live=live_choices,
        configured=planned,
        snapshot=snapshot,
        project_id=_text(read_field(payload, "client_info.nebius.project_id")),
        network_id=selected_live_network,
        discovery_complete=discovery_complete,
    )
    if provider_lookup is not None:
        provider_lookup.record_vpc_diagnostics(full_path_label, result.diagnostics)
    return list(result.choices)


def resolve_wizard_provider_choices(
    *,
    read_field: Callable[[dict[str, Any], str], Any],
    payload: dict[str, Any],
    entry: ComponentEntry,
    full_path_label: str,
    provider: str,
    args: Mapping[str, Any],
    provider_lookup: ProviderOptionLookup,
) -> list[OptionChoice]:
    vpc_provider = provider in {"project_networks", "project_subnets"}
    snapshot = (
        provider_lookup.vpc_identity(payload)
        if vpc_provider and planned_vpc_rows(payload)
        else VpcIdentitySnapshot()
    )
    live_args = dict(args)
    if provider == "project_subnets":
        path = _text(args.get("network_id_path"))
        selected = _text(args.get("network_id")) or (
            _text(read_field(payload, path)) if path else ""
        )
        if selected.startswith(PLANNED_BINDING_TOKEN_PREFIX):
            ref = parse_planned_vpc_ref(
                selected, option_name="network choice", field_name="network_id"
            )
            identity = snapshot.networks.get(ref.source_instance)
            if identity is not None:
                live_args["network_id"] = identity.id
    live = provider_lookup.resolve(
        provider=provider, args=live_args, payload=payload, field_path=full_path_label
    )
    if not vpc_provider:
        return live
    return combined_vpc_choices(
        read_field=read_field,
        payload=payload,
        entry=entry,
        full_path_label=full_path_label,
        provider=provider,
        args=args,
        live_choices=live,
        snapshot=snapshot,
        provider_lookup=provider_lookup,
        discovery_complete=provider_lookup.vpc_discovery_complete(),
    )
