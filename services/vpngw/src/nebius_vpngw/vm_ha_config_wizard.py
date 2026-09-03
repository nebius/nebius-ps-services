"""Guided conversion of an ordinary VPN gateway config to explicit VM-HA."""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import re
import typing as t
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config_loader import _expand_env
from .config_wizard import (
    _CONTROL_HELP,
    WizardCancelled,
    WizardValidationError,
    _Back,
    _default_apipa,
    _Prompter,
    _validate_apipa_cidr,
    _validate_env_name,
    _validate_ip,
    _validate_name,
)
from .schema import VPNGatewayConfig
from .vm_ha_credentials import display_vm_ha_credential_path


@dataclass(frozen=True)
class VMHAConversionResult:
    """Outcome of the ordinary-to-VM-HA wizard without hidden draft state."""

    yaml_text: str | None
    candidate: dict[str, t.Any] | None
    passive_ip: str | None
    passive_ip_reserved: bool
    peer_ready: bool
    member_one_tunnel_count: int


_VM_HA_TUNNEL_CHANGED_FIELDS = frozenset(
    {
        "name",
        "gateway_instance_index",
        "remote_public_ip",
        "psk",
        "inner_cidr",
        "inner_local_ip",
        "inner_remote_ip",
    }
)


def _bounded_name(base: str, suffix: str) -> str:
    candidate = f"{base}{suffix}"
    if len(candidate) <= 64:
        return candidate
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:8]
    prefix_length = 64 - len(suffix) - len(digest) - 1
    prefix = base[:prefix_length].rstrip("-")
    return f"{prefix}-{digest}{suffix}"


def _unique_tunnel_name(source_name: str, used_names: set[str]) -> str:
    suffix_number = 1
    while True:
        suffix = "-gw2" if suffix_number == 1 else f"-gw2-{suffix_number}"
        candidate = _bounded_name(source_name, suffix)
        if candidate not in used_names:
            return candidate
        suffix_number += 1


def _default_psk_env_name(tunnel_name: str, used_names: set[str]) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", tunnel_name.upper()).strip("_") or "VPN_TUNNEL"
    if base[0].isdigit():
        base = f"VPN_{base}"
    base = base[:52].rstrip("_")
    suffix_number = 1
    while True:
        suffix = "_PSK" if suffix_number == 1 else f"_{suffix_number}_PSK"
        candidate = f"{base[: 64 - len(suffix)].rstrip('_')}{suffix}"
        if candidate not in used_names:
            return candidate
        suffix_number += 1


def _placeholder_name(value: t.Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\$\{([A-Z_][A-Z0-9_]*)\}", value.strip())
    return match.group(1) if match else None


def resolve_vm_ha_conversion_source(raw: dict[str, t.Any]) -> dict[str, t.Any]:
    """Build a validation-only view without resolving or serializing environment values."""

    missing: set[str] = set()
    view = t.cast(dict[str, t.Any], _expand_env(copy.deepcopy(raw), missing))
    group = view.get("gateway_group") or {}
    external_ips = group.get("external_ips")
    replacement_octet = 10
    if isinstance(external_ips, list):
        for row in external_ips:
            if not isinstance(row, list):
                continue
            for index, value in enumerate(row):
                if _placeholder_name(value):
                    row[index] = f"192.0.2.{replacement_octet}"
                    replacement_octet += 1

    remote_octet = 10
    for connection in view.get("connections") or []:
        for tunnel in connection.get("tunnels") or []:
            if _placeholder_name(tunnel.get("remote_public_ip")):
                tunnel["remote_public_ip"] = f"198.51.100.{remote_octet}"
                remote_octet += 1
            if _placeholder_name(tunnel.get("psk")):
                tunnel["psk"] = "validation-placeholder-secret"
    return view


def _validate_raw_candidate(raw: dict[str, t.Any]) -> None:
    try:
        VPNGatewayConfig.model_validate(resolve_vm_ha_conversion_source(raw))
    except ValidationError as error:
        messages = []
        for item in error.errors(include_input=False, include_url=False):
            location = ".".join(str(part) for part in item["loc"])
            messages.append(f"{location}: {item['msg']}")
        raise WizardValidationError("; ".join(messages)) from error


def validate_vm_ha_conversion_source(raw: dict[str, t.Any]) -> None:
    """Admit only one ordinary schema-v1 gateway with no inferred VM-HA state."""

    if raw.get("version") != 1:
        raise WizardValidationError("vm-ha supports only configuration schema version 1.")
    group = raw.get("gateway_group")
    if not isinstance(group, dict):
        raise WizardValidationError("gateway_group must be a YAML mapping.")
    if group.get("instance_count") != 1:
        raise WizardValidationError("vm-ha requires an ordinary gateway with instance_count: 1.")
    vm_ha = group.get("vm_ha")
    if vm_ha is not None:
        if not isinstance(vm_ha, dict) or vm_ha.get("enabled") is not False:
            raise WizardValidationError(
                "The source already enables or partially defines VM-HA; use validate-config and apply instead."
            )
        allowed_disabled = {"enabled", "cluster_id", "members"}
        if (
            set(vm_ha) - allowed_disabled
            or vm_ha.get("cluster_id") not in (None, "")
            or (vm_ha.get("members") or [])
        ):
            raise WizardValidationError(
                "The source contains a partial disabled VM-HA block; remove the partial fields first."
            )
    connections = raw.get("connections")
    if not isinstance(connections, list) or not connections:
        raise WizardValidationError("The source must contain at least one configured connection.")
    for connection_index, connection in enumerate(connections):
        tunnels = connection.get("tunnels") if isinstance(connection, dict) else None
        if not isinstance(tunnels, list) or not tunnels:
            raise WizardValidationError(
                f"connections[{connection_index}] must contain at least one tunnel."
            )
        for tunnel_index, tunnel in enumerate(tunnels):
            if not isinstance(tunnel, dict) or tunnel.get("gateway_instance_index") != 0:
                raise WizardValidationError(
                    f"connections[{connection_index}].tunnels[{tunnel_index}] must belong to instance 0."
                )
    _validate_raw_candidate(raw)


def _default_vm_ha_block(gateway_name: str) -> dict[str, t.Any]:
    members: list[dict[str, t.Any]] = []
    for index, role in enumerate(("active", "passive")):
        node_id = _bounded_name(gateway_name, f"-{index}")
        members.append(
            {
                "node_id": node_id,
                "instance_index": index,
                "role": role,
            }
        )
    return {
        "enabled": True,
        "cluster_id": _bounded_name(gateway_name, "-ha"),
        "members": members,
    }


def _validate_vm_ha_identity_block(
    gateway_name: str,
    vm_ha: t.Any,
) -> None:
    if not isinstance(vm_ha, dict):
        raise WizardValidationError("Structural guard rejected the VM-HA identity block.")
    if vm_ha.get("enabled") is not True or vm_ha.get("cluster_id") != _bounded_name(
        gateway_name, "-ha"
    ):
        raise WizardValidationError("Structural guard rejected the VM-HA identity block.")
    members = vm_ha.get("members")
    if not isinstance(members, list) or len(members) != 2:
        raise WizardValidationError("Structural guard rejected the VM-HA identity block.")
    for index, role in enumerate(("active", "passive")):
        member = members[index]
        if not isinstance(member, dict):
            raise WizardValidationError("Structural guard rejected the VM-HA identity block.")
        if member != {
            "node_id": _bounded_name(gateway_name, f"-{index}"),
            "instance_index": index,
            "role": role,
        }:
            raise WizardValidationError("Structural guard rejected the VM-HA identity block.")


def _used_tunnel_state(
    raw: dict[str, t.Any],
    semantic: dict[str, t.Any],
) -> tuple[set[str], set[str], set[str]]:
    names: set[str] = set()
    networks: set[str] = set()
    psk_names: set[str] = set()
    raw_connections = raw.get("connections") or []
    semantic_connections = semantic.get("connections") or []
    for raw_connection, semantic_connection in zip(
        raw_connections,
        semantic_connections,
        strict=True,
    ):
        raw_tunnels = raw_connection.get("tunnels") or []
        semantic_tunnels = semantic_connection.get("tunnels") or []
        for raw_tunnel, semantic_tunnel in zip(raw_tunnels, semantic_tunnels, strict=True):
            names.add(str(semantic_tunnel.get("name") or ""))
            try:
                networks.add(
                    str(ipaddress.ip_network(str(semantic_tunnel.get("inner_cidr")), strict=False))
                )
            except ValueError:
                pass
            placeholder = _placeholder_name(raw_tunnel.get("psk"))
            if placeholder:
                psk_names.add(placeholder)
    return names, networks, psk_names


def _next_unused_apipa(used_networks: set[str]) -> str:
    for number in range(64 * 245):
        candidate = _default_apipa(number)
        if candidate not in used_networks:
            return candidate
    raise WizardValidationError("No unused APIPA /30 remains in the wizard allocation range.")


def _derive_member_one_tunnels(
    raw: dict[str, t.Any],
    semantic: dict[str, t.Any],
    prompt: _Prompter,
) -> list[tuple[dict[str, t.Any], str]]:
    used_names, used_networks, used_psk_names = _used_tunnel_state(raw, semantic)
    derived: list[tuple[dict[str, t.Any], str]] = []
    raw_connections = raw.get("connections") or []
    semantic_connections = semantic.get("connections") or []
    for raw_connection, semantic_connection in zip(
        raw_connections,
        semantic_connections,
        strict=True,
    ):
        raw_tunnels = raw_connection.get("tunnels") or []
        semantic_tunnels = semantic_connection.get("tunnels") or []
        for source_tunnel, semantic_tunnel in zip(raw_tunnels, semantic_tunnels, strict=True):
            source_name = str(semantic_tunnel["name"])
            default_name = _unique_tunnel_name(source_name, used_names)

            def validate_new_name(value: str) -> str:
                value = _validate_name(value)
                if value in used_names:
                    raise ValueError("Tunnel names must be globally unique.")
                return value

            new_name = prompt.ask(
                f"Passive-member counterpart for {source_name}",
                default=default_name,
                help_text=(
                    "This is the new instance-1 tunnel name. Existing instance-0 tunnel names "
                    "and roles remain unchanged."
                ),
                validator=validate_new_name,
            )
            used_names.add(new_name)
            default_env = _default_psk_env_name(new_name, used_psk_names)

            def validate_new_env(value: str) -> str:
                value = _validate_env_name(value)
                if value in used_psk_names:
                    raise ValueError("Use a distinct PSK environment variable for the new tunnel.")
                return value

            psk_env = prompt.ask(
                f"PSK environment variable for {new_name}",
                default=default_env,
                help_text=(
                    "Only the ${NAME} reference is saved. Configure the same secret on the peer; "
                    "the wizard never requests or displays its value."
                ),
                validator=validate_new_env,
            )
            used_psk_names.add(psk_env)
            default_cidr = _next_unused_apipa(used_networks)

            def validate_new_cidr(value: str) -> str:
                value = _validate_apipa_cidr(value)
                if value in used_networks:
                    raise ValueError("Use an APIPA /30 that is not used by another tunnel.")
                return value

            inner_cidr = prompt.ask(
                f"APIPA /30 for {new_name}",
                default=default_cidr,
                help_text="A new globally unique 169.254.0.0/16 /30 used by this peer tunnel.",
                validator=validate_new_cidr,
            )
            used_networks.add(inner_cidr)
            network = ipaddress.ip_network(inner_cidr, strict=False)
            hosts = list(network.hosts())
            local_first = prompt.ask_bool(
                f"Should Nebius use the first host address for {new_name}",
                default=True,
                help_text=(
                    f"Yes assigns {hosts[0]} to Nebius and {hosts[1]} to the peer; "
                    "answer no to reverse the orientation."
                ),
            )
            local_ip, peer_ip = (hosts[0], hosts[1]) if local_first else (hosts[1], hosts[0])
            new_tunnel = t.cast(dict[str, t.Any], copy.deepcopy(source_tunnel))
            new_tunnel.update(
                {
                    "name": new_name,
                    "gateway_instance_index": 1,
                    "remote_public_ip": "",
                    "psk": "${" + psk_env + "}",
                    "inner_cidr": inner_cidr,
                    "inner_local_ip": str(local_ip),
                    "inner_remote_ip": str(peer_ip),
                }
            )
            derived.append((new_tunnel, str(peer_ip)))
    return derived


def _apply_member_one_tunnels(
    candidate: dict[str, t.Any], derived: list[tuple[dict[str, t.Any], str]]
) -> None:
    offset = 0
    for connection in candidate.get("connections") or []:
        original_count = len(connection.get("tunnels") or [])
        additions = [
            copy.deepcopy(tunnel) for tunnel, _ in derived[offset : offset + original_count]
        ]
        connection["tunnels"].extend(additions)
        offset += original_count


def _required_tunnel_quota(
    candidate: dict[str, t.Any],
    semantic_source: dict[str, t.Any],
) -> None:
    total = sum(len(connection.get("tunnels") or []) for connection in candidate["connections"])
    if total > 200:
        raise WizardValidationError(
            "The two-member candidate exceeds the schema maximum of 200 tunnels."
        )
    semantic_gateway = semantic_source.get("gateway") or {}
    semantic_quotas = semantic_gateway.get("quotas") or {}
    configured = int(semantic_quotas.get("max_tunnels", 32))
    if configured >= total:
        return
    gateway = candidate.setdefault("gateway", {})
    quotas = gateway.setdefault("quotas", {})
    quotas["max_tunnels"] = total


def _assert_vm_ha_structural_allowlist(
    source: dict[str, t.Any], candidate: dict[str, t.Any]
) -> None:
    semantic_source = resolve_vm_ha_conversion_source(source)
    source_connections = source.get("connections") or []
    candidate_connections = candidate.get("connections") or []
    if len(source_connections) != len(candidate_connections):
        raise WizardValidationError("Structural guard rejected a connection-list change.")
    for connection_index, (old_connection, new_connection) in enumerate(
        zip(source_connections, candidate_connections, strict=True)
    ):
        old_tunnels = old_connection.get("tunnels") or []
        new_tunnels = new_connection.get("tunnels") or []
        if new_tunnels[: len(old_tunnels)] != old_tunnels:
            raise WizardValidationError(
                f"Structural guard rejected a member-0 change in connections[{connection_index}]."
            )
        additions = new_tunnels[len(old_tunnels) :]
        if len(additions) != len(old_tunnels):
            raise WizardValidationError(
                f"Structural guard requires one member-1 counterpart per tunnel in connections[{connection_index}]."
            )
        for tunnel_index, (old_tunnel, new_tunnel) in enumerate(
            zip(old_tunnels, additions, strict=True)
        ):
            old_fixed = {
                key: value
                for key, value in old_tunnel.items()
                if key not in _VM_HA_TUNNEL_CHANGED_FIELDS
            }
            new_fixed = {
                key: value
                for key, value in new_tunnel.items()
                if key not in _VM_HA_TUNNEL_CHANGED_FIELDS
            }
            if old_fixed != new_fixed or new_tunnel.get("gateway_instance_index") != 1:
                raise WizardValidationError(
                    "Structural guard rejected non-endpoint fields in "
                    f"connections[{connection_index}].tunnels[{len(old_tunnels) + tunnel_index}]."
                )

    group = candidate.get("gateway_group") or {}
    source_group = source.get("gateway_group") or {}
    if group.get("instance_count") != 2:
        raise WizardValidationError("Structural guard requires instance_count: 2.")
    _validate_vm_ha_identity_block(
        str((semantic_source.get("gateway_group") or {}).get("name") or ""),
        group.get("vm_ha"),
    )
    source_external = source_group.get("external_ips")
    source_row_zero = copy.deepcopy(source_external[0]) if source_external else []
    candidate_external = group.get("external_ips")
    if (
        not isinstance(candidate_external, list)
        or len(candidate_external) != 2
        or candidate_external[0] != source_row_zero
        or not isinstance(candidate_external[1], list)
        or len(candidate_external[1]) != 1
    ):
        raise WizardValidationError("Structural guard rejected the passive external-IP row.")

    normalized = t.cast(dict[str, t.Any], copy.deepcopy(candidate))
    normalized_group = normalized["gateway_group"]
    normalized_group["instance_count"] = source_group["instance_count"]
    if "vm_ha" in source_group:
        normalized_group["vm_ha"] = copy.deepcopy(source_group["vm_ha"])
    else:
        normalized_group.pop("vm_ha", None)
    if "external_ips" in source_group:
        normalized_group["external_ips"] = copy.deepcopy(source_group["external_ips"])
    else:
        normalized_group.pop("external_ips", None)
    for index, source_connection in enumerate(source_connections):
        normalized["connections"][index]["tunnels"] = copy.deepcopy(
            source_connection.get("tunnels") or []
        )

    source_gateway = source.get("gateway") or {}
    source_quotas = source_gateway.get("quotas")
    normalized_gateway = normalized.get("gateway") or {}
    normalized_quotas = normalized_gateway.get("quotas")
    if source_quotas is None:
        if isinstance(normalized_quotas, dict):
            normalized_quotas.pop("max_tunnels", None)
            if not normalized_quotas:
                normalized_gateway.pop("quotas", None)
    else:
        if not isinstance(normalized_quotas, dict):
            raise WizardValidationError("Structural guard rejected gateway quota removal.")
        if "max_tunnels" in source_quotas:
            normalized_quotas["max_tunnels"] = source_quotas["max_tunnels"]
        else:
            normalized_quotas.pop("max_tunnels", None)
    if normalized != source:
        raise WizardValidationError(
            "Structural guard rejected a change outside instance count, VM-HA, passive IP, "
            "member-1 tunnels, or the required tunnel quota."
        )


def is_vm_ha_conversion_candidate(source: dict[str, t.Any], candidate: dict[str, t.Any]) -> bool:
    """Return whether an existing destination is an exact allowed conversion of source."""

    try:
        validate_vm_ha_conversion_source(source)
        _assert_vm_ha_structural_allowlist(source, candidate)
        _validate_raw_candidate(candidate)
    except (WizardValidationError, TypeError, ValueError, KeyError):
        return False
    return True


def render_vm_ha_conversion_yaml(config: dict[str, t.Any]) -> str:
    body = yaml.safe_dump(config, sort_keys=False, default_flow_style=False, allow_unicode=False)
    return (
        "# Generated by nebius-vpngw vm-ha.\n"
        "# The original configuration is unchanged; PSKs remain environment references.\n"
        f"{body}"
    )


def run_vm_ha_conversion_wizard(
    console: Console,
    source: dict[str, t.Any],
    destination: Path,
    *,
    reserve_passive_ip: t.Callable[[], str],
) -> VMHAConversionResult:
    """Guide an admitted ordinary config through peer setup to one valid candidate."""

    validate_vm_ha_conversion_source(source)
    group = t.cast(dict[str, t.Any], source["gateway_group"])
    semantic_source = resolve_vm_ha_conversion_source(source)
    semantic_group = t.cast(dict[str, t.Any], semantic_source["gateway_group"])
    gateway_name = str(semantic_group["name"])
    console.print(
        Panel.fit(
            "[bold cyan]Convert an ordinary gateway to explicit VM-HA[/bold cyan]\n\n"
            "The source remains untouched. This wizard adds only the passive Nebius member "
            "and its tunnel counterparts, then writes a complete candidate after peer setup.\n"
            "Comments are canonicalized in the new file; raw environment references are preserved.\n\n"
            f"[dim]{_CONTROL_HELP}[/dim]",
            title="VM-HA Configuration Wizard",
            border_style="cyan",
        )
    )
    prompt = _Prompter(console)
    while True:
        try:
            console.rule("[bold cyan]1. Passive member tunnel parameters[/bold cyan]")
            derived = _derive_member_one_tunnels(source, semantic_source, prompt)
            break
        except _Back:
            console.print("[yellow]Restarting the passive tunnel parameter section.[/yellow]")

    vm_ha_block = _default_vm_ha_block(gateway_name)

    while True:
        try:
            preallocated_ip = prompt.ask(
                "Preallocated passive public IP (optional)",
                default="",
                help_text=(
                    "Enter an existing unattached public IP for member 1, or leave blank to choose "
                    "whether Nebius should reserve the deterministic member-1 allocation."
                ),
                allow_empty=True,
                validator=lambda value: _validate_ip(value) if value else value,
            )
            break
        except _Back:
            console.print("[yellow]Repeating the passive public IP question.[/yellow]")

    passive_ip_reserved = False
    passive_ip = preallocated_ip or None
    if passive_ip is None:
        reserve_now = prompt.ask_bool(
            "Reserve only the passive Nebius public IP now",
            default=False,
            help_text=(
                "This authenticates to Nebius, ensures the gateway subnet and route table, and "
                "creates or reuses only <gateway>-1-eth0-ip. It does not inspect member 0, "
                "create VMs, shared aliases, routes, lifecycle state, or host configuration."
            ),
        )
        if not reserve_now:
            console.print(
                "[yellow]No cloud operation was requested. No candidate was written; rerun when "
                "a passive public IP is available.[/yellow]"
            )
            return VMHAConversionResult(None, None, None, False, False, len(derived))
        passive_ip = _validate_ip(reserve_passive_ip())
        passive_ip_reserved = True

    handoff = Table(title="Incremental peer handoff for Nebius member 1")
    handoff.add_column("Tunnel")
    handoff.add_column("Nebius public IP")
    handoff.add_column("Nebius inner IP")
    handoff.add_column("Peer inner IP")
    handoff.add_column("PSK reference")
    for new_tunnel, peer_ip in derived:
        handoff.add_row(
            str(new_tunnel["name"]),
            passive_ip,
            str(new_tunnel["inner_local_ip"]),
            peer_ip,
            str(new_tunnel["psk"]),
        )
    console.print()
    console.print(handoff)
    console.print(
        "[dim]Configure only these new member-1 endpoints on the peer. Existing member-0 "
        "endpoints are unchanged and are not rediscovered by this wizard.[/dim]"
    )
    while True:
        try:
            peer_ready = prompt.ask_bool(
                "Is the peer configured and ready to provide its new endpoints",
                default=False,
                help_text="Answer no to stop safely without a candidate and rerun after peer setup.",
            )
            break
        except _Back:
            console.print("[yellow]Repeating the peer readiness question.[/yellow]")
    if not peer_ready:
        console.print(
            "[yellow]No candidate was written; rerun when peer details are ready."
            + (
                f" The reserved passive IP {passive_ip} remains allocated and will be reused."
                if passive_ip_reserved
                else ""
            )
            + "[/yellow]"
        )
        return VMHAConversionResult(
            None,
            None,
            passive_ip,
            passive_ip_reserved,
            False,
            len(derived),
        )

    while True:
        try:
            console.rule("[bold cyan]2. Peer endpoints and candidate review[/bold cyan]")
            for new_tunnel, expected_peer_ip in derived:
                new_tunnel["remote_public_ip"] = prompt.ask(
                    f"Peer public IP for {new_tunnel['name']}",
                    help_text="The peer-side public VPN endpoint created for this new tunnel.",
                    validator=_validate_ip,
                )
                new_tunnel["inner_remote_ip"] = prompt.ask(
                    f"Peer inner IP for {new_tunnel['name']}",
                    default=expected_peer_ip,
                    help_text="Confirm the peer host address in the APIPA /30 shown in the handoff.",
                    validator=_validate_ip,
                )

            candidate = t.cast(dict[str, t.Any], copy.deepcopy(source))
            candidate_group = t.cast(dict[str, t.Any], candidate["gateway_group"])
            candidate_group["instance_count"] = 2
            candidate_group["vm_ha"] = copy.deepcopy(vm_ha_block)
            source_external = group.get("external_ips")
            row_zero = copy.deepcopy(source_external[0]) if source_external else []
            candidate_group["external_ips"] = [row_zero, [passive_ip]]
            _apply_member_one_tunnels(candidate, derived)
            _required_tunnel_quota(candidate, semantic_source)
            _assert_vm_ha_structural_allowlist(source, candidate)
            _validate_raw_candidate(candidate)

            summary = Table(title="Redacted VM-HA candidate summary", show_header=False)
            summary.add_column("Field", style="bold")
            summary.add_column("Value")
            summary.add_row("Destination", str(destination))
            summary.add_row("Retained member", f"{gateway_name}-0 (instance 0, active)")
            summary.add_row("Added member", f"{gateway_name}-1 (instance 1, passive)")
            summary.add_row("Added tunnel counterparts", str(len(derived)))
            summary.add_row("Passive public IP", passive_ip)
            summary.add_row("PSKs", "environment references only")
            summary.add_row(
                "VM-HA credentials",
                "managed under "
                + display_vm_ha_credential_path(
                    project_id=str(candidate["project_id"]),
                    gateway_name=gateway_name,
                ),
            )
            summary.add_row("Validation", "schema v1 passed")
            console.print()
            console.print(summary)
            if not prompt.ask_bool(
                "Write this complete VM-HA candidate",
                default=False,
                help_text=(
                    "The source remains unchanged. No deployment or migration approval is performed."
                ),
            ):
                raise WizardCancelled
            return VMHAConversionResult(
                render_vm_ha_conversion_yaml(candidate),
                candidate,
                passive_ip,
                passive_ip_reserved,
                True,
                len(derived),
            )
        except _Back:
            console.print("[yellow]Restarting the peer endpoint and review section.[/yellow]")
