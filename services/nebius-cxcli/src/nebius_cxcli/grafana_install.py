"""Shared Grafana installation intent for the command and Apps wizard."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import questionary
import typer

from .observability_routing import (
    SIGNALS,
    app_row,
    connections,
    describe_target,
    enable_app,
    ensure_routing_apps,
    native_defaults_scope,
    resolve_settings,
    save_settings,
    target_settings,
)


def configure(
    payload: dict[str, Any],
    target: str,
    *,
    overrides: Mapping[str, Any] | None = None,
    datasources: Sequence[tuple[str, str, str]] = (),
    interactive: bool = False,
    preserve_existing_access: bool = False,
) -> dict[str, Any]:
    """Return a candidate; cancellation never mutates the caller's payload."""
    candidate = copy.deepcopy(payload)
    flags = dict(overrides or {})
    # Resolve once before prompts, validating target identity and explicit flags.
    settings = resolve_settings(
        candidate,
        target,
        overrides=flags,
        datasources=datasources,
        require_read_connections=not interactive,
    )
    settings["signals"] = list(SIGNALS)
    with native_defaults_scope(typer.echo if interactive else None):
        describe_target(payload, target)
        if interactive:
            from .grafana_install_wizard import configure_routing

            settings = configure_routing(
                payload,
                target,
                settings=settings,
                flags=flags,
                locked_names={item[0] for item in datasources},
                build_candidate=lambda selected: _configured_candidate(
                    payload, target, selected, preserve_existing_access=preserve_existing_access
                ),
            )
        return _configured_candidate(
            payload, target, settings, preserve_existing_access=preserve_existing_access
        )


def _configured_candidate(
    payload: dict[str, Any],
    target: str,
    settings: dict[str, Any],
    *,
    preserve_existing_access: bool,
) -> dict[str, Any]:
    """Materialize selected routing on a copy, shared by preview and final setup."""
    candidate = copy.deepcopy(payload)
    settings = copy.deepcopy(settings)
    settings["signals"] = list(SIGNALS)
    # Existing collector ownership requires an explicit cutover, never a duplicate.
    if (
        not target_settings(payload, target)
        and app_row(payload, target, "nebius-observability-agent")
        and any(settings[signal]["storage"] != "remote" for signal in SIGNALS)
    ):
        raise ValueError(
            "Existing Nebius collector requires an explicit pipeline cutover before local storage can be enabled"
        )
    if "grafana_access" not in settings:
        settings["grafana_access"] = "existing" if preserve_existing_access else "private"
    save_settings(candidate, target, settings)
    scope = frozenset({target})
    ensure_routing_apps(candidate, observability_target_refs=scope)
    from .components import component_entries
    from .observability import ensure_observability_app_rows

    if not app_row(candidate, target, "grafana"):
        entry = next(entry for entry in component_entries("apps") if entry.id == "grafana")
        enable_app(candidate, target, entry)
    ensure_observability_app_rows(candidate, observability_target_refs=scope)
    connections(candidate, target)
    return candidate


def initialize_selected_grafana(
    payload: dict[str, Any], *, existing_targets: frozenset[str] = frozenset()
) -> bool:
    """Initialize new headless Apps selections through the same resolver."""
    from .deploy_targets import enabled_cluster_target_refs

    targets = [
        target
        for target in enabled_cluster_target_refs(payload)
        if target not in existing_targets and app_row(payload, target, "grafana")
    ]
    for target in targets:
        candidate = configure(payload, target)
        payload.clear()
        payload.update(candidate)
    return bool(targets)


def summary(payload: dict[str, Any], target: str) -> list[str]:
    settings = target_settings(payload, target)
    lines = [f"Grafana installation on {target}"]
    lines.extend(f"  {signal}: {settings[signal]['storage']}" for signal in SIGNALS)
    lines.append(f"  Pushgateway: {'enabled' if settings['pushgateway'] else 'disabled'}")
    lines.extend(
        f"  datasource {item['name']} ({item['type']}): {item['url']} [UID {item['uid']}]"
        + (" [default]" if item["isDefault"] else "")
        for item in connections(payload, target)
    )
    gateway = app_row(payload, target, "opentelemetry-collector")
    if gateway:
        host = f"{gateway['release-name']}.{gateway['namespace']}.svc"
        lines.append(f"  OTLP ingestion: http://{host}:4317 (gRPC), http://{host}:4318 (HTTP)")
    if settings["pushgateway"]:
        from .observability_backends import pushgateway_address

        lines.append(f"  Results publication: http://{pushgateway_address(payload, target)}")
        lines.append(
            "  Dashboard imports use --datasource-map SOURCE=UID with the metrics UID above."
        )
    return lines


def install(
    config: Path,
    target: str,
    *,
    overrides: Mapping[str, Any],
    datasources: Sequence[tuple[str, str, str]],
    interactive: bool,
    emit: Callable[[str], None],
) -> None:
    from .config_loader import load_config
    from .config_model import to_dynamic_payload

    expected = config.read_bytes()
    # Config loading, qualification, saving and rendering all resolve native
    # defaults. Reuse source-only data for this invocation; full chart admission
    # and generation-bound deployment retain their independent authority.
    with native_defaults_scope(emit if interactive else None):
        payload = to_dynamic_payload(
            load_config(config, observability_target_refs=frozenset({target}))
        )
        candidate = configure(
            payload,
            target,
            overrides=overrides,
            datasources=datasources,
            interactive=interactive,
            preserve_existing_access=app_row(payload, target, "grafana") is not None,
        )
        for line in summary(candidate, target):
            emit(line)
        if interactive:
            emit(
                "After confirmation, Cxcli saves these settings, replaces generated artifacts, "
                "and deploys all pending project changes with the normal deployment approvals."
            )
        if (
            interactive
            and not questionary.confirm(
                "Save these settings, render, and deploy the current project?", default=True
            ).ask()
        ):
            raise typer.Abort()
        from .observability_installation import install_observability

        install_observability(config, target, candidate, expected_bytes=expected, emit=emit)
