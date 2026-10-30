"""Explicit destination selection from the customer's resolved Nebius VPC."""

from __future__ import annotations

from typing import Any

from .provider_options import OptionChoice
from .tailscale_config import networks


def prompt_auth(
    current: Any, row: dict[str, Any], payload: dict[str, Any], label: str
) -> tuple[Any, bool]:
    """Select custody now; administrator credential values arrive in the handoff."""
    from . import cli

    auth = dict(current) if isinstance(current, dict) else {}
    choice, stop = cli._prompt_choice_override(
        path_label=label + ".storage",
        current="",
        choices=[
            OptionChoice(
                "new", "Create a linked SecretStash container (recommended)", recommended=True
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
        return result, False
    project = row.get("inputs", {}).get("parent_id") or payload.get("client_info", {}).get(
        "nebius", {}
    ).get("project_id")
    ref: dict[str, Any] = {"project_id": project}
    if choice == "existing":
        identity, stop = cli._prompt_scalar_override(
            label + ".secret_id",
            auth.get("secretstash", {}).get("secret_id", ""),
            type_hint="string",
            required=True,
        )
        if stop or cli._wizard_backtrack_requested(identity):
            return identity, stop
        ref["secret_id"] = identity
    else:
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


def prompt_rules(current: Any, row: dict[str, Any], label: str) -> tuple[Any, bool]:
    from . import cli
    from .tailscale_config import access_rules

    saved = current if isinstance(current, list) else []
    rules: list[dict[str, Any]] = []
    cli.console.print(
        "Specify each allowed source, destination, protocol and port rule. No blanket grant is added."
    )
    while True:
        index = len(rules)
        previous = saved[index] if index < len(saved) else {}
        rule: dict[str, Any] = {}
        for field, hint, default in (
            ("sources", "list(string)", []),
            ("destinations", "list(string)", []),
            ("protocol", "string", "tcp"),
            ("ports", "list(string)", ["443"]),
        ):
            if field == "ports" and rule["protocol"] == "icmp":
                rule[field] = []
                continue
            value, stop = cli._prompt_scalar_override(
                f"{label}[{index}].{field}",
                previous.get(field, default),
                type_hint=hint,
                required=True,
            )
            if stop or cli._wizard_backtrack_requested(value):
                return value, stop
            rule[field] = value
        try:
            access_rules([rule], networks(row["tailscale"]["routes"]))
        except ValueError as error:
            cli.console.print(str(error), markup=False)
            continue
        rules.append(rule)
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
    cli.console.print(
        "Select private prefixes to advertise to your tailnet. New discoveries are not selected automatically."
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
