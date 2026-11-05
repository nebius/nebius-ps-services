"""Explicit destination selection from the customer's resolved Nebius VPC."""

from __future__ import annotations

from typing import Any

from .provider_options import OptionChoice
from .tailscale_config import networks, validate_tags

GUIDANCE = {
    "inputs.management_cidrs": (
        "SSH management access: source IPv4 CIDRs for router TCP port 22 (deployment/recovery). "
        "Use your public egress IP; /32 means one address."
    ),
    "tailscale.tailnet": "Tailnet: the Tailscale network these routers will join.",
    "tailscale.tags": (
        "Router tags: shared by both HA routers; include the tag: prefix, e.g. tag:nebius-dev-router. "
        "Use an authorized tag or request approval before activation; availability is not checked."
    ),
    "tailscale.routes": (
        "Advertised routes: private subnets reachable through the routers. "
        "Route approval and user access are separate."
    ),
    "tailscale.onboarding.owner": (
        "Owner email: work address for the responsible person or monitored team, e.g. "
        "owner@example.com. Handoff contact only; grants no permissions."
    ),
    "tailscale.onboarding.purpose": (
        "Purpose: why access is needed, e.g. private Kubernetes access."
    ),
    "tailscale.onboarding.environment": "Environment: optional development/staging/production label.",
    "tailscale.onboarding.admin_requirements": (
        "Administrator requirements: optional conditions for this access request."
    ),
    "tailscale.policy.tag_owners": (
        "Tag owners: who may assign the router tags. Default [] leaves ownership administrator-managed."
    ),
    "tailscale.policy.auto_approve": (
        "Auto-approve routes: request automatic approval for these tags/routes; user access is separate."
    ),
    "tailscale.approval": (
        "Route approval: external uses admin/autoApprovers approval; api requires delegated route-write access."
    ),
}

POLICY_NOTE = "Changing requested access does not revoke applied grants; ask the tailnet admin to remove broader grants."


def print_comment(message: str) -> None:
    from . import cli

    cli.console.print(message, style="dim not bold", highlight=False, markup=False)


def print_guidance(label: str) -> None:
    for suffix, message in GUIDANCE.items():
        if label == suffix or label.endswith("." + suffix):
            print_comment(message)
            return


def print_auth_guidance() -> None:
    print_comment(
        "Credential storage: references only; supply OAuth values after approval.\n"
        "  Linked SecretStash: create an empty Nebius secret container; fill it later. No Kubernetes/ESO.\n"
        "  Existing SecretStash: read client_id/client_secret from a secret you can access.\n"
        "  Environment/file: use env:NAME or file:PATH; files must be owner-only."
    )


def prompt_tags(current: Any, label: str) -> tuple[Any, bool]:
    from . import cli

    while True:
        value, stop = cli._prompt_scalar_override(
            label, current, type_hint="list(string)", required=True
        )
        if stop or cli._wizard_backtrack_requested(value):
            return value, stop
        try:
            return validate_tags(value), False
        except ValueError as error:
            cli.console.print(str(error), markup=False)


def prompt_auth(
    current: Any, row: dict[str, Any], payload: dict[str, Any], label: str
) -> tuple[Any, bool]:
    """Select custody now; administrator credential values arrive in the handoff."""
    from . import cli

    print_auth_guidance()
    auth = dict(current) if isinstance(current, dict) else {}
    stash = auth.get("secretstash", {})
    storage = (
        "new"
        if stash.get("component_instance")
        else "existing"
        if stash
        else "reference"
        if auth.get("client_id") or auth.get("client_secret_ref")
        else "new"
    )
    choice, stop = cli._prompt_choice_override(
        path_label=label + ".storage",
        current=storage,
        choices=[
            OptionChoice(
                "new",
                "Keep the linked SecretStash container"
                if stash.get("component_instance")
                else "Create a linked SecretStash container (recommended)",
                recommended=True,
            ),
            OptionChoice("existing", "Use an existing SecretStash secret"),
            OptionChoice("reference", "Use a protected environment/file reference"),
        ],
        type_hint="string",
        required=True,
    )
    if stop or cli._wizard_backtrack_requested(choice):
        return choice, stop
    result = {k: auth[k] for k in ("ssh_private_key_file",) if k in auth}
    result["mode"] = "oauth"
    if choice == "reference":
        result.update({k: auth[k] for k in ("client_id", "client_secret_ref") if k in auth})
        return result, False
    project = row.get("inputs", {}).get("parent_id") or payload.get("client_info", {}).get(
        "nebius", {}
    ).get("project_id")
    ref: dict[str, Any] = {"project_id": project, **(stash if choice == storage else {})}
    if choice == "existing":
        identity, stop = cli._prompt_scalar_override(
            label + ".secret_id",
            auth.get("secretstash", {}).get("secret_id", ""),
            type_hint="string",
            required=True,
        )
        if stop or cli._wizard_backtrack_requested(identity):
            return identity, stop
        if identity != ref.get("secret_id"):
            ref = {"project_id": project}
        ref["secret_id"] = identity
    elif not ref.get("component_instance"):
        import hashlib

        owner = row["instance_id"]
        stem = (
            owner
            if len(owner) <= 54
            else owner[:41] + "-" + hashlib.sha256(owner.encode()).hexdigest()[:12]
        )
        ref.update(component_instance=stem + "-oauth", secret_name=stem + "-oauth")
    result["secretstash"] = ref
    return result, False


def _prompt_destinations(current: list[str], routes: list[str], label: str) -> tuple[Any, bool]:
    from . import cli

    print_comment(
        "Destinations: select private subnets these sources may reach, or enter narrower CIDRs."
    )
    choices = [
        OptionChoice(v, v, recommended=v in current) for v in dict.fromkeys([*routes, *current])
    ]
    choices.append(OptionChoice("__manual__", "Enter destination CIDRs manually"))
    while True:
        # Checked defaults are separate from the shared prompt's empty-selection fallback.
        value, stop = cli._prompt_multi_choice_override(
            path_label=label, current=[], choices=choices, type_hint="list(string)"
        )
        if stop or cli._wizard_backtrack_requested(value):
            return value, stop
        if not isinstance(value, list):
            raise ValueError("Destination selection must return a list of CIDRs")
        if not value:
            cli.console.print("Select at least one destination, or use q/qq to navigate.")
            continue
        if "__manual__" in value:
            return cli._prompt_scalar_override(
                label,
                [v for v in value if v != "__manual__"],
                type_hint="list(string)",
                required=True,
            )
        return value, False


def _prompt_rule(previous: dict[str, Any], routes: list[str], label: str) -> tuple[Any, bool]:
    from . import cli

    mode, stop = cli._prompt_choice_override(
        path_label=label + ".access_level",
        current="all" if not previous or previous.get("protocol") == "all" else "services",
        choices=[
            OptionChoice("all", "VPN-style access — all protocols and ports (recommended)"),
            OptionChoice("services", "Specific services — choose protocols and ports"),
        ],
        required=True,
    )
    if stop or cli._wizard_backtrack_requested(mode):
        return mode, stop
    print_comment(
        "Sources: allowed Tailscale identities, e.g. user@example.com or group:developers. "
        "Groups must already exist."
    )
    sources, stop = cli._prompt_scalar_override(
        label + ".sources", previous.get("sources", []), type_hint="list(string)", required=True
    )
    if stop or cli._wizard_backtrack_requested(sources):
        return sources, stop
    destinations, stop = _prompt_destinations(
        previous.get("destinations", routes), routes, label + ".destinations"
    )
    if stop or cli._wizard_backtrack_requested(destinations):
        return destinations, stop
    rule = {"sources": sources, "destinations": destinations, "protocol": "all", "ports": []}
    if mode == "all":
        return rule, False
    print_comment("Protocol: TCP, UDP or ICMP (ping).")
    protocol, stop = cli._prompt_choice_override(
        path_label=label + ".protocol",
        current=previous.get("protocol", "tcp"),
        choices=[OptionChoice(v, v.upper()) for v in ("tcp", "udp", "icmp")],
        required=True,
    )
    if stop or cli._wizard_backtrack_requested(protocol):
        return protocol, stop
    rule["protocol"] = protocol
    if protocol != "icmp":
        print_comment("Ports: destination ports/ranges, e.g. 443, 6443 or 8000-8080.")
        ports, stop = cli._prompt_scalar_override(
            label + ".ports",
            previous.get("ports") or ["443"],
            type_hint="list(string)",
            required=True,
        )
        if stop or cli._wizard_backtrack_requested(ports):
            return ports, stop
        rule["ports"] = ports
    return rule, False


def prompt_rules(current: Any, row: dict[str, Any], label: str) -> tuple[Any, bool]:
    from . import cli
    from .tailscale_config import access_rules

    saved = current if isinstance(current, list) else []
    rules: list[dict[str, Any]] = []
    routes = networks(row["tailscale"]["routes"])
    print_comment(POLICY_NOTE)
    while True:
        index = len(rules)
        previous = saved[index] if index < len(saved) else {}
        while True:
            rule, stop = _prompt_rule(previous, routes, f"{label}[{index}]")
            if stop or cli._wizard_backtrack_requested(rule):
                return rule, stop
            previous = rule
            try:
                access_rules([rule], routes)
            except ValueError as error:
                cli.console.print(str(error), markup=False)
                continue
            traffic = (
                "all protocols and ports"
                if rule["protocol"] == "all"
                else "ICMP"
                if rule["protocol"] == "icmp"
                else rule["protocol"].upper() + " ports " + ", ".join(rule["ports"])
            )
            cli.console.print(
                "Requested access: "
                + ", ".join(rule["sources"])
                + " → "
                + ", ".join(rule["destinations"])
                + " — "
                + traffic,
                markup=False,
            )
            confirmed, stop = cli._prompt_scalar_override(
                f"{label}[{index}].accept_rule", True, type_hint="bool", required=True
            )
            if stop or cli._wizard_backtrack_requested(confirmed):
                return confirmed, stop
            if confirmed:
                break
        rules.append(rule)
        if len(rules) == 32:
            return rules, False
        more, stop = cli._prompt_scalar_override(
            label + ".add_another", index + 1 < len(saved), type_hint="bool", required=True
        )
        if stop or cli._wizard_backtrack_requested(more):
            return more, stop
        if not more:
            return rules, False


def prompt_routes(
    current: Any, row: Any, payload: Any, lookup: Any, label: str
) -> tuple[Any, bool]:
    from . import cli
    from .wireguard_subnets import live_subnet_choices, selected_network_id

    saved = networks(current) if current else []
    print_comment(
        "Select private subnets to advertise. Newly discovered subnets are not preselected."
    )
    choices = []
    try:
        project = row["inputs"]["parent_id"]
        network = selected_network_id(row, payload, lookup)
        if network:
            choices = live_subnet_choices(project, network)
    except Exception:
        cli.console.print(
            "Subnet discovery is unavailable. Enter known CIDRs explicitly; deploy will verify their VPC membership."
        )
    if not choices:
        return cli._prompt_scalar_override(label, saved, type_hint="list(string)", required=True)
    listed = {choice.value for choice in choices}
    choices.extend(
        OptionChoice(value, value + " (saved)") for value in saved if value not in listed
    )
    choices = [OptionChoice(c.value, c.label, recommended=c.value in saved) for c in choices]
    choices.append(OptionChoice("__manual__", "Enter destination CIDRs manually"))
    while True:
        selected, stop = cli._prompt_multi_choice_override(
            path_label=label, current=[], choices=choices, type_hint="list(string)", required=False
        )
        if stop or cli._wizard_backtrack_requested(selected):
            return selected, stop
        if not isinstance(selected, list):
            raise ValueError("Destination selection must return a list of CIDRs")
        if "__manual__" in selected:
            selected.remove("__manual__")
            manual, stop = cli._prompt_scalar_override(
                label, [], type_hint="list(string)", required=True
            )
            if stop or cli._wizard_backtrack_requested(manual):
                return manual, stop
            if not isinstance(manual, list):
                raise ValueError("Manual destinations must be a list of CIDRs")
            selected.extend(manual)
        try:
            return networks(list(dict.fromkeys(selected))), False
        except ValueError:
            cli.console.print("Choose at least one canonical private IPv4 destination prefix.")
