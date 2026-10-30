"""Live VPN destination routes and the focused WireGuard wizard prompt."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

from rich.markup import escape

from .provider_options import OptionChoice, ProviderOptionLookup
from .vpc_choices import parse_planned_vpc_ref
from .wireguard_clients import normalize_local_subnets
from .wireguard_secret_api import wireguard_api


def live_subnet_choices(project_id: str, network_id: str) -> list[OptionChoice]:
    """Return CIDRs, not subnet IDs; shared pools remain explicitly labelled."""
    from nebius.api.nebius.vpc.v1 import (
        GetNetworkRequest,
        ListSubnetsRequest,
        NetworkServiceClient,
        SubnetServiceClient,
    )

    if not project_id or not network_id or network_id.startswith("planned:"):
        raise ValueError("Live subnet discovery requires exact project and VPC identities")
    with wireguard_api() as api:
        network = api.call(NetworkServiceClient(api.sdk).get, GetNetworkRequest(id=network_id))
        if network.metadata.id != network_id or network.metadata.parent_id != project_id:
            raise ValueError("Selected VPC does not belong to the selected project")
        subnets = api.pages(SubnetServiceClient(api.sdk).list, ListSubnetsRequest, project_id)

    routes: dict[str, set[str]] = {}
    for subnet in subnets:
        if subnet.spec.network_id != network_id:
            continue
        pools = subnet.spec.ipv4_private_pools
        shared = not subnet.spec.check_presence("ipv4_private_pools") or pools.use_network_pools
        label = f"{subnet.metadata.name or '(unnamed)'} — {subnet.metadata.id}"
        if shared:
            label += " — shared network pool (may include other subnets)"
        for pool in subnet.status.ipv4_private_pools:
            for raw in pool.cidrs:
                # Status must contain a resolved IPv4 range, never an allocation prefix.
                if "/" not in raw:
                    raise ValueError("Subnet discovery returned an unresolved IPv4 range")
                cidr = ipaddress.ip_network(raw, strict=True)
                if cidr.version != 4:
                    raise ValueError("Subnet discovery returned a non-IPv4 range")
                routes.setdefault(str(cidr), set()).add(label)
    return [
        OptionChoice(cidr, escape(f"{cidr} — {'; '.join(sorted(routes[cidr]))}"))
        for cidr in sorted(
            routes, key=lambda value: (int(ipaddress.ip_network(value).network_address), value)
        )
    ]


def selected_network_id(
    row: Mapping[str, Any], payload: dict[str, Any], lookup: ProviderOptionLookup | None
) -> str:
    """Resolve literal, transient wizard and persisted managed-VPC selections."""
    selected = row.get("inputs", {}).get("network_id") or ""
    binding = row.get("bindings", {}).get("inputs.network_id")
    if binding:
        if binding.get("source_component") != "vpc" or binding.get("source_output") != "network_id":
            return ""
        selected = f"planned:vpc@{binding['source_instance']}.network_id"
    if not selected.startswith("planned:"):
        return selected
    ref = parse_planned_vpc_ref(selected, option_name="VPN network", field_name="network_id")
    project = payload.get("client_info", {}).get("nebius", {}).get("project_id")
    if lookup is not None:
        snapshot = lookup.vpc_identity(payload)
        identity = snapshot.networks.get(ref.source_instance)
        if (
            snapshot.status in {"fresh", "available"}
            and identity is not None
            and identity.parent_id == project
        ):
            return identity.id
    return ""


def prompt_local_subnets(
    *,
    current: object,
    row: Mapping[str, Any],
    payload: dict[str, Any],
    lookup: ProviderOptionLookup | None,
) -> tuple[object, bool]:
    from . import cli

    cli.console.print(
        "Select private destination networks VPN clients should reach (client AllowedIPs). "
        "This is separate from the gateway's placement subnet. "
        "No destinations selected means VPN tunnel access only."
    )
    if current is None:
        current = []
    if not isinstance(current, list):
        raise ValueError("Saved VPN destinations must be a list of IPv4 CIDRs")
    saved = list(dict.fromkeys(normalize_local_subnets(current)))
    choices: list[OptionChoice] = []
    try:
        project = payload.get("client_info", {}).get("nebius", {}).get("project_id", "")
        network = selected_network_id(row, payload, lookup)
        if not project or not network:
            cli.console.print(
                "The selected project/VPC has no resolved live identity yet. "
                "Enter known destination CIDRs manually or keep saved routes."
            )
        else:
            choices = live_subnet_choices(project, network)
            if not choices:
                cli.console.print(
                    "No resolved private IPv4 subnet ranges found in the selected VPC."
                )
    except Exception:
        # Provider exceptions can contain credentials or private endpoint details.
        cli.console.print(
            "Live subnet discovery failed; check VPC network-get/subnet-list access. "
            "The live inventory is unavailable. Saved routes and explicit manual entry remain available."
        )
    values = {choice.value for choice in choices}
    choices.extend(
        OptionChoice(value, escape(f"{value} — saved route; not in the live subnet listing"))
        for value in saved
        if value not in values
    )
    choices = [
        OptionChoice(choice.value, choice.label, recommended=choice.value in saved)
        for choice in choices
    ]
    manual = "__manual__"
    clear = "__clear__"
    choices.append(OptionChoice(manual, "Enter additional destination IPv4 CIDRs manually"))
    choices.append(OptionChoice(clear, "Tunnel access only — clear destination routes"))
    # Current selections are explicit recommendations. Passing [] lets a user clear
    # all routes, while avoiding the generic required-list select-everything default.
    while True:
        selected, stop = cli._prompt_multi_choice_override(
            path_label="VPN destination subnets",
            current=[],
            choices=choices,
            type_hint="list(string)",
            required=False,
        )
        if stop or cli._wizard_backtrack_requested(selected):
            return selected, stop
        if not isinstance(selected, list):
            raise ValueError("VPN destination selection must be a list")
        if clear not in selected:
            break
        if len(selected) == 1:
            return [], False
        cli.console.print("Select tunnel-only access by itself, without destination routes.")
    if manual in selected:
        selected.remove(manual)
        while True:
            entered, stop = cli._prompt_scalar_override(
                "Additional destination IPv4 CIDRs", [], type_hint="list(string)"
            )
            if stop or cli._wizard_backtrack_requested(entered):
                return entered, stop
            try:
                if not isinstance(entered, list):
                    raise ValueError("VPN destinations must be a list")
                selected.extend(normalize_local_subnets(entered))
                break
            except ValueError:
                cli.console.print("Enter valid IPv4 CIDRs, separated by commas.")
    return list(dict.fromkeys(selected)), False
