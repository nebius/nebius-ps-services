"""Interactive, schema-backed configuration creation and migration wizards."""

from __future__ import annotations

import copy
import ipaddress
import re
import typing as t
from pathlib import Path

import typer
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config_template import DEFAULT_CONFIG_TEMPLATE
from .schema import DefaultsConfig, GatewayConfig, GatewayGroup, VPNGatewayConfig
from .vm_ha_credentials import display_vm_ha_credential_path

_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]{4,}$")
_CONTROL_HELP = "Enter ? for help, b to restart the previous section, or q to quit."


class WizardCancelled(Exception):
    """The user deliberately cancelled without publishing a file."""


class WizardInterrupted(Exception):
    """Input ended unexpectedly; the caller must preserve the destination."""


class WizardValidationError(Exception):
    """The completed candidate did not satisfy the existing configuration schema."""


class _Back(Exception):
    pass


class _Prompter:
    def __init__(self, console: Console) -> None:
        self.console = console

    def ask(
        self,
        label: str,
        *,
        default: str | None = None,
        help_text: str,
        allow_empty: bool = False,
        validator: t.Callable[[str], str] | None = None,
        hide_input: bool = False,
    ) -> str:
        self.console.print(f"[dim]{help_text}[/dim]")
        while True:
            try:
                value = typer.prompt(
                    label,
                    default=default,
                    show_default=default is not None and not hide_input,
                    hide_input=hide_input,
                )
            except (typer.Abort, EOFError, KeyboardInterrupt) as error:
                raise WizardInterrupted from error
            normalized = str(value).strip()
            token = normalized.casefold()
            if token == "?":
                self.console.print(f"[cyan]{help_text}[/cyan]")
                continue
            if token in {"b", "back", ":back"}:
                raise _Back
            if token in {"q", "quit", ":quit"}:
                raise WizardCancelled
            if not normalized and not allow_empty:
                self.console.print("[red]A value is required.[/red]")
                continue
            if validator is None:
                return normalized
            try:
                return validator(normalized)
            except ValueError as error:
                self.console.print(f"[red]{error}[/red]")

    def ask_bool(self, label: str, *, default: bool, help_text: str) -> bool:
        default_text = "yes" if default else "no"
        while True:
            value = self.ask(
                f"{label} [yes/no]",
                default=default_text,
                help_text=help_text,
            ).casefold()
            if value in {"y", "yes"}:
                return True
            if value in {"n", "no"}:
                return False
            self.console.print("[red]Enter yes or no.[/red]")

    def ask_choice(
        self,
        label: str,
        choices: t.Sequence[str],
        *,
        default: str,
        help_text: str,
    ) -> str:
        allowed = {choice.casefold(): choice for choice in choices}
        choice_text = "/".join(choices)
        while True:
            value = self.ask(
                f"{label} [{choice_text}]",
                default=default,
                help_text=help_text,
            ).casefold()
            if value in allowed:
                return allowed[value]
            self.console.print(f"[red]Choose one of: {', '.join(choices)}.[/red]")

    def ask_int(
        self,
        label: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
        help_text: str,
    ) -> int:
        def validate(value: str) -> str:
            try:
                parsed = int(value)
            except ValueError as error:
                raise ValueError("Enter a whole number.") from error
            if not minimum <= parsed <= maximum:
                raise ValueError(f"Enter a value from {minimum} through {maximum}.")
            return str(parsed)

        return int(
            self.ask(
                label,
                default=str(default),
                help_text=help_text,
                validator=validate,
            )
        )


def _validate_name(value: str) -> str:
    if not _NAME_RE.fullmatch(value) or len(value) > 64:
        raise ValueError(
            "Use 1-64 lowercase letters, digits, or hyphens; start and end with a letter or digit."
        )
    return value


def _validate_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as error:
        raise ValueError("Enter a valid IPv4 or IPv6 address.") from error


def _validate_cidr(value: str) -> str:
    try:
        return str(ipaddress.ip_network(value, strict=False))
    except ValueError as error:
        raise ValueError("Enter a valid network in CIDR notation, such as 10.0.0.0/16.") from error


def _validate_private_subnet(value: str) -> str:
    if not value:
        return value
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as error:
        raise ValueError(
            "Enter a private IPv4 CIDR, or leave blank for automatic selection."
        ) from error
    if network.version != 4 or not network.is_private or not 8 <= network.prefixlen <= 28:
        raise ValueError(
            "The gateway subnet must be private IPv4 with a prefix from /8 through /28."
        )
    return str(network)


def _validate_apipa_cidr(value: str) -> str:
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as error:
        raise ValueError("Enter a valid APIPA /30, such as 169.254.10.0/30.") from error
    apipa = ipaddress.IPv4Network("169.254.0.0/16")
    if (
        not isinstance(network, ipaddress.IPv4Network)
        or network.prefixlen != 30
        or not network.subnet_of(apipa)
    ):
        raise ValueError("Tunnel addressing must be a /30 inside 169.254.0.0/16.")
    return str(network)


def _validate_env_name(value: str) -> str:
    """Validate environment names used by the separate VM-HA conversion wizard."""

    if not _ENV_NAME_RE.fullmatch(value):
        raise ValueError(
            "Use at least five uppercase letters, digits, or underscores, starting with a letter or underscore."
        )
    return value


def _validate_psk_input(value: str) -> str:
    """Classify one hidden PSK answer without ever including it in an error."""

    if _ENV_NAME_RE.fullmatch(value):
        return "${" + value + "}"
    if "${" in value:
        raise ValueError(
            "Literal PSKs cannot contain ${...}; use an environment variable name or edit "
            "the generated YAML after the wizard finishes."
        )
    if len(value) < 8:
        raise ValueError(
            "Enter an uppercase environment variable name or a literal PSK with at least 8 characters."
        )
    return value


def _format_validation_error(error: ValidationError) -> str:
    """Render schema failures without Pydantic input values or secret-bearing context."""

    messages: list[str] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in item.get("loc", ())) or "configuration"
        messages.append(f"{location}: {item.get('msg', 'is invalid')}")
    return "\n".join(messages) or "The configuration is invalid."


def _validate_absolute_path(value: str) -> str:
    if not value.startswith("/"):
        raise ValueError("Enter an absolute path starting with /.")
    return value


def _validate_preset(value: str) -> str:
    if re.fullmatch(r"\d+vcpu-\d+gb", value) is None:
        raise ValueError("Use a preset such as 4vcpu-16gb.")
    return value


def _validate_asn(value: str) -> str:
    try:
        asn = int(value)
    except ValueError as error:
        raise ValueError("Enter a numeric BGP ASN.") from error
    if not 1 <= asn <= 4_199_999_999:
        raise ValueError("Enter a valid 16-bit or 32-bit BGP ASN.")
    return str(asn)


def _csv_values(
    prompter: _Prompter,
    label: str,
    *,
    default: list[str],
    help_text: str,
    required: bool,
    validator: t.Callable[[str], str],
) -> list[str]:
    def validate(value: str) -> str:
        values = [item.strip() for item in value.split(",") if item.strip()]
        if required and not values:
            raise ValueError("Enter at least one value.")
        return ", ".join(validator(item) for item in values)

    raw = prompter.ask(
        label,
        default=", ".join(default),
        help_text=help_text,
        allow_empty=not required,
        validator=validate,
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_apipa(tunnel_number: int) -> str:
    third_octet = 10 + tunnel_number // 64
    fourth_octet = (tunnel_number % 64) * 4
    if third_octet > 254:
        raise WizardValidationError("The requested tunnel count exhausts the APIPA wizard range.")
    return f"169.254.{third_octet}.{fourth_octet}/30"


def _base_candidate() -> dict[str, t.Any]:
    loaded = yaml.safe_load(DEFAULT_CONFIG_TEMPLATE)
    if not isinstance(loaded, dict):  # pragma: no cover - embedded template invariant
        raise WizardValidationError("The embedded configuration template is invalid.")
    candidate = t.cast(dict[str, t.Any], copy.deepcopy(loaded))
    # The embedded template is a compatibility contract for non-interactive callers and
    # intentionally retains its detailed example. Fresh interactive sessions must not
    # inherit that provider-specific example as user input.
    candidate["connections"] = []
    return candidate


def _project_phase(candidate: dict[str, t.Any], prompt: _Prompter) -> None:
    prompt.console.rule("[bold cyan]1. Nebius project[/bold cyan]")
    candidate["tenant_id"] = prompt.ask(
        "Tenant ID",
        default=str(candidate.get("tenant_id") or "").replace("${TENANT_ID}", ""),
        help_text="The tenant containing the VPN gateway project.",
    )
    candidate["project_id"] = prompt.ask(
        "Project ID",
        default=str(candidate.get("project_id") or "").replace("${PROJECT_ID}", ""),
        help_text="The Nebius project where network resources and gateway VMs belong.",
    )
    candidate["region_id"] = prompt.ask(
        "Region ID",
        default=str(candidate.get("region_id") or "eu-north1").replace("${REGION_ID}", "")
        or "eu-north1",
        help_text="The Nebius region for network resources and gateway VMs, such as eu-north1.",
    )


def _vm_ha_config(prompt: _Prompter, group: dict[str, t.Any]) -> None:
    current = group.get("vm_ha") or {}
    enabled = prompt.ask_bool(
        "Enable VM-level active/passive HA",
        default=bool(current.get("enabled", False)),
        help_text=(
            "VM-HA is independent of tunnel active/passive roles and remains disabled unless "
            "you explicitly answer yes. Enabling it fixes the gateway at two VM members."
        ),
    )
    if not enabled:
        group["vm_ha"] = {"enabled": False}
        return

    group["instance_count"] = 2
    cluster_id = prompt.ask(
        "VM-HA cluster ID",
        default=str(current.get("cluster_id") or f"{group['name']}-ha"),
        help_text="A stable cluster name shared by the two gateway members.",
        validator=_validate_name,
    )
    members: list[dict[str, t.Any]] = []
    current_members = current.get("members") or []
    for index, role in enumerate(("active", "passive")):
        existing = current_members[index] if index < len(current_members) else {}
        node_id = prompt.ask(
            f"Member {index} node ID ({role})",
            default=str(existing.get("node_id") or f"{group['name']}-{index}"),
            help_text=f"Stable identity for instance index {index}; its initial ownership role is {role}.",
            validator=_validate_name,
        )
        members.append(
            {
                "node_id": node_id,
                "instance_index": index,
                "role": role,
            }
        )
    group["vm_ha"] = {"enabled": True, "cluster_id": cluster_id, "members": members}


def _gateway_phase(candidate: dict[str, t.Any], prompt: _Prompter) -> None:
    prompt.console.rule("[bold cyan]2. Gateway and networking[/bold cyan]")
    group = t.cast(dict[str, t.Any], candidate["gateway_group"])
    gateway = t.cast(dict[str, t.Any], candidate["gateway"])
    group["name"] = prompt.ask(
        "Gateway group name",
        default=str(group.get("name") or "nebius-vpn-gw"),
        help_text="Lowercase resource-name prefix used for gateway VMs and allocations.",
        validator=_validate_name,
    )
    group["instance_count"] = prompt.ask_int(
        "Gateway VM count",
        default=int(group.get("instance_count") or 1),
        minimum=1,
        maximum=10,
        help_text="Ordinary gateways may use 1-10 VMs. VM-HA, if explicitly enabled below, uses exactly 2.",
    )
    group["region"] = str(candidate["region_id"])

    template = _base_candidate()
    template_group = t.cast(dict[str, t.Any], template["gateway_group"])
    template_gateway = t.cast(dict[str, t.Any], template["gateway"])
    template_defaults = t.cast(dict[str, t.Any], template["defaults"])
    advanced_default = bool((group.get("vm_ha") or {}).get("enabled")) or any(
        (
            group.get("vm_spec") != template_group.get("vm_spec"),
            gateway.get("quotas") != template_gateway.get("quotas"),
            candidate["defaults"].get("dpd") != template_defaults.get("dpd"),
        )
    )
    advanced = prompt.ask_bool(
        "Configure advanced VM and HA settings",
        default=advanced_default,
        help_text="Choose yes to customize VM sizing, SSH paths, quotas, timers, or explicit VM-level HA.",
    )
    if advanced:
        vm_spec = t.cast(dict[str, t.Any], group["vm_spec"])
        vm_spec["platform"] = prompt.ask_choice(
            "Compute platform",
            ("cpu-d3", "cpu-e2"),
            default=str(vm_spec.get("platform") or "cpu-d3"),
            help_text="cpu-d3 is the current template default; choose a platform available in the selected region.",
        )
        vm_spec["preset"] = prompt.ask(
            "VM preset",
            default=str(vm_spec.get("preset") or "4vcpu-16gb"),
            help_text="Preset in the form <vcpu>vcpu-<gb>gb, such as 4vcpu-16gb.",
            validator=_validate_preset,
        )
        vm_spec["disk_gb"] = prompt.ask_int(
            "Boot disk size (GB)",
            default=int(vm_spec.get("disk_gb") or 100),
            minimum=20,
            maximum=2000,
            help_text="Size of each gateway VM boot disk.",
        )
        vm_spec["ssh_public_key_path"] = prompt.ask(
            "SSH public key path",
            default=str(vm_spec.get("ssh_public_key_path") or "~/.ssh/id_ed25519.pub"),
            help_text="Local public key path used when provisioning gateway VMs.",
        )
        vm_spec["ssh_private_key_path"] = prompt.ask(
            "SSH private key path",
            default=str(vm_spec.get("ssh_private_key_path") or "~/.ssh/id_ed25519"),
            help_text="Local private key path used by deployment and status commands; key bytes are not copied.",
        )
        quotas = t.cast(dict[str, t.Any], gateway["quotas"])
        quotas["max_connections"] = prompt.ask_int(
            "Maximum connections",
            default=int(quotas.get("max_connections") or 16),
            minimum=1,
            maximum=100,
            help_text="Safety quota for configured peer connections.",
        )
        quotas["max_tunnels"] = prompt.ask_int(
            "Maximum tunnels",
            default=int(quotas.get("max_tunnels") or 32),
            minimum=1,
            maximum=200,
            help_text="Safety quota across all connections and gateway instances.",
        )
        defaults = t.cast(dict[str, t.Any], candidate["defaults"])
        dpd = t.cast(dict[str, t.Any], defaults["dpd"])
        dpd["interval_seconds"] = prompt.ask_int(
            "DPD interval (seconds)",
            default=int(dpd.get("interval_seconds") or 5),
            minimum=5,
            maximum=300,
            help_text="How often to probe tunnel liveness.",
        )
        dpd["timeout_seconds"] = prompt.ask_int(
            "DPD timeout (seconds)",
            default=max(int(dpd.get("timeout_seconds") or 15), dpd["interval_seconds"] + 1),
            minimum=max(15, dpd["interval_seconds"] + 1),
            maximum=600,
            help_text="Must be greater than the DPD interval.",
        )
        _vm_ha_config(prompt, group)
    else:
        group["vm_ha"] = {"enabled": False}

    network_id = prompt.ask(
        "Existing VPC network ID (optional)",
        default=str(group.get("network_id") or ""),
        help_text=(
            "Enter the exact VPC ID when you know where the gateways belong. Leave blank "
            "to use default-network, or the project's only network; multiple networks "
            "require an explicit ID."
        ),
        allow_empty=True,
    )
    if network_id:
        group["network_id"] = network_id
    else:
        group.pop("network_id", None)
    subnet = t.cast(dict[str, t.Any], group["subnet"])
    subnet["name"] = prompt.ask(
        "Gateway subnet name",
        default=str(subnet.get("name") or "vpngw-subnet"),
        help_text="Dedicated subnet name to ensure or create for gateway VMs.",
        validator=_validate_name,
    )
    subnet_cidr = prompt.ask(
        "Gateway subnet CIDR (optional)",
        default=str(subnet.get("cidr") or ""),
        help_text=(
            "Leave blank to reuse an existing subnet with this exact name, or auto-carve "
            "a free private subnet if the name does not exist during network preparation."
        ),
        allow_empty=True,
        validator=_validate_private_subnet,
    )
    subnet["cidr"] = subnet_cidr or None
    subnet["prefix_length"] = prompt.ask_int(
        "Automatic subnet prefix length (new subnet only)",
        default=int(subnet.get("prefix_length") or 24),
        minimum=8,
        maximum=28,
        help_text=(
            "Used only when CIDR is blank and the named subnet does not exist; it sets "
            "the size of the auto-created subnet. An explicit CIDR's /prefix always wins."
        ),
    )
    # Public allocation discovery requires Nebius access. Keep authoring offline and let
    # the separately confirmed prep-network flow select or reserve exact allocations.
    group["external_ips"] = t.cast(list[list[str]], group.get("external_ips") or [])
    gateway["local_prefixes"] = _csv_values(
        prompt,
        "Local prefixes (comma-separated)",
        default=t.cast(list[str], gateway.get("local_prefixes") or ["10.0.0.0/16"]),
        help_text="Networks behind Nebius that the gateway advertises or permits through static tunnels.",
        required=True,
        validator=_validate_cidr,
    )
    try:
        GatewayGroup.model_validate(group)
        GatewayConfig.model_validate(gateway)
        DefaultsConfig.model_validate(candidate["defaults"])
    except ValidationError as error:
        raise WizardValidationError(_format_validation_error(error)) from error


def _connection_phase(candidate: dict[str, t.Any], prompt: _Prompter) -> None:
    prompt.console.rule("[bold cyan]3. Peer connections and tunnels[/bold cyan]")
    group = t.cast(dict[str, t.Any], candidate["gateway_group"])
    instance_count = int(group["instance_count"])
    gateway = t.cast(dict[str, t.Any], candidate["gateway"])
    quotas = t.cast(dict[str, t.Any], gateway["quotas"])
    max_connections = min(16, int(quotas["max_connections"]))
    max_tunnels = int(quotas["max_tunnels"])
    previous = t.cast(list[dict[str, t.Any]], candidate.get("connections") or [])
    connection_count = prompt.ask_int(
        "Number of peer connections",
        default=min(max(len(previous), 1), max_connections),
        minimum=1,
        maximum=max_connections,
        help_text="Create one connection for each peer site or cloud VPN gateway.",
    )
    connections: list[dict[str, t.Any]] = []
    tunnel_number = 0
    local_asn_prompted = False
    for connection_index in range(connection_count):
        old = previous[connection_index] if connection_index < len(previous) else {}
        old_tunnels = t.cast(list[dict[str, t.Any]], old.get("tunnels") or [])
        prompt.console.print(f"\n[bold]Connection {connection_index + 1}[/bold]")
        name = prompt.ask(
            "Connection name",
            default=str(old.get("name") or f"site-{connection_index + 1}"),
            help_text="A unique lowercase name used as the prefix for generated tunnel names.",
            validator=_validate_name,
        )
        vendor = prompt.ask_choice(
            "Peer vendor",
            ("gcp", "aws", "azure", "cisco", "generic"),
            default=str(old.get("vendor") or "generic"),
            help_text="Select the peer platform; generic is appropriate for standards-based appliances.",
        )
        routing_mode = prompt.ask_choice(
            "Routing mode",
            ("bgp", "static"),
            default=str(old.get("routing_mode") or "bgp"),
            help_text="BGP learns peer routes dynamically; static requires explicit remote prefixes.",
        )
        if routing_mode == "bgp":
            if not local_asn_prompted:
                gateway["local_asn"] = int(
                    prompt.ask(
                        "Local BGP ASN",
                        default=str(gateway.get("local_asn") or 65010),
                        help_text="The ASN advertised by the Nebius gateway for BGP connections.",
                        validator=_validate_asn,
                    )
                )
                local_asn_prompted = True
            remote_asn = int(
                prompt.ask(
                    "Peer BGP ASN",
                    default=str((old.get("bgp") or {}).get("remote_asn") or 64514),
                    help_text="The ASN advertised by this peer connection.",
                    validator=_validate_asn,
                )
            )
            remote_prefixes = _csv_values(
                prompt,
                "Allowed remote prefixes (optional, comma-separated)",
                default=t.cast(list[str], old.get("remote_prefixes") or []),
                help_text="Optional BGP route allowlist. Leave blank to rely on learned routes and other policy.",
                required=False,
                validator=_validate_cidr,
            )
            bgp: dict[str, t.Any] = {
                "enabled": True,
                "remote_asn": remote_asn,
                "advertise_local_prefixes": True,
            }
        else:
            remote_prefixes = _csv_values(
                prompt,
                "Remote prefixes (comma-separated)",
                default=t.cast(list[str], old.get("remote_prefixes") or ["192.168.0.0/16"]),
                help_text="Networks behind the peer that must be installed as static routes.",
                required=True,
                validator=_validate_cidr,
            )
            bgp = {"enabled": False, "remote_asn": None, "advertise_local_prefixes": True}
        paths_per_instance = prompt.ask_int(
            "Tunnel paths per gateway VM",
            default=(
                max(1, len(old_tunnels) // instance_count)
                if old_tunnels and len(old_tunnels) % instance_count == 0
                else 1
            ),
            minimum=1,
            maximum=4,
            help_text=(
                "The first path for each gateway VM is active; additional paths are passive. "
                "Each path receives its own peer IP, PSK, and APIPA /30."
            ),
        )
        projected_tunnels = sum(len(connection["tunnels"]) for connection in connections)
        projected_tunnels += instance_count * paths_per_instance
        if projected_tunnels > max_tunnels:
            raise WizardValidationError(
                f"This selection would create {projected_tunnels} tunnels, above the configured "
                f"max_tunnels quota of {max_tunnels}. Choose fewer connections or paths, or go "
                "back and raise the advanced quota."
            )
        tunnels: list[dict[str, t.Any]] = []
        for instance_index in range(instance_count):
            for path_index in range(paths_per_instance):
                old_tunnel_index = instance_index * paths_per_instance + path_index
                old_tunnel = (
                    old_tunnels[old_tunnel_index] if old_tunnel_index < len(old_tunnels) else {}
                )
                default_name = f"{name}-gw{instance_index + 1}-tunnel{path_index + 1}"
                tunnel_name = prompt.ask(
                    f"Tunnel name for VM {instance_index}, path {path_index + 1}",
                    default=str(old_tunnel.get("name") or default_name[:64].rstrip("-")),
                    help_text="Tunnel names must be globally unique across every connection.",
                    validator=_validate_name,
                )
                remote_ip = prompt.ask(
                    f"Remote public IP for {tunnel_name}",
                    default=str(old_tunnel.get("remote_public_ip") or "") or None,
                    help_text="Public IP of the peer VPN endpoint for this exact tunnel.",
                    validator=_validate_ip,
                )
                old_psk = str(old_tunnel.get("psk") or "")
                old_env_match = re.fullmatch(r"\$\{([A-Z_][A-Z0-9_]*)\}", old_psk)
                env_default = (
                    old_env_match.group(1)
                    if old_env_match
                    else re.sub(r"[^A-Z0-9_]", "_", tunnel_name.upper()) + "_PSK"
                )
                psk = prompt.ask(
                    f"PSK or environment variable for {tunnel_name} [{env_default}]",
                    default=env_default,
                    help_text=(
                        "Enter an uppercase environment variable name to store ${NAME}, enter any "
                        "other value of at least 8 characters to store a literal PSK, or press Enter "
                        "to keep the generated variable and complete it later. Input is hidden."
                    ),
                    validator=_validate_psk_input,
                    hide_input=True,
                )
                inner_cidr = prompt.ask(
                    f"APIPA /30 for {tunnel_name}",
                    default=str(old_tunnel.get("inner_cidr") or _default_apipa(tunnel_number)),
                    help_text="Unique point-to-point subnet inside 169.254.0.0/16.",
                    validator=_validate_apipa_cidr,
                )
                hosts = list(ipaddress.ip_network(inner_cidr).hosts())
                old_local_ip = str(old_tunnel.get("inner_local_ip") or "")
                local_first = prompt.ask_bool(
                    "Does Nebius use the first usable APIPA address",
                    default=not old_local_ip or old_local_ip == str(hosts[0]),
                    help_text="Answer no when the peer specification assigns the first host address to the peer.",
                )
                local_ip, remote_inner_ip = (hosts[0], hosts[1])
                if not local_first:
                    local_ip, remote_inner_ip = remote_inner_ip, local_ip
                tunnels.append(
                    {
                        "name": tunnel_name,
                        "gateway_instance_index": instance_index,
                        "local_public_ip_index": 0,
                        "ha_role": "active" if path_index == 0 else "passive",
                        "remote_public_ip": remote_ip,
                        "psk": psk,
                        "inner_cidr": inner_cidr,
                        "inner_local_ip": str(local_ip),
                        "inner_remote_ip": str(remote_inner_ip),
                    }
                )
                tunnel_number += 1
        connections.append(
            {
                "name": name,
                "vendor": vendor,
                "routing_mode": routing_mode,
                "remote_prefixes": remote_prefixes or None,
                "bgp": bgp,
                "tunnels": tunnels,
            }
        )
    candidate["connections"] = connections
    defaults = t.cast(dict[str, t.Any], candidate["defaults"])
    routing = t.cast(dict[str, t.Any], defaults["routing"])
    routing["mode"] = connections[0]["routing_mode"]
    _validated_config(candidate)


def _validated_config(candidate: dict[str, t.Any]) -> dict[str, t.Any]:
    try:
        model = VPNGatewayConfig.model_validate(candidate)
    except ValidationError as error:
        raise WizardValidationError(_format_validation_error(error)) from error
    return t.cast(dict[str, t.Any], model.model_dump(mode="json"))


def _review_phase(
    candidate: dict[str, t.Any], prompt: _Prompter, destination: Path
) -> dict[str, t.Any]:
    validated = _validated_config(candidate)
    group = t.cast(dict[str, t.Any], validated["gateway_group"])
    connections = t.cast(list[dict[str, t.Any]], validated["connections"])
    table = Table(title="Configuration summary", show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Destination", str(destination))
    table.add_row("Project", str(validated["project_id"]))
    table.add_row("Gateway", f"{group['name']} ({group['instance_count']} VM(s))")
    table.add_row("Connections", str(len(connections)))
    table.add_row("Tunnels", str(sum(len(item["tunnels"]) for item in connections)))
    table.add_row(
        "VM-level HA", "enabled" if (group.get("vm_ha") or {}).get("enabled") else "disabled"
    )
    if (group.get("vm_ha") or {}).get("enabled"):
        table.add_row(
            "VM-HA credentials",
            "managed under "
            + display_vm_ha_credential_path(
                project_id=str(validated["project_id"]),
                gateway_name=str(group["name"]),
            ),
        )
    table.add_row("Public IPs", "existing addresses" if group.get("external_ips") else "automatic")
    psks = [
        str(tunnel["psk"])
        for connection in connections
        for tunnel in t.cast(list[dict[str, t.Any]], connection["tunnels"])
    ]
    environment_psks = sum(bool(re.fullmatch(r"\$\{[A-Z_][A-Z0-9_]*\}", psk)) for psk in psks)
    literal_psks = len(psks) - environment_psks
    table.add_row(
        "PSKs",
        f"{environment_psks} environment reference(s), {literal_psks} literal value(s)",
    )
    prompt.console.print()
    prompt.console.print(table)
    if not prompt.ask_bool(
        "Write this validated configuration",
        default=True,
        help_text="No file is written until you confirm this schema-valid, redacted summary.",
    ):
        raise WizardCancelled
    return validated


def render_wizard_yaml(config: dict[str, t.Any]) -> str:
    """Serialize a validated candidate in stable schema order."""
    body = yaml.safe_dump(config, sort_keys=False, default_flow_style=False, allow_unicode=False)
    return (
        "# Generated by nebius-vpngw create-config wizard.\n"
        "# PSKs may be ${ENVIRONMENT_VARIABLE} references or literal values.\n"
        "# Keep *.config.yaml private and out of git.\n"
        f"{body}"
    )


def run_config_wizard(console: Console, destination: Path) -> str:
    """Guide a user through one complete candidate and return validated YAML."""
    console.print(
        Panel.fit(
            "[bold cyan]Guided VPN gateway configuration[/bold cyan]\n\n"
            "The wizard builds the complete YAML in memory, validates it against schema v1, "
            "and writes nothing until the final confirmation.\n"
            "PSKs may use environment references or hidden literal input.\n\n"
            f"[dim]{_CONTROL_HELP} Back restarts the previous section.[/dim]",
            title="Configuration Wizard",
            border_style="cyan",
        )
    )
    candidate = _base_candidate()
    prompt = _Prompter(console)
    phases: list[t.Callable[[], None]] = [
        lambda: _project_phase(candidate, prompt),
        lambda: _gateway_phase(candidate, prompt),
        lambda: _connection_phase(candidate, prompt),
    ]
    phase_index = 0
    while phase_index < len(phases):
        try:
            phases[phase_index]()
        except _Back:
            if phase_index == 0:
                console.print("[yellow]This is the first section; restarting it.[/yellow]")
            else:
                phase_index -= 1
            continue
        except WizardValidationError as error:
            console.print(
                Panel.fit(
                    f"[bold red]This section is not valid yet.[/bold red]\n\n{error}",
                    title="Validation Error",
                    border_style="red",
                )
            )
            continue
        phase_index += 1

    while True:
        try:
            validated = _review_phase(candidate, prompt, destination)
            break
        except _Back:
            phase_index = len(phases) - 1
            while phase_index < len(phases):
                try:
                    phases[phase_index]()
                except _Back:
                    phase_index = max(0, phase_index - 1)
                    continue
                phase_index += 1
        except WizardValidationError as error:
            console.print(
                Panel.fit(
                    f"[bold red]The candidate is not valid yet.[/bold red]\n\n{error}",
                    title="Validation Error",
                    border_style="red",
                )
            )
            if not prompt.ask_bool(
                "Return to the connection section and correct it",
                default=True,
                help_text="Answer no to stop without writing any file.",
            ):
                raise
            _connection_phase(candidate, prompt)

    return render_wizard_yaml(validated)
