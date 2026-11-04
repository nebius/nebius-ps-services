"""Focused prompts for telemetry, bucket metadata and VPN defaults."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

import typer
from rich.markup import escape

from .observability_routing import (
    SIGNALS,
    app_row,
    ensure_routing_apps,
    resolve_settings,
    save_settings,
    target_settings,
)
from .provider_options import OptionChoice


def prompt_bucket_labels(current: object) -> tuple[object, bool]:
    from . import cli

    labels = dict(current) if isinstance(current, Mapping) else {}
    cli.console.print("Optional metadata for organizing this bucket, such as environment=dev.")
    if labels:
        cli.console.print(
            escape("Bucket labels: " + ", ".join(f"{k}={v}" for k, v in labels.items()))
        )
        action, stop = cli._prompt_choice_override(
            path_label="Bucket labels",
            current="keep",
            choices=[
                OptionChoice("keep", "Keep labels"),
                OptionChoice("edit", "Edit labels"),
                OptionChoice("clear", "Clear labels"),
            ],
            required=True,
        )
        if stop or cli._wizard_backtrack_requested(action):
            return action, stop
        if action == "keep":
            return labels, False
        if action == "clear":
            return {}, False
    else:
        add, stop = cli._prompt_scalar_override("Add labels to this bucket?", False)
        if stop or cli._wizard_backtrack_requested(add):
            return add, stop
        if not add:
            return {}, False
    while True:
        # Read strings directly: values such as 001, false and null are metadata,
        # not YAML scalars. Nothing is committed until the editor finishes.
        try:
            key = typer.prompt(
                cli._wizard_field_prompt_suffix("Label name (blank finishes)"), default=""
            ).strip()
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return current, True
        if key == cli.WIZARD_ABORT_TOKEN:
            return current, True
        if key == cli.WIZARD_EXIT_TOKEN:
            return cli._WIZARD_BACKTRACK, False
        if not key:
            return labels, False
        if key in labels:
            replace, stop = cli._prompt_scalar_override(f'Replace label "{key}"?', False)
            if stop or cli._wizard_backtrack_requested(replace):
                return replace, stop
            if not replace:
                continue
        try:
            value = typer.prompt(
                cli._wizard_field_prompt_suffix(f'Value for "{key}"'),
                default=str(labels.get(key, "")),
            )
        except (KeyboardInterrupt, EOFError, typer.Abort):
            return current, True
        if value == cli.WIZARD_ABORT_TOKEN:
            return current, True
        if value == cli.WIZARD_EXIT_TOKEN:
            return cli._WIZARD_BACKTRACK, False
        labels[key] = value


def prompt_wireguard_input(
    field: str, current: object, inputs: Mapping[str, Any], instance: str
) -> tuple[object, bool]:
    from . import cli

    titles = {
        "create_public_ip_allocation": "Create a new public IP for this VPN gateway?",
        "client_default_persistent_keepalive": "Client keepalive interval in seconds",
        "nat_mode": "Use NAT for VPN access to private resources?",
    }
    guidance = {
        "create_public_ip_allocation": "Choose false to reuse an existing public IP allocation; its ID is required.",
        "client_default_persistent_keepalive": "Keeps client NAT/firewall mappings active. Set 0 to disable keepalive.",
        "nat_mode": "NAT lets VPN clients reach private resources without return routes to the VPN client subnet. With NAT disabled, configure those return routes.",
    }
    if field == "public_ip_allocation_name":
        name = str(inputs.get("name") or instance) + "-public-ip"
        cli.console.print(escape(f"Automatic public IP allocation name: {name}"))
        if current:
            action, stop = cli._prompt_choice_override(
                path_label="Public IP allocation name",
                current="keep",
                choices=[
                    OptionChoice("keep", f"Keep {current}"),
                    OptionChoice("change", "Change override"),
                    OptionChoice("automatic", f"Use automatic name: {name}"),
                ],
                required=True,
            )
            if stop or cli._wizard_backtrack_requested(action):
                return action, stop
            if action == "keep":
                return current, False
            if action == "automatic":
                return None, False
        value, stop = cli._prompt_scalar_override(
            "Public IP allocation name override",
            current or "",
            type_hint="string",
            unset_on_skip=not bool(current),
            blank_text="blank uses the automatic name",
        )
        return value, stop
    cli.console.print(guidance[field])
    while True:
        value, stop = cli._prompt_scalar_override(titles[field], current)
        if stop or cli._wizard_backtrack_requested(value):
            return value, stop
        if field != "client_default_persistent_keepalive":
            return value, False
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and 0 <= value <= 65535
            and value == int(value)
        ):
            return int(value), False
        cli.console.print("Keepalive must be a whole number between 0 and 65535 seconds.")


def retained_wizard_app_rows(
    rows: list[dict[str, Any]],
    removed: set[tuple[str, str]],
    originals: Mapping[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Undo session activation; remove only rows that did not exist beforehand."""
    from .component_instances import component_instance_id, component_type_id

    result = []
    for row in rows:
        identity = (component_type_id(row), component_instance_id(row))
        if identity not in removed:
            result.append(row)
        elif identity in originals:
            result.append(copy.deepcopy(originals[identity]))
    return result


def prompt_telemetry_mode(*, target: str, enabled: bool, local: bool) -> tuple[object, bool]:
    from . import cli

    cli.console.print(
        escape(
            f"Telemetry for {target}: the default sends metrics, logs and traces to Nebius. A collector runs in the cluster; local storage and Grafana are optional."
        )
    )
    mode, stop = cli._prompt_choice_override(
        path_label="Observability",
        current="disabled" if not enabled else ("local" if local else "managed"),
        choices=[
            OptionChoice("managed", "Nebius-managed observability (default)"),
            OptionChoice("local", "In-cluster observability"),
            OptionChoice("disabled", "Disabled"),
        ],
        required=True,
    )
    if stop or cli._wizard_backtrack_requested(mode):
        return mode, stop
    return {"enabled": mode != "disabled", "local": mode == "local"}, False


def remote_telemetry_candidate(payload: dict[str, Any], target: str) -> dict[str, Any]:
    candidate = copy.deepcopy(payload)
    from .observability_routing import endpoint_defaults

    # This named preset is explicit intent to use Nebius, not a generic remote mode.
    saved = copy.deepcopy(target_settings(candidate, target))
    defaults = endpoint_defaults(candidate)
    for signal in SIGNALS:
        remote = {"url": defaults[signal]["write"], "auth": "nebius"}
        if signal != "metrics":
            remote["protocol"] = "otlp-grpc"
        saved.setdefault(signal, {})["remote"] = remote
    reserved = {f"{signal}-remote" for signal in SIGNALS}
    saved["datasources"] = [
        item for item in saved.get("datasources", []) if item["name"] not in reserved
    ]
    default = saved.get("default_datasource")
    if default in {f"{signal}-local" for signal in SIGNALS} and not any(
        item["name"] == default for item in saved["datasources"]
    ):
        saved.pop("default_datasource", None)
        saved["default_datasource_explicit"] = False
    save_settings(candidate, target, saved)
    settings = resolve_settings(
        candidate,
        target,
        overrides={f"{signal}_storage": "remote" for signal in SIGNALS},
        require_read_connections=False,
    )
    from .observability_intent import select_collectors

    select_collectors(candidate, target, settings)
    save_settings(candidate, target, settings)
    ensure_routing_apps(candidate, observability_target_refs=frozenset({target}))
    return candidate


def print_remote_telemetry_summary(payload: dict[str, Any], target: str) -> None:
    from . import cli
    from .observability_intent import effective_write_destinations, trace_endpoint

    for signal, destinations in effective_write_destinations(payload, target).items():
        for destination in destinations:
            cli.console.print(
                escape(f"{signal.capitalize()} → {destination['url']} ({destination['protocol']})")
            )
    agent = app_row(payload, target, "nebius-observability-agent")
    from .observability_routing import effective_signals

    if agent and "traces" in effective_signals(payload, target):
        cli.console.print(
            escape(
                f"Application traces: send OTLP/gRPC to {trace_endpoint()}. Applications must be instrumented to emit traces."
            )
        )
